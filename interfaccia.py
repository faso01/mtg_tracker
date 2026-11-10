#!/usr/bin/env python3
"""
Interfaccia grafica per consultare cardtrader.db nel browser.

Avvio: doppio clic su avvia_interfaccia.bat, oppure
  python -m streamlit run interfaccia.py
Si apre il browser su http://localhost:8501. Funziona solo sul tuo PC: nessun dato va online.
"""

import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import analisi as an
from ct_collector import NO_FINISH_CATEGORIES

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("MTG_DB", SCRIPT_DIR / "cardtrader.db"))

# Colori validati per daltonismo (palette di riferimento, primi tre slot)
BLU, ARANCIO, ACQUA = "#2a78d6", "#eb6834", "#1baf7a"

st.set_page_config(page_title="MTG Tracker", page_icon="🃏", layout="wide")


# ------------------------------------------------------------------ dati

@st.cache_resource
def connessione():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.executescript(an.SCRYFALL_SCHEMA)
    for sql in ("CREATE INDEX IF NOT EXISTS idx_ds_date ON daily_stats(date, foil)",
                "CREATE INDEX IF NOT EXISTS idx_sd_date ON scryfall_daily(date)"):
        try:
            con.execute(sql)
        except sqlite3.OperationalError:
            pass  # database in sola lettura: si lavora senza indice
    an.prepare_reference(con)
    return con


def q(sql, params=()):
    return pd.read_sql_query(sql, connessione(), params=list(params))


def uno(sql, params=()):
    return connessione().execute(sql, list(params)).fetchone()


def single_cond(alias="b"):
    marks = ",".join("?" * len(an.SINGLE))
    return (f"{alias}.category_id IN (SELECT id FROM categories WHERE name IN ({marks}))",
            list(an.SINGLE))


def sealed_cond(alias="b"):
    marks = ",".join("?" * len(an.SEALED))
    return (f"{alias}.category_id IN (SELECT id FROM categories WHERE name IN ({marks}))",
            list(an.SEALED))


def ultima_data():
    return uno("SELECT MAX(date) FROM daily_stats")[0]


def altezza(df):
    """Altezza della tabella: tutte le righe fino a 600 pixel, poi scorre."""
    return min(600, 35 * (len(df) + 1) + 3)


def euro(v):
    return "-" if v is None or pd.isna(v) else f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def numero(v):
    return "-" if v is None or pd.isna(v) else f"{int(v):,}".replace(",", ".")


# ---------------------------------------------------------- aggiornamento

def aggiorna_dati():
    with st.spinner("Scarico i dati da GitHub e ricostruisco il database... "
                    "la prima volta può richiedere qualche minuto."):
        try:                         # Windows non sostituisce un file aperto: prima si chiude
            connessione().close()
        except Exception:
            pass
        connessione.clear()
        res = subprocess.run([sys.executable, str(SCRIPT_DIR / "archivio.py"), "aggiorna"],
                             cwd=SCRIPT_DIR, capture_output=True, text=True)
    st.session_state["esito_aggiornamento"] = (res.returncode, res.stdout + res.stderr)


def barra_laterale():
    st.sidebar.title("🃏 MTG Tracker")
    pagina = st.sidebar.radio("Pagina", ["Panoramica", "Carta", "Movimenti", "Anomalie"],
                              label_visibility="collapsed")
    st.sidebar.divider()
    if st.sidebar.button("⟳ Aggiorna i dati", width="stretch",
                         help="Scarica da GitHub i giorni nuovi e ricostruisce il database"):
        aggiorna_dati()
        st.rerun()
    esito = st.session_state.get("esito_aggiornamento")
    if esito:
        code, out = esito
        (st.sidebar.success if code == 0 else st.sidebar.error)(
            "Dati aggiornati." if code == 0 else "Aggiornamento non riuscito: vedi i dettagli.")
        with st.sidebar.expander("Dettagli"):
            st.code(out.strip() or "(nessun messaggio)", language=None)
    if DB_PATH.exists():
        last = ultima_data()
        st.sidebar.caption(f"Ultima raccolta nel database: **{last or '-'}**")
    return pagina


# ------------------------------------------------------------- pagine

