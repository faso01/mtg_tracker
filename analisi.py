#!/usr/bin/env python3
"""
Consulta i dati raccolti da ct_collector.py.

Esempi:
  python analisi.py carta "Sheoldred, the Apocalypse"
  python analisi.py carta "Sheoldred, the Apocalypse" --foil --giorni 60
  python analisi.py movimenti --giorni 7 --min-prezzo 5
  python analisi.py stato
  python analisi.py panoramica
  python analisi.py anomalie
"""

import argparse
import sqlite3
from pathlib import Path

from ct_collector import NO_FINISH_CATEGORIES
from scryfall_sync import SCHEMA as SCRYFALL_SCHEMA

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOGLIA = 5.0      # anomalo se il minimo CardTrader supera di 5 volte il riferimento...
MIN_SCARTO = 20.0         # ...e di almeno 20 euro (evita falsi allarmi su carte da pochi centesimi)


def scryfall_date(con):
    """Ultimo giorno con prezzi Scryfall, o None se scryfall_sync.py non è mai stato lanciato."""
    return con.execute("SELECT MAX(date) FROM scryfall_daily").fetchone()[0]


MIN_RIF = 0.05            # riferimenti Cardmarket sotto i 5 centesimi = valore di comodo, ignorati


def prepare_reference(con):
    """Tabella temporanea ref_name: per ogni nome di carta, il prezzo di riferimento più alto
    tra tutte le sue stampe (normale o foil). Serve da tetto quando la stampa non ha un
    riferimento proprio. Le carte a doppia faccia sono indicizzate anche con la sola prima faccia."""
    con.execute("DROP TABLE IF EXISTS temp.ref_name")
    con.execute("CREATE TEMP TABLE ref_name (name TEXT PRIMARY KEY, ref_max REAL)")
    sdate = scryfall_date(con)
    if not sdate:
        return
    con.execute(
        """INSERT INTO ref_name
           SELECT name, MAX(p) FROM (
               SELECT c.name AS name, MAX(COALESCE(s.eur, 0), COALESCE(s.eur_foil, 0)) AS p
               FROM scryfall_cards c JOIN scryfall_daily s
                    ON s.scryfall_id = c.scryfall_id AND s.date = ?
               UNION ALL
               SELECT substr(c.name, 1, instr(c.name, ' // ') - 1),
                      MAX(COALESCE(s.eur, 0), COALESCE(s.eur_foil, 0))
               FROM scryfall_cards c JOIN scryfall_daily s
                    ON s.scryfall_id = c.scryfall_id AND s.date = ?
               WHERE instr(c.name, ' // ') > 0)
           WHERE p > ? GROUP BY name""",
        (sdate, sdate, MIN_RIF))


def ref_join(b="b", sf="sf", rn="rn"):
    """JOIN con i prezzi Scryfall di un giorno (parametro: la data) e con il tetto per nome."""
    return (f"LEFT JOIN scryfall_daily {sf} ON {sf}.scryfall_id = {b}.scryfall_id "
            f"AND {sf}.date = ? LEFT JOIN temp.ref_name {rn} ON {rn}.name = {b}.name")


def ref_expr(d="d", sf="sf"):
    """Prezzo di riferimento Cardmarket della stampa, nella stessa finitura (None se inaffidabile)."""
    raw = f"(CASE WHEN {d}.foil = 1 THEN {sf}.eur_foil ELSE {sf}.eur END)"
    return f"(CASE WHEN {raw} > {MIN_RIF} THEN {raw} END)"


def eff_ref_expr(d="d", sf="sf", rn="rn"):
    """Riferimento usato per le anomalie: quello della stampa, altrimenti il tetto della carta."""
    return f"COALESCE({ref_expr(d, sf)}, {rn}.ref_max)"


def anomalo_expr(soglia, d="d", sf="sf", rn="rn"):
    """Condizione SQL: il minimo CardTrader è un prezzo segnaposto / fuori mercato."""
    r, p = eff_ref_expr(d, sf, rn), f"COALESCE({d}.min_price_nm, {d}.min_price)"
    return (f"({r} IS NOT NULL AND {p} > {float(soglia)} * {r} "
            f"AND {p} - {r} > {MIN_SCARTO})")


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


