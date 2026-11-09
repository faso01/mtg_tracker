#!/usr/bin/env python3
"""
Scarica da Scryfall i prezzi di riferimento e i dati di ogni stampa Magic.

Da lanciare una volta al giorno, dopo ct_collector.py (esegui_raccolta.bat lo fa già).

Cosa salva nel database:
  - scryfall_daily: per ogni stampa e giorno, prezzo Cardmarket in euro (normale e foil),
    prezzo TCGplayer in dollari e posizione nella classifica di popolarità EDHREC (Commander);
  - scryfall_cards: dati che cambiano di rado (nome, set, data di uscita, rarità,
    Reserved List, ristampa, legalità nei formati principali).

Il file di Scryfall ("Default Cards") pesa circa mezzo gigabyte: viene letto un pezzo alla volta,
senza caricarlo tutto in memoria. Scryfall aggiorna i prezzi circa una volta al giorno.

Uso:
  python scryfall_sync.py
  python scryfall_sync.py --file default-cards.json     (usa un file già scaricato)
"""

import argparse
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

BULK_LIST_URL = "https://api.scryfall.com/bulk-data"
BULK_TYPE = "default_cards"
HEADERS = {"User-Agent": "mtg_tracker/1.0", "Accept": "application/json"}
SCRIPT_DIR = Path(__file__).resolve().parent
BATCH = 5000
FORMATS = ("commander", "modern", "pioneer", "standard", "legacy", "pauper")

SCHEMA = """
CREATE TABLE IF NOT EXISTS scryfall_daily (
    date TEXT,
    scryfall_id TEXT,
    eur REAL,             -- prezzo di riferimento Cardmarket, non foil
    eur_foil REAL,        -- prezzo di riferimento Cardmarket, foil
    usd REAL,             -- prezzo TCGplayer, non foil
    usd_foil REAL,        -- prezzo TCGplayer, foil
    edhrec_rank INTEGER,  -- posizione nella popolarità Commander (1 = la più giocata)
    PRIMARY KEY (date, scryfall_id)
);
CREATE INDEX IF NOT EXISTS idx_sd_id ON scryfall_daily(scryfall_id, date);

CREATE TABLE IF NOT EXISTS scryfall_cards (
    scryfall_id TEXT PRIMARY KEY,
    oracle_id TEXT,       -- uguale per tutte le ristampe della stessa carta
    name TEXT,
    set_code TEXT,
    collector_number TEXT,
    released_at TEXT,
    rarity TEXT,
    reserved INTEGER,     -- 1 = Reserved List: non verrà mai ristampata
    reprint INTEGER,      -- 1 = questa stampa è una ristampa
    legal_commander TEXT,
    legal_modern TEXT,
    legal_pioneer TEXT,
    legal_standard TEXT,
    legal_legacy TEXT,
    legal_pauper TEXT,
    updated TEXT
);
CREATE INDEX IF NOT EXISTS idx_sc_oracle ON scryfall_cards(oracle_id);

CREATE TABLE IF NOT EXISTS scryfall_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT,
    started_at TEXT,
    finished_at TEXT,
    bulk_updated_at TEXT,
    cards INTEGER
);
"""


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def card_rows(card, today):
    """Trasforma una carta di Scryfall nelle due righe da salvare. None se non serve."""
    if card.get("digital") or "paper" not in (card.get("games") or ["paper"]):
        return None  # carte solo digitali (Arena, MTGO): nessun mercato fisico
    sid = card.get("id")
    if not sid:
        return None
    prices = card.get("prices") or {}
    legal = card.get("legalities") or {}
    daily = (today, sid, to_float(prices.get("eur")), to_float(prices.get("eur_foil")),
             to_float(prices.get("usd")), to_float(prices.get("usd_foil")),
             card.get("edhrec_rank"))
    meta = (sid, card.get("oracle_id"), card.get("name"), card.get("set"),
            card.get("collector_number"), card.get("released_at"), card.get("rarity"),
            int(bool(card.get("reserved"))), int(bool(card.get("reprint"))),
            *(legal.get(f) for f in FORMATS), today)
    return daily, meta


def save(con, daily, meta):
    con.executemany(
        """INSERT OR REPLACE INTO scryfall_daily
           (date, scryfall_id, eur, eur_foil, usd, usd_foil, edhrec_rank)
           VALUES (?, ?, ?, ?, ?, ?, ?)""", daily)
    con.executemany(
        """INSERT OR REPLACE INTO scryfall_cards
           (scryfall_id, oracle_id, name, set_code, collector_number, released_at, rarity,
            reserved, reprint, legal_commander, legal_modern, legal_pioneer, legal_standard,
            legal_legacy, legal_pauper, updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", meta)
    con.commit()


def import_cards(con, cards, today):
    """cards: iteratore di dizionari carta. Restituisce quante carte sono state salvate."""
    daily, meta, n = [], [], 0
    for card in cards:
        rows = card_rows(card, today)
        if rows is None:
            continue
        daily.append(rows[0])
        meta.append(rows[1])
        n += 1
        if len(daily) >= BATCH:
            save(con, daily, meta)
            daily, meta = [], []
            if n % 50000 == 0:
                log(f"  {n} carte importate")
    if daily:
        save(con, daily, meta)
    return n


def open_stream(retries=4):
    """Chiede a Scryfall l'indirizzo del file del giorno e lo apre in streaming."""
    import requests
    wait = 10
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(BULK_LIST_URL, headers=HEADERS, timeout=60)
            resp.raise_for_status()
            listing = resp.json()
            items = [x for x in listing.get("data", []) if x.get("type") == BULK_TYPE]
            if not items or not items[0].get("download_uri"):
                raise RuntimeError(f"risposta inattesa da Scryfall: {str(listing)[:400]}")
            info = items[0]
            log(f"  file del {info.get('updated_at')}, {info.get('size', 0) // 1_000_000} MB")
            r = requests.get(info["download_uri"], headers=HEADERS, stream=True, timeout=120)
            r.raise_for_status()
            r.raw.decode_content = True  # decomprime al volo se arriva compresso
            return r, info.get("updated_at")
        except Exception as e:
            if attempt == retries:
                raise
            log(f"  errore ({e}), riprovo tra {wait}s")
            time.sleep(wait)
            wait *= 2


def main():
    ap = argparse.ArgumentParser(description="Prezzi e dati di riferimento da Scryfall")
    ap.add_argument("--db", default=str(SCRIPT_DIR / "cardtrader.db"))
    ap.add_argument("--file", help="file Default Cards già scaricato (per prove)")
    args = ap.parse_args()

    import ijson

    today = date.today().isoformat()
    started = datetime.now().isoformat(timespec="seconds")
    con = sqlite3.connect(args.db)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)

    if args.file:
        log(f"Leggo il file {args.file}")
        with open(args.file, "rb") as fh:
            n = import_cards(con, ijson.items(fh, "item"), today)
        bulk_updated = None
    else:
        log("Scarico Default Cards da Scryfall")
        resp, bulk_updated = open_stream()
        with resp:
            n = import_cards(con, ijson.items(resp.raw, "item"), today)

    con.execute(
        """INSERT INTO scryfall_runs(run_date, started_at, finished_at, bulk_updated_at, cards)
           VALUES (?, ?, ?, ?, ?)""",
        (today, started, datetime.now().isoformat(timespec="seconds"), bulk_updated, n))
    con.commit()
    con.close()
    log(f"Fatto: {n} carte importate (file Scryfall aggiornato il {bulk_updated or '-'}).")
    if n == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