def pagina_panoramica():
    last = ultima_data()
    sdate = an.scryfall_date(connessione())
    st.title("Panoramica")
    st.caption(f"Raccolta del {last}. Prezzi di riferimento Cardmarket (Scryfall) del {sdate or '-'}.")

    sc, sp = single_cond()
    se, sep = sealed_cond()
    anom = an.anomalo_expr(an.DEFAULT_SOGLIA)
    nf = uno(f"""SELECT COUNT(*) FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
                 WHERE d.date=? AND d.foil=0 AND d.n_listings>0 AND {sc}""", [last, *sp])[0]
    fo = uno(f"""SELECT COUNT(*) FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
                 WHERE d.date=? AND d.foil=1 AND d.n_listings>0 AND {sc}""", [last, *sp])[0]
    sg = uno(f"""SELECT COUNT(*) FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
                 WHERE d.date=? AND d.n_listings>0 AND {se}""", [last, *sep])[0]
    na = uno(f"""SELECT COUNT(*) FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
                 {an.ref_join()} WHERE d.date=? AND d.foil=0 AND {sc} AND {anom}""",
             [sdate, last, *sp])[0] if sdate else None
    run = uno("SELECT started_at, finished_at, expansions_ok, expansions_failed FROM runs "
              "ORDER BY id DESC LIMIT 1")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Carte singole in vendita", numero(nf), help="Stampe non foil con almeno una copia")
    c2.metric("Carte singole foil in vendita", numero(fo))
    c3.metric("Prodotti sigillati in vendita", numero(sg))
    c4.metric("Prezzi anomali esclusi", numero(na) if na is not None else "-",
              help="Carte singole non foil con prezzo oltre 5 volte il riferimento Cardmarket")
    if run and run[0] and run[1]:
        durata = datetime.fromisoformat(run[1]) - datetime.fromisoformat(run[0])
        ore, resto = divmod(int(durata.total_seconds()), 3600)
        st.caption(f"Ultima raccolta: {run[2]} espansioni riuscite, {run[3]} fallite, "
                   f"durata {ore} h {resto // 60} min.")

    st.subheader("Carte singole per fascia di prezzo")
    fasce = q(f"""SELECT CASE
                     WHEN d.min_price_nm IS NULL THEN 'senza copie NM'
                     WHEN d.min_price_nm < 1 THEN 'meno di 1 €'
                     WHEN d.min_price_nm < 5 THEN '1 - 5 €'
                     WHEN d.min_price_nm < 20 THEN '5 - 20 €'
                     WHEN d.min_price_nm < 100 THEN '20 - 100 €'
                     ELSE 'oltre 100 €' END AS fascia, COUNT(*) AS stampe
                  FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id {an.ref_join()}
                  WHERE d.date=? AND d.foil=0 AND d.n_listings>0 AND {sc} AND NOT {anom}
                  GROUP BY fascia""", [sdate, last, *sp])
    ordine = ["meno di 1 €", "1 - 5 €", "5 - 20 €", "20 - 100 €", "oltre 100 €", "senza copie NM"]
    col_a, col_b = st.columns([3, 2])
    with col_a:
        chart = alt.Chart(fasce).mark_bar(color=BLU, cornerRadiusTopLeft=4,
                                          cornerRadiusTopRight=4).encode(
            x=alt.X("fascia:N", sort=ordine, title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("stampe:Q", title="Stampe non foil"),
            tooltip=[alt.Tooltip("fascia:N", title="Fascia"),
                     alt.Tooltip("stampe:Q", title="Stampe", format=",")],
        ).properties(height=280)
        st.altair_chart(chart, width="stretch")
    with col_b:
        tot = fasce["stampe"].sum() or 1
        tab = fasce.set_index("fascia").reindex(ordine).dropna().reset_index()
        tab["quota"] = (tab["stampe"] / tot * 100).round(1).astype(str) + " %"
        st.dataframe(tab, hide_index=True, width="stretch",
                     column_config={"fascia": "Fascia", "stampe": st.column_config.NumberColumn(
                         "Stampe", format="%d"), "quota": "Quota"})
        st.caption("Sopra i 5 € le commissioni e le spedizioni pesano meno: è la fascia "
                   "in cui ha senso cercare investimenti.")

    st.subheader("Carte singole più care")
    top = q(f"""SELECT b.name AS carta, COALESCE(b.version, '') AS versione,
                       UPPER(e.code) AS set_, d.min_price_nm AS min_nm,
                       {an.ref_expr()} AS rif_cm, d.total_qty AS copie, b.id AS id
                FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
                LEFT JOIN expansions e ON e.id=b.expansion_id {an.ref_join()}
                WHERE d.date=? AND d.foil=0 AND {sc} AND d.min_price_nm IS NOT NULL AND NOT {anom}
                ORDER BY d.min_price_nm DESC LIMIT 25""", [sdate, last, *sp])
    st.dataframe(top, hide_index=True, width="stretch", column_config={
        "carta": "Carta", "versione": "Versione", "set_": "Set",
        "min_nm": st.column_config.NumberColumn("Minimo NM", format="%.2f €"),
        "rif_cm": st.column_config.NumberColumn("Rif. Cardmarket", format="%.2f €"),
        "copie": "Copie", "id": st.column_config.NumberColumn("id", format="%d")})


def pagina_carta():
    st.title("Carta")
    c1, c2 = st.columns([3, 2])
    nome = c1.text_input("Nome della carta (in inglese, anche solo una parte)",
                         value=st.session_state.get("ultima_ricerca", ""),
                         placeholder="es. Sheoldred")
    tipo = c2.radio("Cerca tra", ["Carte singole", "Prodotti sigillati"], horizontal=True)
    if not nome.strip():
        st.info("Scrivi il nome di una carta o di un prodotto sigillato per vederne lo storico.")
        return
    st.session_state["ultima_ricerca"] = nome
    cond, params = single_cond() if tipo == "Carte singole" else sealed_cond()
    stampe = q(f"""SELECT b.id, b.name, COALESCE(b.version, '') AS version,
                          UPPER(COALESCE(e.code, '')) AS code, COALESCE(e.name, '') AS espansione,
                          k.name AS categoria
                   FROM blueprints b LEFT JOIN expansions e ON e.id=b.expansion_id
                   LEFT JOIN categories k ON k.id=b.category_id
                   WHERE b.name LIKE ? AND {cond} ORDER BY b.name, e.name LIMIT 300""",
               [f"%{nome.strip()}%", *params])
    if stampe.empty:
        st.warning("Nessuna stampa trovata con quel nome.")
        return
    etichette = {r.id: f"{r.name}{' (' + r.version + ')' if r.version else ''} — "
                       f"{r.espansione} [{r.code}]" for r in stampe.itertuples()}
    c1, c2 = st.columns([3, 1])
    scelta = c1.selectbox(f"Stampa ({len(stampe)} trovate)", list(etichette),
                          format_func=etichette.get)
    riga = stampe.set_index("id").loc[scelta]
    sigillato = riga["categoria"] in NO_FINISH_CATEGORIES
    foil = 0 if sigillato else (1 if c2.toggle("Foil", value=False) else 0)
    finitura = 2 if sigillato else foil

    storia = q("""SELECT date, min_price, min_price_nm, median_price, total_qty, n_listings,
                         sold_qty_est, removed_qty_est, new_qty
                  FROM daily_stats WHERE blueprint_id=? AND foil=? ORDER BY date""",
               [scelta, finitura])
    rif = q(f"""SELECT s.date, {'s.eur_foil' if foil else 's.eur'} AS rif, s.edhrec_rank
                FROM scryfall_daily s JOIN blueprints b ON b.scryfall_id = s.scryfall_id
                WHERE b.id=? ORDER BY s.date""", [scelta])
    if storia.empty:
        st.warning("Nessuna inserzione registrata per questa stampa"
                   + ("" if sigillato else " in questa finitura") + ".")
        return
    storia["date"] = pd.to_datetime(storia["date"])
    rif["date"] = pd.to_datetime(rif["date"])
    prezzo_col = "min_price" if sigillato else "min_price_nm"
    ultimo = storia.iloc[-1]

    m1, m2, m3, m4 = st.columns(4)
    delta = None
    if len(storia) > 1 and pd.notna(storia[prezzo_col].iloc[0]) and pd.notna(ultimo[prezzo_col]):
        delta = euro(ultimo[prezzo_col] - storia[prezzo_col].iloc[0]) + " dal primo giorno"
    m1.metric("Prezzo minimo" + ("" if sigillato else " NM"), euro(ultimo[prezzo_col]), delta)
    m2.metric("Rif. Cardmarket", euro(rif["rif"].iloc[-1]) if not rif.empty else "-")
    m3.metric("Copie in vendita", numero(ultimo["total_qty"]))
    m4.metric("Vendute nel periodo", numero(storia["sold_qty_est"].fillna(0).sum()),
              help="Somma delle copie quasi certamente vendute in tutti i giorni registrati")

    info = uno("""SELECT c.reserved, c.released_at, c.rarity, c.legal_commander, c.oracle_id
                  FROM scryfall_cards c JOIN blueprints b ON b.scryfall_id = c.scryfall_id
                  WHERE b.id=?""", [scelta])
    if info:
        n_stampe = uno("SELECT COUNT(*) FROM scryfall_cards WHERE oracle_id=?", [info[4]])[0] \
            if info[4] else None
        parti = [f"uscita {info[1]}" if info[1] else None,
                 f"rarità {info[2]}" if info[2] else None,
                 "**Reserved List: non verrà ristampata**" if info[0] else None,
                 f"stampe esistenti di questa carta: {n_stampe}" if n_stampe else None,
                 "legale in Commander" if info[3] == "legal" else
                 ("non legale in Commander" if info[3] else None)]
        st.caption(" · ".join(p for p in parti if p))

    if len(storia) < 2:
        st.info("Per ora c'è un solo giorno di dati: i grafici si riempiono con le prossime raccolte.")

    serie = storia[["date"]].copy()
    serie["Minimo" + ("" if sigillato else " NM") + " CardTrader"] = storia[prezzo_col]
    serie["Mediana CardTrader"] = storia["median_price"]
    lunga = serie.melt("date", var_name="serie", value_name="euro")
    if not rif.empty and rif["rif"].notna().any():
        lunga = pd.concat([lunga, rif.rename(columns={"rif": "euro"}).assign(
            serie="Riferimento Cardmarket")[["date", "serie", "euro"]]])
    nomi = list(dict.fromkeys(lunga["serie"]))
    colori = alt.Scale(domain=nomi, range=[BLU, ARANCIO, ACQUA][:len(nomi)])
    st.subheader("Prezzo nel tempo")
    linea = alt.Chart(lunga.dropna()).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(
        size=60, filled=True)).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%d/%m", tickCount="day")),
        y=alt.Y("euro:Q", title="Euro", scale=alt.Scale(zero=False)),
        color=alt.Color("serie:N", scale=colori, title=None, legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip("date:T", title="Data", format="%d/%m/%Y"),
                 alt.Tooltip("serie:N", title="Serie"),
                 alt.Tooltip("euro:Q", title="Euro", format=",.2f")],
    ).properties(height=300)
    st.altair_chart(linea, width="stretch")

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Vendite stimate al giorno")
        v = storia[["date", "sold_qty_est", "removed_qty_est"]].rename(columns={
            "sold_qty_est": "Vendute (quasi certe)", "removed_qty_est": "Uscite (vendute o ritirate)"})
        v = v.melt("date", var_name="tipo", value_name="copie").dropna()
        if v.empty:
            st.caption("Le vendite compaiono dal secondo giorno di dati.")
        else:
            barre = alt.Chart(v).mark_bar(stroke="white", strokeWidth=2, cornerRadiusTopLeft=4,
                                          cornerRadiusTopRight=4).encode(
                x=alt.X("yearmonthdate(date):O", title=None,
                        axis=alt.Axis(format="%d/%m", labelAngle=0)),
                y=alt.Y("copie:Q", title="Copie", stack="zero"),
                color=alt.Color("tipo:N", title=None, legend=alt.Legend(orient="top"),
                                scale=alt.Scale(domain=["Vendute (quasi certe)",
                                                        "Uscite (vendute o ritirate)"],
                                                range=[BLU, ARANCIO])),
                tooltip=[alt.Tooltip("date:T", title="Data", format="%d/%m/%Y"),
                         alt.Tooltip("tipo:N", title="Tipo"),
                         alt.Tooltip("copie:Q", title="Copie")],
            ).properties(height=260)
            st.altair_chart(barre, width="stretch")
    with col_b:
        st.subheader("Copie in vendita")
        copie = alt.Chart(storia).mark_line(color=BLU, strokeWidth=2, point=alt.OverlayMarkDef(
            size=60, filled=True, color=BLU)).encode(
            x=alt.X("date:T", title=None, axis=alt.Axis(format="%d/%m", tickCount="day")),
            y=alt.Y("total_qty:Q", title="Copie"),
            tooltip=[alt.Tooltip("date:T", title="Data", format="%d/%m/%Y"),
                     alt.Tooltip("total_qty:Q", title="Copie")],
        ).properties(height=260)
        st.altair_chart(copie, width="stretch")
        st.caption("Copie tra le 25 inserzioni più economiche. In calo con vendite costanti: "
                   "l'offerta si sta esaurendo.")

    if not rif.empty and rif["edhrec_rank"].notna().any():
        st.subheader("Popolarità in Commander (EDHREC)")
        ed = alt.Chart(rif.dropna(subset=["edhrec_rank"])).mark_line(
            color=ACQUA, strokeWidth=2, point=alt.OverlayMarkDef(size=60, filled=True,
                                                                 color=ACQUA)).encode(
            x=alt.X("date:T", title=None, axis=alt.Axis(format="%d/%m", tickCount="day")),
            y=alt.Y("edhrec_rank:Q", title="Posizione (1 = la più giocata)",
                    scale=alt.Scale(reverse=True, zero=False)),
            tooltip=[alt.Tooltip("date:T", title="Data", format="%d/%m/%Y"),
                     alt.Tooltip("edhrec_rank:Q", title="Posizione", format=",")],
        ).properties(height=220)
        st.altair_chart(ed, width="stretch")
        st.caption("La linea che sale indica una carta sempre più giocata.")

    with st.expander("Tabella giorno per giorno"):
        t = storia.copy()
        t["date"] = t["date"].dt.strftime("%d/%m/%Y")
        st.dataframe(t, hide_index=True, width="stretch", column_config={
            "date": "Data", "min_price": st.column_config.NumberColumn("Minimo", format="%.2f €"),
            "min_price_nm": st.column_config.NumberColumn("Minimo NM", format="%.2f €"),
            "median_price": st.column_config.NumberColumn("Mediana", format="%.2f €"),
            "total_qty": "Copie", "n_listings": "Inserzioni", "sold_qty_est": "Vendute",
            "removed_qty_est": "Uscite", "new_qty": "Nuove"})


