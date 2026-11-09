"""Test con un'API CardTrader simulata: python test_collector.py"""
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import ct_collector as ct


def product(pid, bp, cents, qty=1, cond="Near Mint", lang="en", vac=False, hub=True):
    return {"id": pid, "blueprint_id": bp, "name_en": f"Card {bp}", "quantity": qty,
            "price": {"cents": cents, "currency": "EUR"},
            "properties_hash": {"condition": cond, "mtg_language": lang, "mtg_foil": False},
            "user": {"id": 1, "can_sell_via_hub": hub}, "graded": False,
            "on_vacation": vac, "bundle_size": 1}


class FakeClient:
    def __init__(self):
        self.day = 0
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path == "/categories":
            return [{"id": 1, "name": "Magic Single Card", "game_id": 1},
                    {"id": 2, "name": "Magic Token/Emblem", "game_id": 1},
                    {"id": 5, "name": "Magic Booster Boxes", "game_id": 1}]
        if path == "/expansions":
            return [{"id": 10, "game_id": 1, "code": "dmu", "name": "Dominaria United"},
                    {"id": 99, "game_id": 5, "code": "xyz", "name": "Pokemon set"}]
        if path == "/blueprints/export":
            return [{"id": 100, "name": "Sheoldred, the Apocalypse", "version": None,
                     "expansion_id": 10, "category_id": 1, "scryfall_id": "abc",
                     "card_market_ids": [1], "tcg_player_id": None},
                    {"id": 200, "name": "Bulk Common", "version": None, "expansion_id": 10,
                     "category_id": 1, "scryfall_id": "def", "card_market_ids": [],
                     "tcg_player_id": None},
                    {"id": 400, "name": "Polymorph", "version": None, "expansion_id": 10,
                     "category_id": 1, "scryfall_id": "jkl", "card_market_ids": [],
                     "tcg_player_id": None},
                    {"id": 500, "name": "Druid of the Cowl", "version": None, "expansion_id": 10,
                     "category_id": 1, "scryfall_id": "w24", "card_market_ids": [],
                     "tcg_player_id": None},
                    {"id": 600, "name": "Seven Dwarves", "version": None, "expansion_id": 10,
                     "category_id": 1, "scryfall_id": "sld7", "card_market_ids": [],
                     "tcg_player_id": None},
                    {"id": 700, "name": "Dominaria United | Draft Booster Box", "version": None,
                     "expansion_id": 10, "category_id": 5, "scryfall_id": None,
                     "card_market_ids": [], "tcg_player_id": None},
                    {"id": 300, "name": "Zombie Token", "version": None, "expansion_id": 10,
                     "category_id": 2, "scryfall_id": "ghi", "card_market_ids": [],
                     "tcg_player_id": None}]
        if path == "/marketplace/products":
            foil = params.get("foil")
            if foil == "true":
                return {}
            if foil == "false":
                # caso limite: un sigillato che arrivasse anche qui va ignorato (niente doppioni)
                data = dict(self.market())
                data["700"] = self.boxes()
                return data
            # senza filtro foil: carte + box (che non ha la proprietà foil)
            data = dict(self.market())
            data["700"] = self.boxes()
            return data
        raise AssertionError(path)

    def boxes(self):
        def box(pid, cents, qty):
            p = product(pid, 700, cents, qty=qty)
            p["properties_hash"] = {"mtg_language": "en"}   # niente condizione né foil
            return p
        if self.day == 0:
            return [box(8000, 9500, 3), box(8001, 9900, 1)]
        if self.day == 1:
            return [box(8000, 9500, 2)]                     # 1 venduto, 1 inserzione sparita
        return []

    def market(self):
        # Sheoldred (100): 25 inserzioni da 70€ a 94€, id 1000..1024
        base = [product(1000 + i, 100, 7000 + i * 100, qty=2) for i in range(25)]
        bulk = [product(5000, 200, 10, qty=50), product(5001, 200, 12, qty=10)]
        token = [product(9000, 300, 5, qty=99)]
        parked = [product(9500, 400, 1817016, qty=2)]   # prezzo segnaposto
        druid = [product(9600, 500, 1817016, qty=9)]    # segnaposto, stampa senza riferimento
        dwarves = [product(9700, 600, 3034, qty=13)]    # prezzo vero, riferimento di comodo
        if self.day == 0:
            return {"100": base, "200": bulk, "300": token, "400": parked,
                    "500": druid, "600": dwarves}
        if self.day == 1:
            s = [dict(p) for p in base]
            s[0] = product(1000, 100, 7000, qty=1)          # 1 venduta (2 -> 1)
            del s[1]                                         # sparita, economica -> uscita (2)
            s = [p for p in s if p["id"] != 1024]            # sparita, la più cara...
            s.append(product(1100, 100, 7050, qty=1))        # nuova inserzione
            s.append(product(1101, 100, 6500, qty=3, vac=True))  # in vacanza: no prezzo
            # lista piena: 25 elementi; la 1024 (94€) costava più della 25esima? no:
            # la 25esima ora è 1023 (93€) < 94€ -> 1024 ignorata (potrebbe essere scivolata fuori)
            s = sorted(s, key=lambda p: p["price"]["cents"])[:25]
            bulk2 = [product(5000, 200, 10, qty=45)]         # 5 vendute, 5001 sparita (10)
            return {"100": s, "200": bulk2, "300": token, "400": parked,
                    "500": druid, "600": dwarves}
        return {"100": [], "200": []}