def finishes_for(args):
    """Finiture da considerare: i sigillati comprendono quelli senza finitura (foil = 2)."""
    base = 1 if getattr(args, "foil", False) else 0
    if getattr(args, "sigillati", False) or getattr(args, "tutte", False):
        return (base, 2)
    return (base,)


def cmd_carta(con, args):
    cond, cparams = category_filter(args)
    prints = con.execute(
        f"""SELECT b.id, b.name, b.version, e.name, e.code, k.name
           FROM blueprints b LEFT JOIN expansions e ON e.id = b.expansion_id
           LEFT JOIN categories k ON k.id = b.category_id
           WHERE b.name LIKE ? AND {cond} ORDER BY e.name""",
        (f"%{args.nome}%", *cparams),
    ).fetchall()
    if not prints:
        print("Nessuna carta trovata con quel nome.")
        return
    foil = int(args.foil)
    print(f"Stampe trovate: {len(prints)} ({'foil' if foil else 'non foil'}; "
          f"i prodotti sigillati senza finitura sono mostrati comunque)\n")
    finish_of = {}
    summary = []
    for bp_id, name, version, exp_name, code, cat in prints:
        f = 2 if cat in NO_FINISH_CATEGORIES else foil
        finish_of[bp_id] = f
        last = con.execute(
            """SELECT date, COALESCE(min_price_nm, CASE WHEN foil = 2 THEN min_price END),
                      median_price, total_qty FROM daily_stats
               WHERE blueprint_id=? AND foil=? ORDER BY date DESC LIMIT 1""",
            (bp_id, f),
        ).fetchone()
        sold = con.execute(
            """SELECT SUM(sold_qty_est), SUM(removed_qty_est) FROM daily_stats
               WHERE blueprint_id=? AND foil=? AND date >= date('now', ?)""",
            (bp_id, f, f"-{args.giorni} days"),
        ).fetchone()
        ref = con.execute(
            """SELECT CASE WHEN ? = 1 THEN sf.eur_foil ELSE sf.eur END, sf.edhrec_rank
               FROM blueprints b JOIN scryfall_daily sf ON sf.scryfall_id = b.scryfall_id
               WHERE b.id = ? ORDER BY sf.date DESC LIMIT 1""",
            (foil, bp_id)).fetchone() or (None, None)
        label = f"{name}{' (' + version + ')' if version else ''}"
        summary.append([bp_id, label[:40], (code or "").upper(), *(last[1:] if last else
                        (None, None, None)), ref[0], sold[0], sold[1], ref[1]])
    table(["id", "carta", "set", "min NM €", "mediana €", "copie", "rif. CM €",
           "vendute", "uscite", "EDHREC"], summary)
    print("'min NM €' per i sigillati = prezzo minimo; "
          "'rif. CM €' = prezzo di riferimento Cardmarket (Scryfall); "
          "'EDHREC' = posizione di popolarità in Commander, 1 = la più giocata.")
    print(f"\n'vendute' e 'uscite' sono la somma degli ultimi {args.giorni} giorni.")

    if len(prints) == 1 or args.id:
        bp_id = args.id or prints[0][0]
        hist = con.execute(
            """SELECT date, min_price, min_price_nm, min_price_nm_en, median_price,
                      total_qty, sold_qty_est, removed_qty_est, new_qty
               FROM daily_stats WHERE blueprint_id=? AND foil=?
               AND date >= date('now', ?) ORDER BY date""",
            (bp_id, finish_of.get(bp_id, foil), f"-{args.giorni} days"),
        ).fetchall()
        print(f"\nStorico della stampa {bp_id}:")
        table(["data", "min", "min NM", "min NM EN", "mediana", "copie",
               "vendute", "uscite", "nuove"], hist)
    else:
        print("Per lo storico giornaliero di una stampa aggiungi --id <numero>.")


