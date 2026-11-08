#!/usr/bin/env python3
"""
Consulta i dati raccolti da ct_collector.py.

Esempi:
  python analisi.py carta "Sheoldred, the Apocalypse"
  python analisi.py carta "Sheoldred, the Apocalypse" --foil --giorni 60
  python analisi.py movimenti --giorni 7 --min-prezzo 5
  python analisi.py stato
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


def cmd_carta(con, args):
    prints = con.execute(
        """SELECT b.id, b.name, b.version, e.name, e.code
           FROM blueprints b LEFT JOIN expansions e ON e.id = b.expansion_id
           WHERE b.name LIKE ? ORDER BY e.name""",
        (f"%{args.nome}%",),
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
    rows = con.execute(
        """
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
        WHERE s1.min_price_nm >= ?
        ORDER BY p.vendute DESC
        LIMIT ?
        """,
        (foil, f"-{args.giorni} days", foil, foil, args.min_prezzo, args.top),
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

    m = sub.add_parser("movimenti", help="carte con più vendite stimate")
    m.add_argument("--giorni", type=int, default=7)
    m.add_argument("--min-prezzo", type=float, default=5.0)
    m.add_argument("--top", type=int, default=30)
    m.add_argument("--foil", action="store_true")

    sub.add_parser("stato", help="stato delle ultime raccolte")

    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    {"carta": cmd_carta, "movimenti": cmd_movimenti, "stato": cmd_stato}[args.cmd](con, args)


if __name__ == "__main__":
    main()