def main():
    con = sqlite3.connect(":memory:")
    con.executescript(ct.SCHEMA)
    client = FakeClient()
    args = SimpleNamespace(only=None, limit=None, save_raw=False, raw_dir="raw")
    d0 = (date.today() - timedelta(days=1)).isoformat()
    d1 = date.today().isoformat()

    ct.run(client, con, args, today=d0, sleep=lambda s: None)
    client.day = 1
    ct.run(client, con, args, today=d1, sleep=lambda s: None)

    # solo l'espansione Magic deve essere elaborata
    assert not any(p and p.get("expansion_id") == 99 for _, p in client.calls)
    # blueprint sincronizzati una sola volta (refresh settimanale)
    assert sum(1 for c, _ in client.calls if c == "/blueprints/export") == 1

    r = con.execute("""SELECT sold_qty_est, removed_qty_est, new_qty, min_price, n_listings,
                              total_qty, prev_date FROM daily_stats
                       WHERE blueprint_id=100 AND date=?""", (d1,)).fetchone()
    print("Sheoldred giorno 2:", r)
    sold, removed, new, minp, n, qty, prev = r
    assert sold == 1, sold
    assert removed == 2, removed          # solo la 1001, la 1024 è ignorata
    assert new == 1 + 3, new              # 1100 + 1101 (in vacanza ma nuova offerta)
    assert minp == 70.0, minp             # la 65€ in vacanza non conta
    assert n == 24, n                     # 25 meno quella in vacanza
    assert prev == d0

    r = con.execute("""SELECT sold_qty_est, removed_qty_est, min_price FROM daily_stats
                       WHERE blueprint_id=200 AND date=?""", (d1,)).fetchone()
    print("Bulk giorno 2:", r)
    assert r == (5, 10, 0.1), r

    r = con.execute("""SELECT sold_qty_est FROM daily_stats
                       WHERE blueprint_id=100 AND date=?""", (d0,)).fetchone()
    assert r[0] is None                   # primo giorno: nessun confronto possibile

    # stessa giornata rieseguita: le stime già calcolate restano
    ct.run(client, con, args, today=d1, sleep=lambda s: None)
    r = con.execute("""SELECT sold_qty_est, removed_qty_est FROM daily_stats
                       WHERE blueprint_id=100 AND date=?""", (d1,)).fetchone()
    print("Rieseguito stesso giorno:", r)
    assert r == (1, 2), r

    # giorno 3: tutto sparito (liste vuote, non piene) -> tutte uscite
    client.day = 2
    d2 = (date.today() + timedelta(days=1)).isoformat()
    ct.run(client, con, args, today=d2, sleep=lambda s: None)
    r = con.execute("""SELECT removed_qty_est, n_listings, min_price FROM daily_stats
                       WHERE blueprint_id=200 AND date=?""", (d2,)).fetchone()
    print("Bulk giorno 3:", r)
    assert r == (45, 0, None), r

    # i token restano in catalogo ma fuori dallo storico e dallo stato corrente
    assert con.execute("SELECT COUNT(*) FROM blueprints WHERE id=300").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM daily_stats WHERE blueprint_id=300").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM listings_current WHERE blueprint_id=300").fetchone()[0] == 0
    print("Token esclusi dallo storico: ok")

    r = con.execute("""SELECT foil, sold_qty_est, removed_qty_est, min_price, total_qty
                       FROM daily_stats WHERE blueprint_id=700 AND date=?""", (d1,)).fetchall()
    print("Booster box giorno 2:", r)
    assert r == [(2, 1, 1, 95.0, 2)], r                     # salvato come sigillato (foil=2)
    assert con.execute("SELECT COUNT(*) FROM daily_stats WHERE blueprint_id=700 AND foil<2"
                       ).fetchone()[0] == 0
    # le carte non vengono duplicate dalla richiesta senza filtro
    assert con.execute("SELECT COUNT(*) FROM daily_stats WHERE foil=2 AND blueprint_id<>700"
                       ).fetchone()[0] == 0

    test_scryfall_e_anomalie(con, d1)
    test_archivio()

    print("\nTUTTI I TEST SUPERATI")