def cmd_movimenti(con, args):
    """Stampe con più vendite stimate nel periodo, con variazione del prezzo NM."""
    finishes = finishes_for(args)
    fmarks = ",".join("?" * len(finishes))
    cond, cparams = category_filter(args)
    rows = con.execute(
        f"""
        WITH periodo AS (
            SELECT blueprint_id, foil,
                   SUM(COALESCE(sold_qty_est, 0)) AS vendute,
                   SUM(COALESCE(removed_qty_est, 0)) AS uscite,
                   SUM(COALESCE(new_qty, 0)) AS nuove,
                   MIN(date) AS d0, MAX(date) AS d1
            FROM daily_stats
            WHERE foil IN ({fmarks}) AND date >= date('now', ?)
            GROUP BY blueprint_id, foil
        )
        SELECT b.name, e.code, p.vendute, p.uscite, p.nuove,
               COALESCE(s0.min_price_nm, CASE WHEN s0.foil = 2 THEN s0.min_price END),
               COALESCE(s1.min_price_nm, CASE WHEN s1.foil = 2 THEN s1.min_price END),
               s1.total_qty
        FROM periodo p
        JOIN daily_stats s0 ON s0.blueprint_id = p.blueprint_id AND s0.foil = p.foil AND s0.date = p.d0
        JOIN daily_stats s1 ON s1.blueprint_id = p.blueprint_id AND s1.foil = p.foil AND s1.date = p.d1
        JOIN blueprints b ON b.id = p.blueprint_id
        LEFT JOIN expansions e ON e.id = b.expansion_id
        {ref_join()}
        WHERE COALESCE(s1.min_price_nm, CASE WHEN s1.foil = 2 THEN s1.min_price END) >= ?
              AND {cond} AND NOT {anomalo_expr(args.soglia, d='s1')}
        ORDER BY p.vendute DESC
        LIMIT ?
        """,
        (*finishes, f"-{args.giorni} days", scryfall_date(con), args.min_prezzo,
         *cparams, args.top),
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
                             AND d.date=? AND d.foil=1 AND d.n_listings>0)) AS foil,
                  SUM(EXISTS(SELECT 1 FROM daily_stats d WHERE d.blueprint_id=b.id
                             AND d.date=? AND d.foil=2 AND d.n_listings>0)) AS sigillati
           FROM blueprints b LEFT JOIN categories k ON k.id=b.category_id
           GROUP BY k.name ORDER BY catalogo DESC""",
        (last, last, last),
    ).fetchall()
    table(["categoria", "in catalogo", "in vendita non foil", "in vendita foil",
           "in vendita senza finitura"], rows)

    single, _ = category_filter(SimpleArgs(), "b")
    sdate = scryfall_date(con)
    anom = anomalo_expr(args.soglia)
    if sdate:
        n_anom = con.execute(
            f"""SELECT COUNT(*) FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
                {ref_join()} WHERE d.date=? AND d.foil=0 AND {single} AND {anom}""",
            (sdate, last, *SINGLE)).fetchone()[0]
        cov = con.execute(
            f"""SELECT SUM({ref_expr()} IS NOT NULL),
                       SUM({ref_expr()} IS NULL AND rn.ref_max IS NOT NULL),
                       SUM({eff_ref_expr()} IS NULL), COUNT(*)
                FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id {ref_join()}
                WHERE d.date=? AND d.foil=0 AND d.n_listings>0 AND {single}""",
            (sdate, last, *SINGLE)).fetchone()
        print(f"\nRiferimenti Scryfall del {sdate} per le {cov[3]} carte singole non foil in vendita: "
              f"{cov[0]} con prezzo della stampa, {cov[1]} solo con il tetto della carta, "
              f"{cov[2]} senza riferimento (non controllabili).")
        print(f"Escluse come anomale: {n_anom} (minimo CardTrader oltre {args.soglia:g} volte il "
              f"riferimento e di almeno {MIN_SCARTO:g} €).")
        print("Per vederle: python analisi.py anomalie")
    else:
        print("\nAttenzione: nessun prezzo Scryfall nel database, i prezzi anomali non sono "
              "filtrati. Lancia python scryfall_sync.py")
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
           FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id {ref_join()}
           WHERE d.date=? AND d.foil=0 AND {single} AND NOT {anom}
           GROUP BY fascia ORDER BY fascia""",
        (sdate, last, *SINGLE),
    ).fetchall()
    table(["fascia", "stampe"], bands)

    print(f"\nEspansioni con più carte singole in vendita (non foil)")
    exps = con.execute(
        f"""SELECT e.name, UPPER(e.code), COUNT(*),
                   ROUND(SUM(d.min_price_nm), 0)
            FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
            JOIN expansions e ON e.id=b.expansion_id {ref_join()}
            WHERE d.date=? AND d.foil=0 AND {single} AND NOT {anom}
            GROUP BY e.id ORDER BY COUNT(*) DESC LIMIT ?""",
        (sdate, last, *SINGLE, args.top),
    ).fetchall()
    table(["espansione", "set", "stampe in vendita", "somma minimi NM €"], exps)

    print(f"\nCarte singole non foil più care (minimo Near Mint)")
    top = con.execute(
        f"""SELECT b.name, b.version, UPPER(e.code), d.min_price_nm, {ref_expr()}, d.total_qty
            FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
            LEFT JOIN expansions e ON e.id=b.expansion_id {ref_join()}
            WHERE d.date=? AND d.foil=0 AND {single} AND d.min_price_nm IS NOT NULL
                  AND NOT {anom}
            ORDER BY d.min_price_nm DESC LIMIT ?""",
        (sdate, last, *SINGLE, args.top),
    ).fetchall()
    table(["carta", "versione", "set", "min NM €", "rif. Cardmarket €", "copie"],
          [[n[:34], (v or "")[:18], c, p, r, q] for n, v, c, p, r, q in top])