def pagina_movimenti():
    st.title("Movimenti")
    st.caption("Le stampe con più vendite stimate nel periodo. Clicca il titolo di una colonna "
               "per ordinare.")
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
    giorni = c1.slider("Periodo (giorni)", 1, 90, 7)
    min_prezzo = c2.number_input("Prezzo minimo (€)", min_value=0.0, value=5.0, step=1.0)
    tipo = c3.radio("Tipo", ["Carte singole", "Prodotti sigillati"], horizontal=True)
    foil = c4.toggle("Foil", value=False, disabled=tipo != "Carte singole")
    sigillati = tipo == "Prodotti sigillati"
    cond, params = sealed_cond() if sigillati else single_cond()
    finiture = (0, 2) if sigillati else ((1,) if foil else (0,))
    fm = ",".join("?" * len(finiture))
    prezzo = "COALESCE({a}.min_price_nm, CASE WHEN {a}.foil = 2 THEN {a}.min_price END)"
    df = q(f"""
        WITH periodo AS (
            SELECT blueprint_id, foil,
                   SUM(COALESCE(sold_qty_est, 0)) AS vendute,
                   SUM(COALESCE(removed_qty_est, 0)) AS uscite,
                   SUM(COALESCE(new_qty, 0)) AS nuove,
                   MIN(date) AS d0, MAX(date) AS d1
            FROM daily_stats
            WHERE foil IN ({fm}) AND date >= date((SELECT MAX(date) FROM daily_stats), ?)
            GROUP BY blueprint_id, foil)
        SELECT b.name AS carta, COALESCE(b.version, '') AS versione, UPPER(e.code) AS set_,
               p.vendute, p.uscite, p.nuove,
               {prezzo.format(a='s0')} AS inizio, {prezzo.format(a='s1')} AS fine,
               s1.total_qty AS copie, {an.ref_expr(d='s1')} AS rif_cm
        FROM periodo p
        JOIN daily_stats s0 ON s0.blueprint_id=p.blueprint_id AND s0.foil=p.foil AND s0.date=p.d0
        JOIN daily_stats s1 ON s1.blueprint_id=p.blueprint_id AND s1.foil=p.foil AND s1.date=p.d1
        JOIN blueprints b ON b.id=p.blueprint_id
        LEFT JOIN expansions e ON e.id=b.expansion_id
        {an.ref_join()}
        WHERE {prezzo.format(a='s1')} >= ? AND {cond}
              AND NOT {an.anomalo_expr(an.DEFAULT_SOGLIA, d='s1')}
        ORDER BY p.vendute DESC, p.uscite DESC LIMIT 500""",
        [*finiture, f"-{giorni - 1} days", an.scryfall_date(connessione()), min_prezzo,
         *params])
    if df.empty:
        st.info("Nessun movimento con questi filtri.")
        return
    df["variazione"] = (df["fine"] - df["inizio"]) / df["inizio"] * 100
    st.dataframe(df, hide_index=True, width="stretch", height=altezza(df), column_config={
        "carta": "Carta", "versione": "Versione", "set_": "Set",
        "vendute": st.column_config.NumberColumn("Vendute", help="Copie quasi certamente vendute"),
        "uscite": st.column_config.NumberColumn("Uscite", help="Inserzioni sparite: vendute o ritirate"),
        "nuove": st.column_config.NumberColumn("Nuove", help="Copie messe in vendita"),
        "inizio": st.column_config.NumberColumn("Prezzo inizio", format="%.2f €"),
        "fine": st.column_config.NumberColumn("Prezzo oggi", format="%.2f €"),
        "variazione": st.column_config.NumberColumn("Variazione", format="%+.1f %%"),
        "copie": st.column_config.NumberColumn("Copie oggi"),
        "rif_cm": st.column_config.NumberColumn("Rif. Cardmarket", format="%.2f €")})
    st.caption(f"{len(df)} stampe. Le vendite compaiono dal secondo giorno di dati; per le carte con "
               "molte copie in vendita sono sottostimate, conta la tendenza.")