def test_scryfall_e_anomalie(con, day):
    import json, os, tempfile
    import scryfall_sync as sf
    import analisi as an

    cards = [
        {"id": "abc", "oracle_id": "o1", "name": "Sheoldred, the Apocalypse", "set": "dmu",
         "games": ["paper"], "digital": False, "reserved": False, "reprint": False,
         "prices": {"eur": "80.00", "eur_foil": "120.00", "usd": "85.00", "usd_foil": None},
         "edhrec_rank": 150, "legalities": {"commander": "legal", "standard": "not_legal"}},
        {"id": "def", "name": "Bulk Common", "set": "dmu", "games": ["paper"],
         "prices": {"eur": "0.01"}},                       # 10 volte, ma solo +0,09 euro
        {"id": "jkl", "name": "Polymorph", "set": "plst", "games": ["paper"],
         "prices": {"eur": "0.50"}},                       # segnaposto da 18.170 euro
        {"id": "w24", "name": "Druid of the Cowl", "games": ["paper"],
         "prices": {"eur": None}},                         # stampa senza prezzo Cardmarket
        {"id": "dotc-old", "name": "Druid of the Cowl", "games": ["paper"],
         "prices": {"eur": "0.30"}},                       # altra stampa: tetto 0,30
        {"id": "sld7", "name": "Seven Dwarves", "games": ["paper"],
         "prices": {"eur": "0.02"}},                       # riferimento di comodo
        {"id": "dwarves-foil", "name": "Seven Dwarves", "games": ["paper"],
         "prices": {"eur": "1.00", "eur_foil": "35.00"}},  # tetto 35
        {"id": "zzz", "name": "Solo Arena", "games": ["arena"], "digital": True,
         "prices": {}},                                    # solo digitale: ignorata
    ]
    # import dal file, come in uso reale (lettura in streaming con ijson)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "default-cards.json")
        with open(path, "w") as fh:          # formato attuale di Scryfall: una carta per riga
            fh.write("\n".join(json.dumps(c) for c in cards))
        con.executescript(sf.SCHEMA)
        with open(path, "rb") as fh:
            n = sf.import_cards(con, sf.iter_cards(fh), day)
    assert n == 7, n
    r = con.execute("SELECT eur, eur_foil, usd, edhrec_rank FROM scryfall_daily "
                    "WHERE scryfall_id='abc'").fetchone()
    assert r == (80.0, 120.0, 85.0, 150), r
    r = con.execute("SELECT reserved, legal_commander FROM scryfall_cards "
                    "WHERE scryfall_id='abc'").fetchone()
    assert r == (0, "legal"), r

    an.prepare_reference(con)
    anomale = {row[0] for row in con.execute(
        f"""SELECT b.id FROM daily_stats d JOIN blueprints b ON b.id=d.blueprint_id
            {an.ref_join()} WHERE d.date=? AND {an.anomalo_expr(an.DEFAULT_SOGLIA)}""",
        (day, day))}
    print("Anomalie trovate:", anomale)
    # i due segnaposto sì (uno grazie al tetto della carta); Sheoldred, la comune da pochi
    # centesimi e Seven Dwarves (riferimento di comodo 0,02 ignorato) no
    assert anomale == {400, 500}, anomale




class FakeGitHub:
    """Release di GitHub simulate in una cartella: risponde come il comando gh."""
    def __init__(self, root):
        import os
        self.root = root
        self.os = os

    def __call__(self, *args, check=True):
        import json, os, shutil
        from types import SimpleNamespace as NS
        a = list(args)
        assert a[0] == "release", a
        verb, rest = a[1], a[2:]
        def opt(name):
            return rest[rest.index(name) + 1] if name in rest else None
        def ok(out=""):
            return NS(returncode=0, stdout=out, stderr="")
        tagdir = lambda t: os.path.join(self.root, t)
        if verb == "list":
            return ok(json.dumps([{"tagName": t} for t in sorted(os.listdir(self.root))]))
        tag = rest[0]
        if verb == "view":
            if not os.path.isdir(tagdir(tag)):
                if check:
                    raise RuntimeError("release not found")
                return NS(returncode=1, stdout="", stderr="release not found")
            return ok(json.dumps({"assets": [{"name": n} for n in os.listdir(tagdir(tag))]}))
        if verb == "create":
            os.makedirs(tagdir(tag))
            return ok()
        if verb == "upload":
            assert "--clobber" in rest
            shutil.copy(rest[1], tagdir(tag))
            return ok()
        if verb == "download":
            dest, pattern = opt("--dir"), opt("--pattern")
            os.makedirs(dest, exist_ok=True)
            for n in os.listdir(tagdir(tag)):
                if pattern and n != pattern:
                    continue
                target = os.path.join(dest, n)
                if os.path.exists(target) and "--skip-existing" in rest:
                    continue
                shutil.copy(os.path.join(tagdir(tag), n), target)
            return ok()
        raise AssertionError(a)


