#!/usr/bin/env python3
"""
Raccoglitore giornaliero CardTrader per Magic: The Gathering.

Cosa fa a ogni esecuzione:
  1. aggiorna l'elenco delle espansioni Magic e (una volta a settimana) dei blueprint,
     cioè le singole stampe, con il loro scryfall_id;
  2. per ogni espansione scarica le inserzioni in vendita (non foil e foil separatamente);
  3. confronta con l'esecuzione precedente e stima le copie vendute;
  4. salva una riga di statistiche per stampa e per giorno nel database SQLite.

Limite importante dell'API: per ogni stampa restituisce solo le 25 inserzioni più economiche.
Le statistiche riguardano quindi la "parte bassa" del mercato, che è comunque quella
dove avviene la maggior parte delle vendite.

Uso:
  export CARDTRADER_TOKEN="il_tuo_token"     (Windows: set CARDTRADER_TOKEN=...)
  python ct_collector.py --test-token        verifica il token
  python ct_collector.py --limit 5           prova su 5 espansioni
  python ct_collector.py                     esecuzione completa (circa 30-40 minuti)
"""

import argparse
import gzip
import json
import os
import sqlite3
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_URL = "https://api.cardtrader.com/api/v2"
MAGIC_GAME_ID = 1
TOP_N = 25                    # l'API restituisce al massimo 25 inserzioni per stampa
MARKETPLACE_DELAY = 1.1       # limite API: 1 chiamata al secondo sul marketplace
BLUEPRINT_REFRESH_DAYS = 7
SCRIPT_DIR = Path(__file__).resolve().parent
NM_CONDITIONS = {"Mint", "Near Mint"}


# --------------------------------------------------------------------------- API

class CardTraderClient:
    def __init__(self, token, timeout=60):
        import requests  # importato qui così i test non lo richiedono
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        self.timeout = timeout

    def get(self, path, params=None, retries=5):
        url = f"{BASE_URL}{path}"
        wait = 5
        for attempt in range(1, retries + 1):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
            except Exception as e:  # errore di rete
                if attempt == retries:
                    raise
                log(f"  errore di rete ({e}), riprovo tra {wait}s")
                time.sleep(wait)
                wait *= 2
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401:
                raise SystemExit("Token non valido (401). Controlla CARDTRADER_TOKEN.")
            if r.status_code == 404:
                return None  # es. dati non pronti per una espansione
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                log(f"  HTTP {r.status_code}, riprovo tra {wait}s")
                time.sleep(wait)
                wait *= 2
                continue
            raise RuntimeError(f"HTTP {r.status_code} su {path} {params}: {r.text[:200]}")


