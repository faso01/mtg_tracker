# mtg_tracker

Raccolta giornaliera dei prezzi Magic: The Gathering da CardTrader, con stima delle vendite,
per individuare le carte con domanda in crescita.

Istruzioni complete in [LEGGIMI.md](LEGGIMI.md).

```
pip install -r requirements.txt
python test_collector.py
python ct_collector.py --only dmu,mh3
python analisi.py stato
```
