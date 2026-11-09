#!/usr/bin/env python3
"""
Consulta i dati raccolti da ct_collector.py.

Esempi:
  python analisi.py carta "Sheoldred, the Apocalypse"
  python analisi.py carta "Sheoldred, the Apocalypse" --foil --giorni 60
  python analisi.py movimenti --giorni 7 --min-prezzo 5
  python analisi.py stato
  python analisi.py panoramica
"""

import argparse
import sqlite3
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def fmt(v, suffix=""):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}{suffix}"
    return f"{v}{suffix}"


def table(headers, rows):
    rows = [[fmt(c) for c in r] for r in rows]
    widths = [max(len(str(h)), *(len(r[i]) for r in rows)) if rows else len(str(h))
              for i, h in enumerate(headers)]
    line = "  ".join(str(h).ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


SINGLE = ("Magic Single Card",)
SEALED = ("Magic Boosters", "Magic Booster Boxes", "Magic Bundles and Fat Packs",
          "Magic Complete Sets", "Magic Tournament Prerelease Packs", "Magic Cases & Displays")


def category_filter(args, alias="b"):
    """Condizione SQL sulle categorie: carte singole di default."""
    if getattr(args, "tutte", False):
        return "1=1", ()
    names = SEALED if getattr(args, "sigillati", False) else SINGLE
    marks = ",".join("?" * len(names))
    return (f"{alias}.category_id IN (SELECT id FROM categories WHERE name IN ({marks}))",
            names)


def cmd_carta(con, args):
    cond, cparams = category_filter(args)
    prints = con.execute(
        f"""SELECT b.id, b.name, b.version, e.name, e.code
           FROM blueprints b LEFT JOIN expansions e ON e.id = b.expansion_id
           WHERE b.name LIKE ? AND {cond} ORDER BY e.name""",
        (f"%{args.nome}%", *cparams),
    ).fetchall()
    if not prints:
        print("Nessuna carta trovata con quel nome.")
        return
    foil = int(args.foil)
    print(f"Stampe trovate: {len(prints)} ({'foil' if foil else 'non foil'})\n")
    summary = []
    for bp_id, name, version, exp_name, code in prints:
        last = con.execute(
            """SELECT date, min_price_nm, median_price, total_qty FROM daily_stats
               WHERE blueprint_id=? AND foil=? ORDER BY date DESC LIMIT 1""",
            (bp_id, foil),
        ).fetchone()
        sold = con.execute(
            """SELECT SUM(sold_qty_est), SUM(removed_qty_est) FROM daily_stats
               WHERE blueprint_id=? AND foil=? AND date >= date('now', ?)""",
            (bp_id, foil, f"-{args.giorni} days"),
        ).fetchone()
        label = f"{name}{' (' + version + ')' if version else ''}"
        summary.append([bp_id, label[:40], (code or "").upper(), *(last[1:] if last else
                        (None, None, None)), sold[0], sold[1]])
    table(["id", "carta", "set", "min NM €", "mediana €", "copie", "vendute", "uscite"],
          summary)
    print(f"\n'vendute' e 'uscite' sono la somma degli ultimi {args.giorni} giorni.")

    if len(prints) == 1 or args.id:
        bp_id = args.id or prints[0][0]
        hist = con.execute(
            """SELECT date, min_price, min_price_nm, min_price_nm_en, median_price,
                      total_qty, sold_qty_est, removed_qty_est, new_qty
               FROM daily_stats WHERE blueprint_id=? AND foil=?
               AND date >= date('now', ?) ORDER BY date""",
            (bp_id, foil, f"-{args.giorni} days"),
        ).fetchall()
        print(f"\nStorico della stampa {bp_id}:")
        table(["data", "min", "min NM", "min NM EN", "mediana", "copie",
               "vendute", "uscite", "nuove"], hist)
    else:
        print("Per lo storico giornaliero di una stampa aggiungi --id <numero>.")


def cmd_movimenti(con, args):
    """Stampe con più vendite stimate nel periodo, con variazione del prezzo NM."""
    foil = int(args.foil)
    cond, cparams = category_filter(args)
    rows = con.execute(
        f"""
        WITH periodo AS (
            SELECT blueprint_id,
                   SUM(COALESCE(sold_qty_est, 0)) AS vendute,
                   SUM(COALESCE(removed_qty_est, 0)) AS uscite,
                   SUM(COALESCE(new_qty, 0)) AS nuove,
                   MIN(date) AS d0, MAX(date) AS d1
            FROM daily_stats
            WHERE foil = ? AND date >= date('now', ?)
            GROUP BY blueprint_id
        )
        SELECT b.name, e.code, p.vendute, p.uscite, p.nuove,
               s0.min_price_nm AS prezzo_inizio, s1.min_price_nm AS prezzo_fine,
               s1.total_qty
        FROM periodo p
        JOIN daily_stats s0 ON s0.blueprint_id = p.blueprint_id AND s0.foil = ? AND s0.date = p.d0
        JOIN daily_stats s1 ON s1.blueprint_id = p.blueprint_id AND s1.foil = ? AND s1.date = p.d1
        JOIN blueprints b ON b.id = p.blueprint_id
        LEFT JOIN expansions e ON e.id = b.expansion_id
        WHERE s1.min_price_nm >= ? AND {cond}
        ORDER BY p.vendute DESC
        LIMIT ?
        """,
        (foil, f"-{args.giorni} days", foil, foil, args.min_prezzo, *cparams, args.top),
    ).fetchall()
    out = []
    for name, code, vend, usc, nuove, p0, p1, qty in rows:
        var = ((p1 - p0) / p0 * 100) if p0 and p1 else None
        out.append([name[:38], (code or "").upper(), vend, usc, nuove, p0, p1,
                    None if var is None else round(var, 1), qty])
    print(f"Top {args.top} per vendite stimate, ultimi {args.giorni} giorni, "
          f"prezzo NM >= {args.min_prezzo} €\n")
    table(["carta", "set", "vendute", "uscite", "nuove", "€ inizio", "€ fine",
           "var %", "copie ora"], out)


def cmd_panoramica(con, args):
    """Quadro d'insieme: catalogo, copertura dell'ultima raccolta, fasce di prezzo, top carte."""
    last = con.execute("SELECT MAX(date) FROM daily_stats").fetchone()[0]
    if not last:
        print("Nessuna raccolta nel database.")
        return
    print(f"Ultima raccolta: {last}\n")

    print("Catalogo e copertura per categoria")
    rows = con.execute(
        """SELECT k.name, COUNT(*) AS catalogo,
                  SUM(EXISTS(SELECT 1 FROM daily_stats d WHERE d.blueprint_id=b.id
                             AND d.date=? AND d.foil=0 AND d.n_listings>0)) AS non_foil,
                  SUM(EXISTS(SELECT 1 FROM daily_stats d WHERE d.blueprint_id=b.id
                             AND d.date=? AND d.foil=1 AND d.n_listings>0)) AS foil
           FROM blueprints b LEFT JOIN categories k ON k.id=b.category_id
           GROUP BY k.name ORDER BY catalogo DESC""",
        (last, last),
    ).fetchall()
    table(["categoria", "in catalogo", "in vendita non foil", "in vendita foil"], rows)

    single, _ = category_filter(SimpleArgs(), "b")
    print(f"\nCarte singole non foil per fascia di prezzo (minimo Near Mint, {last})")
    bands = con.execute(
        f"""SELECT CASE
                 WHEN d.min_price_nm IS NULL THEN 'nessuna copia NM'
                 WHEN d.min_price_nm < 1 THEN 'a) meno di 1 euro'
                 WHEN d.min_price_nm < 5 THEN 'b) 1 - 5 euro'
                 WHEN d.min_price_nm < 20 THEN 'c) 5 - 20 euro'
                 WHEN d.min_price_nm < 100 THEN 'd) 20 - 100 euro'
                 ELSE 'e) oltre 100 euro' END AS fascia,
               COUNT(*)
           FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
           WHERE d.date=? AND d.foil=0 AND {single}
           GROUP BY fascia ORDER BY fascia""",
        (last, *SINGLE),
    ).fetchall()
    table(["fascia", "stampe"], bands)

    print(f"\nEspansioni con più carte singole in vendita (non foil)")
    exps = con.execute(
        f"""SELECT e.name, UPPER(e.code), COUNT(*),
                   ROUND(SUM(d.min_price_nm), 0)
            FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
            JOIN expansions e ON e.id=b.expansion_id
            WHERE d.date=? AND d.foil=0 AND {single}
            GROUP BY e.id ORDER BY COUNT(*) DESC LIMIT ?""",
        (last, *SINGLE, args.top),
    ).fetchall()
    table(["espansione", "set", "stampe in vendita", "somma minimi NM €"], exps)

    print(f"\nCarte singole non foil più care (minimo Near Mint)")
    top = con.execute(
        f"""SELECT b.name, b.version, UPPER(e.code), d.min_price_nm, d.total_qty
            FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
            LEFT JOIN expansions e ON e.id=b.expansion_id
            WHERE d.date=? AND d.foil=0 AND {single} AND d.min_price_nm IS NOT NULL
            ORDER BY d.min_price_nm DESC LIMIT ?""",
        (last, *SINGLE, args.top),
    ).fetchall()
    table(["carta", "versione", "set", "min NM €", "copie"],
          [[n[:34], (v or "")[:18], c, p, q] for n, v, c, p, q in top])


class SimpleArgs:
    """Argomenti vuoti: category_filter restituisce il filtro delle carte singole."""
    tutte = False
    sigillati = False


def cmd_stato(con, _args):
    runs = con.execute(
        """SELECT run_date, started_at, finished_at, expansions_ok, expansions_failed,
                  rows_written FROM runs ORDER BY id DESC LIMIT 10"""
    ).fetchall()
    days = con.execute("SELECT COUNT(DISTINCT date) FROM daily_stats").fetchone()[0]
    n_bp = con.execute("SELECT COUNT(*) FROM blueprints").fetchone()[0]
    print(f"Giorni raccolti: {days}   Stampe in anagrafica: {n_bp}\n")
    table(["giorno", "inizio", "fine", "ok", "fallite", "righe"], runs)


def main():
    ap = argparse.ArgumentParser(description="Analisi dei dati CardTrader raccolti")
    ap.add_argument("--db", default=str(SCRIPT_DIR / "cardtrader.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("carta", help="stampe e storico di una carta")
    c.add_argument("nome")
    c.add_argument("--id", type=int, help="id della stampa per lo storico giornaliero")
    c.add_argument("--foil", action="store_true")
    c.add_argument("--giorni", type=int, default=30)
    c.add_argument("--sigillati", action="store_true", help="cerca tra i prodotti sigillati")
    c.add_argument("--tutte", action="store_true", help="tutte le categorie")

    m = sub.add_parser("movimenti", help="carte con più vendite stimate")
    m.add_argument("--giorni", type=int, default=7)
    m.add_argument("--min-prezzo", type=float, default=5.0)
    m.add_argument("--top", type=int, default=30)
    m.add_argument("--foil", action="store_true")
    m.add_argument("--sigillati", action="store_true", help="classifica dei prodotti sigillati")
    m.add_argument("--tutte", action="store_true", help="tutte le categorie")

    sub.add_parser("stato", help="stato delle ultime raccolte")

    pa = sub.add_parser("panoramica", help="quadro d'insieme dell'ultima raccolta")
    pa.add_argument("--top", type=int, default=20)

    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    {"carta": cmd_carta, "movimenti": cmd_movimenti, "stato": cmd_stato,
     "panoramica": cmd_panoramica}[args.cmd](con, args)


if __name__ == "__main__":
    main()