# ------------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS expansions (
    id INTEGER PRIMARY KEY,
    code TEXT,
    name TEXT,
    first_seen TEXT,
    blueprints_synced_at TEXT
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT
);
CREATE TABLE IF NOT EXISTS blueprints (
    id INTEGER PRIMARY KEY,
    name TEXT,
    version TEXT,
    expansion_id INTEGER,
    category_id INTEGER,
    scryfall_id TEXT,
    tcg_player_id TEXT,
    cardmarket_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_bp_name ON blueprints(name);
CREATE INDEX IF NOT EXISTS idx_bp_scryfall ON blueprints(scryfall_id);

-- Stato dell'ultima lettura: serve solo per il confronto con il giorno dopo.
CREATE TABLE IF NOT EXISTS listings_current (
    product_id INTEGER,
    foil INTEGER,
    expansion_id INTEGER,
    blueprint_id INTEGER,
    price_cents INTEGER,
    quantity INTEGER,
    seen_date TEXT,
    PRIMARY KEY (product_id, foil)
);
CREATE INDEX IF NOT EXISTS idx_lc_exp ON listings_current(expansion_id, foil);

-- Una riga per stampa, per finitura (foil / non foil), per giorno.
CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT,
    blueprint_id INTEGER,
    foil INTEGER,
    currency TEXT,
    n_listings INTEGER,       -- inserzioni acquistabili tra le 25 più economiche
    total_qty INTEGER,        -- copie totali in quelle inserzioni
    min_price REAL,           -- prezzo minimo, qualsiasi condizione e lingua
    min_price_nm REAL,        -- minimo Near Mint / Mint
    min_price_nm_en REAL,     -- minimo Near Mint inglese
    median_price REAL,        -- mediana delle 25 più economiche
    ct0_min_price REAL,       -- minimo acquistabile via CardTrader Zero
    sold_qty_est INTEGER,     -- copie scalate da inserzioni ancora presenti (vendite quasi certe)
    removed_qty_est INTEGER,  -- copie da inserzioni sparite (vendute o ritirate)
    new_qty INTEGER,          -- copie apparse (nuove inserzioni o rifornimenti)
    prev_date TEXT,           -- data della lettura con cui è stato fatto il confronto
    PRIMARY KEY (date, blueprint_id, foil)
);
CREATE INDEX IF NOT EXISTS idx_ds_bp ON daily_stats(blueprint_id, foil, date);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    run_date TEXT,
    expansions_ok INTEGER,
    expansions_failed INTEGER,
    rows_written INTEGER
);
"""


def open_db(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


# ------------------------------------------------------------- sincronizzazioni

def sync_categories(client, con):
    cats = client.get("/categories", {"game_id": MAGIC_GAME_ID}) or []
    con.executemany(
        "INSERT OR REPLACE INTO categories(id, name) VALUES (?, ?)",
        [(c["id"], c["name"]) for c in cats],
    )
    con.commit()


def sync_expansions(client, con, today):
    exps = client.get("/expansions") or []
    magic = [e for e in exps if e.get("game_id") == MAGIC_GAME_ID]
    for e in magic:
        con.execute(
            """INSERT INTO expansions(id, code, name, first_seen) VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET code=excluded.code, name=excluded.name""",
            (e["id"], e.get("code"), e.get("name"), today),
        )
    con.commit()
    return magic


def sync_blueprints(client, con, expansion_id, today):
    bps = client.get("/blueprints/export", {"expansion_id": expansion_id}) or []
    rows = []
    for b in bps:
        cm = b.get("card_market_ids") or []
        rows.append((
            b["id"], b.get("name"), b.get("version"), b.get("expansion_id", expansion_id),
            b.get("category_id"), b.get("scryfall_id"), b.get("tcg_player_id"),
            cm[0] if cm else None,
        ))
    con.executemany(
        """INSERT OR REPLACE INTO blueprints
           (id, name, version, expansion_id, category_id, scryfall_id, tcg_player_id, cardmarket_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    con.execute("UPDATE expansions SET blueprints_synced_at=? WHERE id=?", (today, expansion_id))
    con.commit()
    return len(rows)


def blueprints_due(con, expansion_id, today):
    row = con.execute(
        "SELECT blueprints_synced_at FROM expansions WHERE id=?", (expansion_id,)
    ).fetchone()
    if not row or not row[0]:
        return True
    last = datetime.strptime(row[0], "%Y-%m-%d").date()
    return (datetime.strptime(today, "%Y-%m-%d").date() - last).days >= BLUEPRINT_REFRESH_DAYS


# ----------------------------------------------------------- calcoli giornalieri

def parse_listing(p):
    props = p.get("properties_hash") or {}
    user = p.get("user") or {}
    price = p.get("price") or {}
    return {
        "product_id": p["id"],
        "blueprint_id": p["blueprint_id"],
        "price_cents": price.get("cents"),
        "currency": price.get("currency"),
        "quantity": (p.get("quantity") or 0) * (p.get("bundle_size") or 1),
        "condition": props.get("condition"),
        "language": props.get("mtg_language"),
        "on_vacation": bool(p.get("on_vacation")),
        "graded": bool(p.get("graded")),
        "ct0": bool(user.get("can_sell_via_hub")),
    }


def compute_stats(listings):
    """Statistiche di prezzo sulle inserzioni acquistabili (no vacanza, no gradate)."""
    buyable = [l for l in listings
               if not l["on_vacation"] and not l["graded"] and l["price_cents"] is not None]
    if not buyable:
        return None
    prices = [l["price_cents"] / 100 for l in buyable]
    nm = [l["price_cents"] / 100 for l in buyable if l["condition"] in NM_CONDITIONS]
    nm_en = [l["price_cents"] / 100 for l in buyable
             if l["condition"] in NM_CONDITIONS and l["language"] == "en"]
    ct0 = [l["price_cents"] / 100 for l in buyable if l["ct0"]]
    return {
        "currency": buyable[0]["currency"],
        "n_listings": len(buyable),
        "total_qty": sum(l["quantity"] for l in buyable),
        "min_price": min(prices),
        "min_price_nm": min(nm) if nm else None,
        "min_price_nm_en": min(nm_en) if nm_en else None,
        "median_price": round(statistics.median(prices), 2),
        "ct0_min_price": min(ct0) if ct0 else None,
    }


def compute_flows(prev, curr):
    """
    Confronta le inserzioni di ieri (prev) e di oggi (curr) di UNA stampa.
    prev, curr: dict product_id -> (price_cents, quantity)

    - Inserzione presente in entrambi con quantità scesa  -> vendita quasi certa.
    - Inserzione sparita:
        * se oggi la lista è piena (25) e l'inserzione costava più della 25esima,
          potrebbe solo essere "scivolata fuori" dalla classifica: la ignoriamo;
        * altrimenti è stata venduta o ritirata.
    - Inserzione nuova o quantità salita -> nuova offerta.
    """
    sold = removed = new = 0
    full = len(curr) >= TOP_N
    cutoff = max((p for p, _ in curr.values()), default=None) if full else None
    for pid, (price, qty) in prev.items():
        if pid in curr:
            delta = qty - curr[pid][1]
            if delta > 0:
                sold += delta
            elif delta < 0:
                new += -delta
        else:
            if full and cutoff is not None and price is not None and price > cutoff:
                continue
            removed += qty
    for pid, (_, qty) in curr.items():
        if pid not in prev:
            new += qty
    return sold, removed, new


def process_expansion(client, con, expansion_id, foil, today, raw_sink=None):
    data = client.get("/marketplace/products",
                      {"expansion_id": expansion_id, "foil": "true" if foil else "false"})
    if data is None:
        return 0
    if raw_sink is not None:
        raw_sink.append({"expansion_id": expansion_id, "foil": foil, "data": data})

    # oggi
    curr_by_bp = {}
    for bp_id, products in data.items():
        parsed = [parse_listing(p) for p in products]
        # sicurezza: il filtro foil dovrebbe già farlo, ma escludiamo eventuali incoerenze
        curr_by_bp[int(bp_id)] = parsed

    # ieri (o ultima lettura riuscita)
    prev_rows = con.execute(
        """SELECT product_id, blueprint_id, price_cents, quantity, seen_date
           FROM listings_current WHERE expansion_id=? AND foil=?""",
        (expansion_id, int(foil)),
    ).fetchall()
    prev_by_bp, prev_date = {}, None
    for pid, bp, price, qty, seen in prev_rows:
        prev_by_bp.setdefault(bp, {})[pid] = (price, qty)
        prev_date = seen
    has_history = bool(prev_rows) and prev_date != today

    rows = []
    for bp in set(curr_by_bp) | set(prev_by_bp):
        listings = curr_by_bp.get(bp, [])
        stats = compute_stats(listings)
        if has_history:
            curr_map = {l["product_id"]: (l["price_cents"], l["quantity"]) for l in listings}
            sold, removed, new = compute_flows(prev_by_bp.get(bp, {}), curr_map)
        else:
            sold = removed = new = None
        if stats is None and not sold and not removed:
            continue
        s = stats or {k: None for k in ("currency", "n_listings", "total_qty", "min_price",
                                         "min_price_nm", "min_price_nm_en", "median_price",
                                         "ct0_min_price")}
        if stats is None:
            s["n_listings"], s["total_qty"] = 0, 0
        rows.append((
            today, bp, int(foil), s["currency"], s["n_listings"], s["total_qty"],
            s["min_price"], s["min_price_nm"], s["min_price_nm_en"], s["median_price"],
            s["ct0_min_price"], sold, removed, new, prev_date if has_history else None,
        ))

    # Se lo script viene rilanciato nello stesso giorno, i prezzi si aggiornano
    # ma le stime di vendita già calcolate rispetto al giorno prima restano.
    con.executemany(
        """INSERT INTO daily_stats
           (date, blueprint_id, foil, currency, n_listings, total_qty, min_price, min_price_nm,
            min_price_nm_en, median_price, ct0_min_price, sold_qty_est, removed_qty_est,
            new_qty, prev_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(date, blueprint_id, foil) DO UPDATE SET
             currency=excluded.currency, n_listings=excluded.n_listings,
             total_qty=excluded.total_qty, min_price=excluded.min_price,
             min_price_nm=excluded.min_price_nm, min_price_nm_en=excluded.min_price_nm_en,
             median_price=excluded.median_price, ct0_min_price=excluded.ct0_min_price,
             sold_qty_est=COALESCE(excluded.sold_qty_est, daily_stats.sold_qty_est),
             removed_qty_est=COALESCE(excluded.removed_qty_est, daily_stats.removed_qty_est),
             new_qty=COALESCE(excluded.new_qty, daily_stats.new_qty),
             prev_date=COALESCE(excluded.prev_date, daily_stats.prev_date)""",
        rows,
    )
    # sostituisce lo stato corrente di questa espansione/finitura
    con.execute("DELETE FROM listings_current WHERE expansion_id=? AND foil=?",
                (expansion_id, int(foil)))
    con.executemany(
        """INSERT OR REPLACE INTO listings_current
           (product_id, foil, expansion_id, blueprint_id, price_cents, quantity, seen_date)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(l["product_id"], int(foil), expansion_id, l["blueprint_id"], l["price_cents"],
          l["quantity"], today)
         for ls in curr_by_bp.values() for l in ls],
    )
    con.commit()
    return len(rows)


# ------------------------------------------------------------------------ main

def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_token(args):
    token = os.environ.get("CARDTRADER_TOKEN")
    if not token:
        f = Path(args.token_file)
        if f.exists():
            token = f.read_text().strip()
    if not token:
        raise SystemExit(
            "Token mancante. Impostalo con la variabile CARDTRADER_TOKEN "
            f"oppure salvalo nel file {args.token_file}"
        )
    return token


def run(client, con, args, today=None, sleep=time.sleep):
    today = today or date.today().isoformat()
    started = datetime.now().isoformat(timespec="seconds")

    sync_categories(client, con)
    expansions = sync_expansions(client, con, today)
    if args.only:
        wanted = {c.strip().lower() for c in args.only.split(",")}
        expansions = [e for e in expansions if (e.get("code") or "").lower() in wanted]
    if args.limit:
        expansions = expansions[: args.limit]
    log(f"Espansioni da elaborare: {len(expansions)}")

    raw_sink = [] if args.save_raw else None
    ok = failed = written = 0
    for i, e in enumerate(expansions, 1):
        try:
            if blueprints_due(con, e["id"], today):
                sync_blueprints(client, con, e["id"], today)
            for foil in (False, True):
                written += process_expansion(client, con, e["id"], foil, today, raw_sink)
                sleep(MARKETPLACE_DELAY)
            ok += 1
        except SystemExit:
            raise
        except Exception as ex:
            failed += 1
            log(f"  ERRORE su {e.get('code')} ({e['id']}): {ex}")
        if i % 50 == 0:
            log(f"  {i}/{len(expansions)} espansioni, {written} righe scritte")

    if raw_sink is not None:
        raw_dir = Path(args.raw_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(raw_dir / f"{today}.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(raw_sink, fh)

    con.execute(
        """INSERT INTO runs(started_at, finished_at, run_date, expansions_ok,
                            expansions_failed, rows_written) VALUES (?, ?, ?, ?, ?, ?)""",
        (started, datetime.now().isoformat(timespec="seconds"), today, ok, failed, written),
    )
    con.commit()
    log(f"Fatto: {ok} espansioni ok, {failed} fallite, {written} righe scritte.")
    return ok, failed, written


def main():
    ap = argparse.ArgumentParser(description="Raccoglitore giornaliero CardTrader (Magic)")
    ap.add_argument("--db", default=str(SCRIPT_DIR / "cardtrader.db"), help="file database")
    ap.add_argument("--token-file", default=str(SCRIPT_DIR / "cardtrader_token.txt"))
    ap.add_argument("--limit", type=int, help="elabora solo le prime N espansioni (per prova)")
    ap.add_argument("--only", help="solo questi codici espansione, es. dmu,one,mh3")
    ap.add_argument("--save-raw", action="store_true",
                    help="salva anche le risposte complete compresse (occupa ~30-60 MB al giorno)")
    ap.add_argument("--raw-dir", default=str(SCRIPT_DIR / "raw"))
    ap.add_argument("--test-token", action="store_true", help="verifica il token ed esce")
    args = ap.parse_args()

    client = CardTraderClient(load_token(args))
    if args.test_token:
        info = client.get("/info")
        print(f"Token valido. App: {info}")
        return
    con = open_db(args.db)
    try:
        run(client, con, args)
    finally:
        con.close()


if __name__ == "__main__":
    main()
