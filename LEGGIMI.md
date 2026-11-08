# Raccoglitore prezzi CardTrader (Magic)

Tre file:
- `ct_collector.py` – da eseguire una volta al giorno: scarica le inserzioni e salva le statistiche in `cardtrader.db`
- `analisi.py` – per consultare i dati raccolti
- `test_collector.py` – verifica la logica con dati finti (non serve internet)

## 1. Installazione

Serve Python 3.9 o superiore.

```
pip install requests
python test_collector.py        # deve stampare "TUTTI I TEST SUPERATI"
```

## 2. Token CardTrader

Su cardtrader.com: profilo → Impostazioni → sezione API, copia il token.
Salvalo in un file `cardtrader_token.txt` nella stessa cartella degli script
(una sola riga, solo il token). Non condividerlo: dà accesso al tuo account.

```
python ct_collector.py --test-token
```

## 3. Prima prova

```
python ct_collector.py --only dmu,mh3      # solo due espansioni, un paio di minuti
python analisi.py stato
python analisi.py carta "Sheoldred, the Apocalypse"
```

Le colonne "vendute" e "uscite" saranno vuote finché non esegui lo script un secondo giorno:
servono due letture da confrontare.

## 4. Esecuzione giornaliera

L'esecuzione completa dura circa 30-40 minuti (l'API permette 1 richiesta al secondo).
Il computer deve essere acceso a quell'ora.

**Windows** – Utilità di pianificazione → Crea attività di base → Ogni giorno, es. ore 6:00 →
Avvia programma: `python`, argomenti: `ct_collector.py`, "Inizia in": la cartella degli script.

**Mac / Linux** – `crontab -e` e aggiungi:
```
0 6 * * * cd /percorso/cartella && /usr/bin/python3 ct_collector.py >> collector.log 2>&1
```

Se il PC non è sempre acceso, un Raspberry Pi o un piccolo server da pochi euro al mese
sono la soluzione più comoda. Un giorno saltato non rompe nulla: il confronto viene fatto
con l'ultima lettura disponibile (colonna `prev_date`).

## 5. Consultare i dati

```
python analisi.py carta "Ragavan" --giorni 60
python analisi.py carta "Sheoldred" --id 220466 --foil
python analisi.py movimenti --giorni 7 --min-prezzo 5
```

## Come leggere i numeri

- **vendute**: copie scalate da un'inserzione ancora presente. Sono quasi sempre vendite reali.
- **uscite**: inserzioni sparite. Vendute *oppure* ritirate dal venditore: segnale più rumoroso.
- **nuove**: nuove inserzioni o rifornimenti. Se crescono più delle vendite, l'offerta aumenta.

## Limiti da tenere a mente

- L'API mostra solo le **25 inserzioni più economiche** per stampa. Le carte con centinaia di
  copie in vendita avranno stime di vendita sottostimate; per quelle conta di più l'andamento
  nel tempo che il numero assoluto.
- Lingue e condizioni sono mescolate nelle 25: usa `min NM EN` per confronti coerenti.
- I prezzi sono nella valuta del tuo account (euro).
- Il database cresce di qualche decina di MB al giorno. `--save-raw` salva anche le risposte
  complete (molto più spazio) se in futuro vuoi ricalcolare statistiche diverse.