def test_archivio():
    import os, sqlite3, tempfile
    from datetime import date, timedelta
    from types import SimpleNamespace
    import archivio

    d0 = (date.today() - timedelta(days=1)).isoformat()
    d1 = date.today().isoformat()
    d2 = (date.today() + timedelta(days=1)).isoformat()
    noargs = SimpleNamespace(only=None, limit=None, save_raw=False, raw_dir="raw")
    with tempfile.TemporaryDirectory() as tmp:
        remote = os.path.join(tmp, "github")
        os.makedirs(remote)
        archivio.gh = FakeGitHub(remote)
        repo = "faso01/mtg_tracker_dati"

        # 1) PC: due giorni di raccolta, poi primo invio di tutto lo storico
        pc_db = os.path.join(tmp, "pc.db")
        con = ct.open_db(pc_db)
        client = FakeClient()
        ct.run(client, con, noargs, today=d0, sleep=lambda s: None)
        client.day = 1
        ct.run(client, con, noargs, today=d1, sleep=lambda s: None)
        pc_rows = con.execute("SELECT COUNT(*) FROM daily_stats").fetchone()[0]
        con.close()
        archivio.cmd_pubblica(SimpleNamespace(repo=repo, db=pc_db, tutte_le_date=True))
        assert "stato" in os.listdir(remote)
        assert sqlite3.connect(pc_db).execute("SELECT COUNT(*) FROM daily_stats"
                                              ).fetchone()[0] == pc_rows   # PC non toccato

        # 2) Actions, giorno dopo: scarica lo stato, raccoglie, pubblica
        ci_db = os.path.join(tmp, "ci.db")
        archivio.cmd_scarica_stato(SimpleNamespace(repo=repo, db=ci_db))
        con = sqlite3.connect(ci_db)
        assert [r[0] for r in con.execute("SELECT DISTINCT date FROM daily_stats")] == [d1]
        client.day = 1                        # stesse inserzioni di ieri, tranne una vendita
        ct.run(_OneMoreSale(client), con, noargs, today=d2, sleep=lambda s: None)
        r = con.execute("SELECT sold_qty_est, prev_date FROM daily_stats "
                        "WHERE blueprint_id=100 AND date=?", (d2,)).fetchone()
        assert r == (1, d1), r                # confronto con la fotografia dello stato
        con.close()
        archivio.cmd_pubblica(SimpleNamespace(repo=repo, db=ci_db, tutte_le_date=False))

        # 3) PC: aggiorna e ricostruisce il database completo
        archivio.cmd_aggiorna(SimpleNamespace(repo=repo, db=pc_db, dir=os.path.join(tmp, "dati")))
        con = sqlite3.connect(pc_db)
        days = [r[0] for r in con.execute("SELECT DISTINCT date FROM daily_stats ORDER BY date")]
        assert days == [d0, d1, d2], days
        same = con.execute("SELECT sold_qty_est, removed_qty_est FROM daily_stats "
                           "WHERE blueprint_id=100 AND date=?", (d1,)).fetchone()
        assert same == (1, 2), same           # i valori sopravvivono al passaggio in CSV
        assert con.execute("SELECT COUNT(*) FROM blueprints").fetchone()[0] > 0
        assert os.path.exists(os.path.join(tmp, "pc_precedente.db"))
        # il PC usa il catalogo leggero: niente fotografia delle inserzioni
        assert os.path.exists(os.path.join(tmp, "dati", "catalogo.db.gz"))
        assert not os.path.exists(os.path.join(tmp, "dati", "stato.db.gz"))
        assert con.execute("SELECT COUNT(*) FROM listings_current").fetchone()[0] == 0
        con.close()
    print("Archivio (pubblica, scarica-stato, aggiorna): ok")


class _OneMoreSale:
    """Come FakeClient, ma una copia di Sheoldred in meno rispetto al giorno prima."""
    def __init__(self, inner):
        self.inner = inner

    def get(self, path, params=None):
        data = self.inner.get(path, params)
        if path == "/marketplace/products" and params.get("foil") == "false":
            data = dict(data)
            data["100"] = [dict(p) for p in data["100"]]
            for p in data["100"]:
                if p["id"] == 1002:
                    p["quantity"] -= 1
        return data


if __name__ == "__main__":
    main()
