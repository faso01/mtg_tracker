#!/usr/bin/env python3
"""
Archivio dei dati nel repository privato su GitHub (release), usato da GitHub Actions e dal PC.

Come sono organizzati i dati nel repository privato:
  - release "stato":            stato.db.gz, il database senza lo storico (catalogo, fotografia
                                delle inserzioni dell'ultimo giorno, registro delle esecuzioni);
                                serve alla raccolta successiva per il confronto con ieri.
                                catalogo.db.gz, lo stesso senza la fotografia: è quello che
                                scarica il PC per ricostruire il database delle analisi.
  - release "storico-AAAA-MM":  un file compresso per giorno e per tabella, che non cambia più:
                                daily_stats_AAAA-MM-GG.csv.gz, scryfall_daily_AAAA-MM-GG.csv.gz

Comandi:
  python archivio.py scarica-stato               (Actions) scarica lo stato prima della raccolta
  python archivio.py pubblica                    carica stato e storico dell'ultimo giorno
  python archivio.py pubblica --tutte-le-date    carica tutto lo storico del database (primo invio dal PC)
  python archivio.py aggiorna                    (PC) scarica i file nuovi e ricostruisce il database completo

Serve GitHub CLI (gh), già presente su GitHub Actions. Sul PC: winget install GitHub.cli, poi gh auth login.
"""

import argparse
import csv
import gzip
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REPO = os.environ.get("DATI_REPO", "faso01/mtg_tracker_dati")
STATE_TAG = "stato"
STATE_FILE = "stato.db.gz"          # per la raccolta: comprende la fotografia delle inserzioni
CATALOG_FILE = "catalogo.db.gz"     # per il PC: come lo stato ma senza la fotografia, molto più leggero
HISTORY_TABLES = ("daily_stats", "scryfall_daily")   # tabelle dello storico, un file per giorno


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ------------------------------------------------------------------ GitHub CLI

def gh(*args, check=True):
    """Esegue un comando gh e restituisce l'output. Sostituibile nei test."""
    res = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)}: {res.stderr.strip() or res.stdout.strip()}")
    return res


def ensure_release(repo, tag, title, notes):
    if gh("release", "view", tag, "--repo", repo, check=False).returncode != 0:
        gh("release", "create", tag, "--repo", repo, "--title", title, "--notes", notes)
        log(f"  creata la release {tag}")


def upload(repo, tag, paths):
    for p in paths:
        gh("release", "upload", tag, str(p), "--repo", repo, "--clobber")
        log(f"  caricato {Path(p).name} in {tag} ({Path(p).stat().st_size / 1e6:.1f} MB)")


def list_tags(repo):
    out = gh("release", "list", "--repo", repo, "--limit", "1000", "--json", "tagName").stdout
    return [r["tagName"] for r in json.loads(out or "[]")]


def list_assets(repo, tag):
    out = gh("release", "view", tag, "--repo", repo, "--json", "assets").stdout
    return [a["name"] for a in json.loads(out or "{}").get("assets", [])]


def download(repo, tag, dest, pattern=None, clobber=False):
    args = ["release", "download", tag, "--repo", repo, "--dir", str(dest)]
    if pattern:
        args += ["--pattern", pattern]
    args += ["--clobber"] if clobber else ["--skip-existing"]
    gh(*args)


# ------------------------------------------------------------ esporta / importa

def table_columns(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def has_table(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (table,)).fetchone() is not None


def dates_in(con, table):
    if not has_table(con, table):
        return []
    return [r[0] for r in con.execute(f"SELECT DISTINCT date FROM {table} ORDER BY date")]


def export_day(con, table, day, out_dir):
    """Scrive le righe di un giorno di una tabella in <tabella>_<giorno>.csv.gz."""
    cols = table_columns(con, table)
    path = Path(out_dir) / f"{table}_{day}.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for row in con.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE date=?", (day,)):
            w.writerow(["" if v is None else v for v in row])
    return path


def import_file(con, table, path):
    """Importa un file giornaliero nella tabella (le righe già presenti vengono sostituite)."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        r = csv.reader(fh)
        cols = next(r)
        known = set(table_columns(con, table))
        idx = [i for i, c in enumerate(cols) if c in known]   # tollera colonne aggiunte in futuro
        names = [cols[i] for i in idx]
        sql = (f"INSERT OR REPLACE INTO {table} ({', '.join(names)}) "
               f"VALUES ({', '.join('?' * len(names))})")
        rows = ([None if row[i] == "" else row[i] for i in idx] for row in r)
        con.executemany(sql, rows)
    return path


def make_state(db_path, out_path, drop_tables=()):
    """Copia del database con lo storico ridotto all'ultimo giorno, compressa.
    L'ultimo giorno resta perché un rilancio nello stesso giorno conservi le vendite stimate.
    drop_tables: tabelle da svuotare (per il catalogo: la fotografia delle inserzioni)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / "stato.db"
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp_db)
        src.backup(dst)          # copia coerente anche se il database è in modalità WAL
        src.close()
        for table in HISTORY_TABLES:
            if has_table(dst, table):
                last = dst.execute(f"SELECT MAX(date) FROM {table}").fetchone()[0]
                dst.execute(f"DELETE FROM {table} WHERE date < ?", (last,))
        for table in drop_tables:
            if has_table(dst, table):
                dst.execute(f"DELETE FROM {table}")
        dst.commit()
        dst.execute("PRAGMA journal_mode=DELETE")
        dst.execute("VACUUM")
        dst.close()
        with open(tmp_db, "rb") as fi, gzip.open(out_path, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo)
    return out_path


# -------------------------------------------------------------------- comandi

