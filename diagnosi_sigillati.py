#!/usr/bin/env python3
"""
Diagnosi: perché i prodotti sigillati risultano senza inserzioni?

Su un'espansione con prodotti sigillati nel catalogo, chiede le inserzioni all'API in tre modi
(senza filtro foil, foil=false, foil=true) e per un singolo prodotto sigillato, poi confronta
quanti sigillati tornano in ciascun caso. Fa 4 chiamate in tutto. Serve il database già creato
da ct_collector.py (per il catalogo) e il token.

Uso:
  python diagnosi_sigillati.py              sceglie da solo l'espansione
  python diagnosi_sigillati.py --code dmu   usa un'espansione precisa
"""

import argparse
import sqlite3
import time
from collections import Counter
from pathlib import Path

from ct_collector import CardTraderClient, MARKETPLACE_DELAY, TRACKED_CATEGORIES, load_token

SCRIPT_DIR = Path(__file__).resolve().parent
SEALED = sorted(TRACKED_CATEGORIES - {"Magic Single Card"})


def candidates(con):
    marks = ",".join("?" * len(SEALED))
    return con.execute(
        f"""SELECT e.id, e.code, e.name, COUNT(*) AS n
            FROM blueprints b JOIN categories k ON k.id = b.category_id
            JOIN expansions e ON e.id = b.expansion_id
            WHERE k.name IN ({marks})
            GROUP BY e.id ORDER BY n DESC LIMIT 5""", SEALED).fetchall()


def describe(con, label, data, sealed_ids):
    data = data or {}
    bps = {int(k) for k in data}
    found = bps & sealed_ids
    listings = sum(len(v) for k, v in data.items() if int(k) in found)
    print(f"\n[{label}] stampe restituite: {len(bps)}, di cui sigillati: {len(found)} "
          f"({listings} inserzioni)")
    if found:
        cats = Counter(con.execute(
            "SELECT k.name FROM blueprints b JOIN categories k ON k.id=b.category_id "
            "WHERE b.id=?", (bp,)).fetchone()[0] for bp in found)
        print("  per categoria:", dict(cats))
        bp = sorted(found)[0]
        sample = data.get(str(bp)) or data.get(bp)
        p = sample[0]
        print(f"  esempio, stampa {bp}: prezzo {p.get('price')}, quantità {p.get('quantity')}")
        print(f"  proprietà dell'inserzione: {p.get('properties_hash')}")
    return found


def main():
    ap = argparse.ArgumentParser(description="Diagnosi delle inserzioni dei prodotti sigillati")
    ap.add_argument("--db", default=str(SCRIPT_DIR / "cardtrader.db"))
    ap.add_argument("--token-file", default=str(SCRIPT_DIR / "cardtrader_token.txt"))
    ap.add_argument("--code", help="codice dell'espansione da provare")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cands = candidates(con)
    print("Espansioni con più prodotti sigillati nel catalogo:")
    for _, code, name, n in cands:
        print(f"  {code}: {name} ({n})")
    if args.code:
        row = con.execute("SELECT id, code, name FROM expansions WHERE lower(code)=lower(?)",
                          (args.code,)).fetchone()
        if not row:
            raise SystemExit(f"Espansione {args.code} non trovata nel database.")
        exp_id, code, name = row
    elif cands:
        exp_id, code, name, _ = cands[0]
    else:
        raise SystemExit("Nessun prodotto sigillato nel catalogo.")

    marks = ",".join("?" * len(SEALED))
    sealed = con.execute(
        f"""SELECT b.id, b.name, k.name FROM blueprints b JOIN categories k ON k.id=b.category_id
            WHERE b.expansion_id=? AND k.name IN ({marks})""", (exp_id, *SEALED)).fetchall()
    sealed_ids = {r[0] for r in sealed}
    print(f"\nProvo l'espansione {code} ({name}): {len(sealed_ids)} prodotti sigillati nel catalogo,"
          f" es. {[r[1] for r in sealed[:3]]}")

    client = CardTraderClient(load_token(args))
    results = {}
    for label, params in [("senza filtro foil", {"expansion_id": exp_id}),
                          ("foil=false", {"expansion_id": exp_id, "foil": "false"}),
                          ("foil=true", {"expansion_id": exp_id, "foil": "true"})]:
        results[label] = describe(con, label, client.get("/marketplace/products", params),
                                  sealed_ids)
        time.sleep(MARKETPLACE_DELAY)

    if sealed:
        bp_id, bp_name, _ = sealed[0]
        data = client.get("/marketplace/products", {"blueprint_id": bp_id})
        describe(con, f"solo il prodotto {bp_id} '{bp_name}'", data, sealed_ids)

    print("\nConclusione:")
    if results["senza filtro foil"] - results["foil=false"] - results["foil=true"]:
        print("  I sigillati arrivano SOLO senza filtro foil: è il filtro a escluderli.")
    elif not any(results.values()):
        print("  Nessun sigillato in nessuna delle tre richieste per espansione: il problema non "
              "è il filtro foil (guarda la richiesta per singolo prodotto qui sopra).")
    else:
        print("  I sigillati arrivano anche con il filtro foil: il problema è altrove. "
              "Incolla questo output in chat.")


if __name__ == "__main__":
    main()