def pagina_anomalie():
    st.title("Anomalie")
    st.caption("Prezzi CardTrader molto sopra il riferimento Cardmarket, esclusi da panoramica "
               "e movimenti. Servono a controllare che il filtro non scarti carte vere.")
    sdate = an.scryfall_date(connessione())
    last = ultima_data()
    if not sdate:
        st.warning("Mancano i prezzi di riferimento Scryfall nel database.")
        return
    c1, c2 = st.columns(2)
    soglia = c1.slider("Soglia: volte sopra il riferimento", 2.0, 20.0, an.DEFAULT_SOGLIA, 0.5)
    ordine = c2.radio("Ordine", ["Più vicine alla soglia (possibili falsi allarmi)",
                                 "Più lontane dal riferimento"], horizontal=False)
    price = "COALESCE(d.min_price_nm, d.min_price)"
    verso = "ASC" if ordine.startswith("Più vicine") else "DESC"
    sc, sp = single_cond()
    df = q(f"""SELECT b.name AS carta, COALESCE(b.version, '') AS versione,
                      UPPER(e.code) AS set_, CASE WHEN d.foil=1 THEN 'sì' ELSE '' END AS foil,
                      {price} AS prezzo, {an.ref_expr()} AS rif_stampa, rn.ref_max AS tetto,
                      {price} / {an.eff_ref_expr()} AS volte, d.n_listings AS inserzioni
               FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
               LEFT JOIN expansions e ON e.id=b.expansion_id {an.ref_join()}
               WHERE d.date=? AND {sc} AND {an.anomalo_expr(soglia)}
               ORDER BY volte {verso} LIMIT 500""", [sdate, last, *sp])
    st.metric("Stampe anomale", numero(len(df)) + ("+" if len(df) == 500 else ""))
    st.dataframe(df, hide_index=True, width="stretch", height=altezza(df), column_config={
        "carta": "Carta", "versione": "Versione", "set_": "Set", "foil": "Foil",
        "prezzo": st.column_config.NumberColumn("Minimo CardTrader", format="%.2f €"),
        "rif_stampa": st.column_config.NumberColumn("Rif. stampa", format="%.2f €",
                                                    help="Prezzo Cardmarket di questa stampa"),
        "tetto": st.column_config.NumberColumn("Tetto carta", format="%.2f €",
                                               help="Il più alto tra tutte le stampe della carta"),
        "volte": st.column_config.NumberColumn("Volte", format="%.1f"),
        "inserzioni": "Inserzioni"})


# --------------------------------------------------------------- avvio

def main():
    pagina = barra_laterale()
    if not DB_PATH.exists():
        st.title("MTG Tracker")
        st.warning(f"Il database `{DB_PATH.name}` non c'è ancora. Clicca **Aggiorna i dati** "
                   "nella barra a sinistra per scaricarlo da GitHub.")
        return
    if not ultima_data():
        st.warning("Il database non contiene ancora nessuna raccolta.")
        return
    {"Panoramica": pagina_panoramica, "Carta": pagina_carta,
     "Movimenti": pagina_movimenti, "Anomalie": pagina_anomalie}[pagina]()


main()
