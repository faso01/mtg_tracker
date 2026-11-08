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
            return [{"id": 1, "name": "Magic Single Card", "game_id": 1}]
        if path == "/expansions":
            return [{"id": 10, "game_id": 1, "code": "dmu", "name": "Dominaria United"},
                    {"id": 99, "game_id": 5, "code": "xyz", "name": "Pokemon set"}]
        if path == "/blueprints/export":
            return [{"id": 100, "name": "Sheoldred, the Apocalypse", "version": None,
                     "expansion_id": 10, "category_id": 1, "scryfall_id": "abc",
                     "card_market_ids": [1], "tcg_player_id": None},
                    {"id": 200, "name": "Bulk Common", "version": None, "expansion_id": 10,
                     "category_id": 1, "scryfall_id": "def", "card_market_ids": [],
                     "tcg_player_id": None}]
        if path == "/marketplace/products":
            if params["foil"] == "true":
                return {}
            return self.market()
        raise AssertionError(path)

    def market(self):
        # Sheoldred (100): 25 inserzioni da 70€ a 94€, id 1000..1024
        base = [product(1000 + i, 100, 7000 + i * 100, qty=2) for i in range(25)]
        bulk = [product(5000, 200, 10, qty=50), product(5001, 200, 12, qty=10)]
        if self.day == 0:
            return {"100": base, "200": bulk}
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
            return {"100": s, "200": bulk2}
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

    print("\nTUTTI I TEST SUPERATI")


if __name__ == "__main__":
    main()