def cmd_anomalie(con, args):
    """Stampe il cui minimo CardTrader è molto sopra il riferimento Cardmarket."""
    last = con.execute("SELECT MAX(date) FROM daily_stats").fetchone()[0]
    sdate = scryfall_date(con)
    if not last or not sdate:
        print("Servono sia una raccolta CardTrader sia i prezzi Scryfall (scryfall_sync.py).")
        return
    cond, cparams = category_filter(args)
    price = "COALESCE(d.min_price_nm, d.min_price)"
    rows = con.execute(
        f"""SELECT b.name, b.version, UPPER(e.code), d.foil, {price}, {ref_expr()}, rn.ref_max,
                   ROUND({price} / {eff_ref_expr()}, 1), d.n_listings
            FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
            LEFT JOIN expansions e ON e.id=b.expansion_id {ref_join()}
            WHERE d.date=? AND {cond} AND {anomalo_expr(args.soglia)}
            ORDER BY {price} / {eff_ref_expr()} DESC LIMIT ?""",
        (sdate, last, *cparams, args.top)).fetchall()
    total = con.execute(
        f"""SELECT COUNT(*) FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
            {ref_join()} WHERE d.date=? AND {cond} AND {anomalo_expr(args.soglia)}""",
        (sdate, last, *cparams)).fetchone()[0]
    print(f"Stampe anomale il {last} (riferimento Scryfall del {sdate}, soglia "
          f"{args.soglia:g}x e +{MIN_SCARTO:g} €): {total}. Le più lontane dal riferimento:\n")
    table(["carta", "versione", "set", "foil", "min CT €", "rif. stampa €", "tetto carta €",
           "volte", "inserzioni"],
          [[n[:30], (v or "")[:14], c, "sì" if f else "", p, r, m, x, k]
           for n, v, c, f, p, r, m, x, k in rows])
    print("\n'rif. stampa' = prezzo Cardmarket di questa stampa (vuoto se assente o sotto "
          f"{MIN_RIF:g} €); 'tetto carta' = il più alto tra tutte le stampe della stessa carta, "
          "usato quando manca il primo.")


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
    m.add_argument("--soglia", type=float, default=DEFAULT_SOGLIA)
    m.add_argument("--sigillati", action="store_true", help="classifica dei prodotti sigillati")
    m.add_argument("--tutte", action="store_true", help="tutte le categorie")

    sub.add_parser("stato", help="stato delle ultime raccolte")

    pa = sub.add_parser("panoramica", help="quadro d'insieme dell'ultima raccolta")
    pa.add_argument("--top", type=int, default=20)
    pa.add_argument("--soglia", type=float, default=DEFAULT_SOGLIA)

    an = sub.add_parser("anomalie", help="prezzi CardTrader fuori mercato rispetto a Cardmarket")
    an.add_argument("--top", type=int, default=40)
    an.add_argument("--soglia", type=float, default=DEFAULT_SOGLIA)
    an.add_argument("--sigillati", action="store_true")
    an.add_argument("--tutte", action="store_true")

    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    con.executescript(SCRYFALL_SCHEMA)   # crea le tabelle Scryfall vuote se mancano
    prepare_reference(con)
    {"carta": cmd_carta, "movimenti": cmd_movimenti, "stato": cmd_stato,
     "panoramica": cmd_panoramica, "anomalie": cmd_anomalie}[args.cmd](con, args)


if __name__ == "__main__":
    main()