def cmd_scarica_stato(args):
    db = Path(args.db)
    if db.exists():
        raise SystemExit(f"{db} esiste già: per sicurezza non lo sovrascrivo.")
    if STATE_TAG not in list_tags(args.repo):
        log("Nessuno stato nel repository dei dati: la raccolta parte da zero.")
        return
    with tempfile.TemporaryDirectory() as tmp:
        download(args.repo, STATE_TAG, tmp, pattern=STATE_FILE, clobber=True)
        with gzip.open(Path(tmp) / STATE_FILE, "rb") as fi, open(db, "wb") as fo:
            shutil.copyfileobj(fi, fo)
    con = sqlite3.connect(db)
    last = con.execute("SELECT MAX(run_date) FROM runs").fetchone()[0] \
        if has_table(con, "runs") else None
    con.close()
    log(f"Stato scaricato ({db.stat().st_size / 1e6:.0f} MB), ultima raccolta: {last}")


def cmd_pubblica(args):
    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"Database {db} non trovato.")
    con = sqlite3.connect(db)
    files = []
    with tempfile.TemporaryDirectory() as tmp:
        for table in HISTORY_TABLES:
            days = dates_in(con, table)
            if not days:
                continue
            for day in (days if args.tutte_le_date else days[-1:]):
                files.append(export_day(con, table, day, tmp))
        con.close()
        if not files:
            raise SystemExit("Nessun dato da pubblicare.")

        by_month = {}
        for f in files:
            day = f.name.rsplit("_", 1)[1].split(".")[0]      # AAAA-MM-GG
            by_month.setdefault(day[:7], []).append(f)
        for month, month_files in sorted(by_month.items()):
            tag = f"storico-{month}"
            ensure_release(args.repo, tag, f"Storico {month}",
                           "Un file compresso per giorno: daily_stats (CardTrader) e "
                           "scryfall_daily (Scryfall).")
            upload(args.repo, tag, sorted(month_files))

        state = make_state(db, Path(tmp) / STATE_FILE)
        catalog = make_state(db, Path(tmp) / CATALOG_FILE, drop_tables=("listings_current",))
        ensure_release(args.repo, STATE_TAG, "Stato della raccolta",
                       "stato.db.gz: database senza storico, con la fotografia delle inserzioni "
                       "dell'ultimo giorno (per la raccolta). catalogo.db.gz: lo stesso senza "
                       "fotografia (per il PC). Sostituiti a ogni raccolta.")
        upload(args.repo, STATE_TAG, [catalog, state])
    log(f"Pubblicati {len(files)} file di storico e lo stato.")


def cmd_aggiorna(args):
    """Scarica i file nuovi e ricostruisce il database completo per le analisi."""
    data_dir = Path(args.dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    tags = list_tags(args.repo)
    if STATE_TAG not in tags:
        raise SystemExit("Nel repository dei dati non c'è ancora nessuno stato.")
    base = CATALOG_FILE if CATALOG_FILE in list_assets(args.repo, STATE_TAG) else STATE_FILE
    log(f"Scarico {base}")
    download(args.repo, STATE_TAG, data_dir, pattern=base, clobber=True)
    for tag in sorted(t for t in tags if t.startswith("storico-")):
        log(f"Scarico i file nuovi di {tag}")
        download(args.repo, tag, data_dir / tag)

    db = Path(args.db)
    if db.exists():
        backup = db.with_name(db.stem + "_precedente.db")
        shutil.copyfile(db, backup)
        log(f"Copia di sicurezza del database attuale: {backup.name}")
    tmp_db = db.with_name(db.stem + "_nuovo.db")
    with gzip.open(data_dir / base, "rb") as fi, open(tmp_db, "wb") as fo:
        shutil.copyfileobj(fi, fo)
    con = sqlite3.connect(tmp_db)
    n = 0
    for table in HISTORY_TABLES:
        if not has_table(con, table):
            continue
        for path in sorted(data_dir.glob(f"storico-*/{table}_*.csv.gz")):
            import_file(con, table, path)
            n += 1
    # indici per interrogazioni veloci anche con mesi di storico
    if has_table(con, "daily_stats"):
        con.execute("CREATE INDEX IF NOT EXISTS idx_ds_date ON daily_stats(date, foil)")
    if has_table(con, "scryfall_daily"):
        con.execute("CREATE INDEX IF NOT EXISTS idx_sd_date ON scryfall_daily(date)")
    con.commit()
    days = dates_in(con, "daily_stats")
    con.close()
    for suffix in ("-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    os.replace(tmp_db, db)
    log(f"Database ricostruito: {n} file importati, {len(days)} giorni di storico "
        f"({days[0] if days else '-'} - {days[-1] if days else '-'}).")


def main():
    ap = argparse.ArgumentParser(description="Archivio dei dati nel repository privato")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="repository dei dati (utente/nome)")
    ap.add_argument("--db", default=str(SCRIPT_DIR / "cardtrader.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scarica-stato", help="(Actions) scarica lo stato prima della raccolta")
    p = sub.add_parser("pubblica", help="carica lo stato e lo storico")
    p.add_argument("--tutte-le-date", action="store_true",
                   help="carica tutti i giorni presenti nel database, non solo l'ultimo")
    a = sub.add_parser("aggiorna", help="(PC) scarica i dati e ricostruisce il database")
    a.add_argument("--dir", default=str(SCRIPT_DIR / "dati"),
                   help="cartella dove tenere i file scaricati")
    args = ap.parse_args()
    {"scarica-stato": cmd_scarica_stato, "pubblica": cmd_pubblica,
     "aggiorna": cmd_aggiorna}[args.cmd](args)


if __name__ == "__main__":
    main()
