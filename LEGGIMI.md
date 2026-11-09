# Raccoglitore prezzi CardTrader (Magic)

File principali:
- `ct_collector.py` – da eseguire una volta al giorno: scarica le inserzioni e salva le statistiche in `cardtrader.db`
- `scryfall_sync.py` – subito dopo: prezzi di riferimento Cardmarket, popolarità EDHREC e dati delle carte da Scryfall
- `esegui_raccolta.bat` – lancia i due script in sequenza e scrive tutto in `collector.log` (Windows)
- `analisi.py` – per consultare i dati raccolti
- `test_collector.py` – verifica la logica con dati finti (non serve internet)

## 1. Installazione

Serve Python 3.9 o superiore.

```
pip install -r requirements.txt
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

La raccolta CardTrader completa (circa 800 espansioni) dura circa 2 ore e mezza; Scryfall
aggiunge qualche minuto. Il computer deve restare acceso per tutto il tempo.

**Windows** – Utilità di pianificazione → Crea attività di base → Ogni giorno, sempre alla
stessa ora → Avvia programma: il percorso completo di `esegui_raccolta.bat`, argomenti vuoti.
Nelle Impostazioni dell'attività spunta "Esegui l'attività appena possibile dopo l'omissione
di un avvio pianificato". Per seguire l'avanzamento, in PowerShell:
`Get-Content collector.log -Wait -Tail 20`.

Codice finale nel registro: 0 = tutto ok, 2 = alcune espansioni fallite, 1 = nessuna riuscita.

**Mac / Linux** – `crontab -e` e aggiungi:
```
0 6 * * * cd /percorso/cartella && python3 ct_collector.py >> collector.log 2>&1; python3 scryfall_sync.py >> collector.log 2>&1
```

Se il PC non è sempre acceso, un Raspberry Pi o un piccolo server da pochi euro al mese
sono la soluzione più comoda. Un giorno saltato non rompe nulla: il confronto viene fatto
con l'ultima lettura disponibile (colonna `prev_date`).

## 5. Consultare i dati

```
python analisi.py carta "Ragavan" --giorni 60
python analisi.py carta "Sheoldred" --id 220466 --foil
python analisi.py movimenti --giorni 7 --min-prezzo 5
python analisi.py panoramica                     # quadro d'insieme dell'ultima raccolta
python analisi.py anomalie                       # prezzi fuori mercato esclusi dalle analisi
python analisi.py movimenti --sigillati          # classifica di box, bundle, booster
python analisi.py carta "Sheoldred" --tutte      # include token, playmat, accessori
```

Di default l'analisi mostra solo le carte singole.

### Cosa finisce nello storico

Il catalogo contiene tutto, ma lo storico giornaliero (`daily_stats`) viene salvato solo per
carte singole e prodotti sigillati: Boosters, Booster Boxes, Bundles and Fat Packs,
Complete Sets, Tournament Prerelease Packs, Cases & Displays. Token, playmat, dadi, bustine,
deck box, album e imballaggi sono esclusi. L'elenco è in `TRACKED_CATEGORIES` all'inizio di
`ct_collector.py`; per salvare tutto si usa `--all-categories`.

### Prezzi anomali

Alcuni venditori mettono prezzi "parcheggio" assurdi (es. 18.170 € per una carta da 50
centesimi). Per riconoscerli, il minimo CardTrader viene confrontato con il prezzo di
riferimento Cardmarket preso da Scryfall: se è oltre **5 volte** il riferimento e di almeno
**20 €**, la stampa è considerata anomala ed esclusa da `panoramica` e `movimenti`.
La soglia si cambia con `--soglia`, es. `--soglia 10`. Senza dati Scryfall nulla viene filtrato.

## Come leggere i numeri

- **vendute**: copie scalate da un'inserzione ancora presente. Sono quasi sempre vendite reali.
- **uscite**: inserzioni sparite. Vendute *oppure* ritirate dal venditore: segnale più rumoroso.
- **nuove**: nuove inserzioni o rifornimenti. Se crescono più delle vendite, l'offerta aumenta.

## Struttura dei dati

### Da dove arrivano

| Richiesta API | Quando | Campi letti |
| --- | --- | --- |
| `/categories` | ogni esecuzione | id, nome |
| `/expansions` | ogni esecuzione | id, codice, nome |
| `/blueprints/export` | una volta a settimana per set | id, nome, versione, espansione, categoria, scryfall_id, cardmarket_id, tcgplayer_id |
| `/marketplace/products` | ogni giorno, 2 chiamate per set (non foil e foil) | id inserzione, stampa, prezzo, quantità, condizione, lingua, foil, CardTrader Zero, vacanza, gradata |

Nome del venditore, descrizioni e immagini non vengono salvati.

### Percorso

```
API CardTrader
 ├─ catalogo (categorie, espansioni, blueprint) ──► anagrafica: categories, expansions, blueprints
 └─ inserzioni di oggi ──► confronto con ieri ◄──► listings_current (fotografia di ieri)
                                  │
                                  ▼
                            daily_stats (storico) ──► analisi.py
                                                            ▲
Scryfall (Default Cards) ──► scryfall_daily, scryfall_cards ┘   (collegati tramite scryfall_id)
```

### Tabelle del database (`cardtrader.db`)

| Tabella | Una riga è | Cresce? |
| --- | --- | --- |
| `categories` | un tipo di oggetto (carta singola, token, sigillato…) | no |
| `expansions` | un set | di poco |
| `blueprints` | una stampa (es. Sheoldred DMU normale) | di poco |
| `listings_current` | un'inserzione vista nell'ultima lettura | no, viene sostituita a ogni lettura |
| `daily_stats` | una stampa in un giorno, foil o non foil | sì, ogni giorno: è lo storico |
| `runs` | un'esecuzione dello script | sì, una al giorno |
| `scryfall_daily` | una stampa in un giorno: prezzi Cardmarket (eur, eur_foil), TCGplayer (usd, usd_foil), `edhrec_rank` | sì, ogni giorno |
| `scryfall_cards` | una stampa: nome, set, uscita, rarità, `reserved` (Reserved List), `reprint`, legalità nei formati | di poco |
| `scryfall_runs` | un'esecuzione di `scryfall_sync.py` | sì, una al giorno |

`edhrec_rank` è la posizione della carta nella popolarità Commander (1 = la più giocata): seguita nel
tempo è il segnale di domanda. `oracle_id` è uguale per tutte le ristampe della stessa carta e serve a
contare quante volte è stata ristampata.

Colonne di `daily_stats`, calcolate sulle 25 inserzioni più economiche
(venditori in vacanza e carte gradate esclusi dai prezzi):

| Colonna | Significato |
| --- | --- |
| `date`, `blueprint_id`, `foil` | chiave: giorno, stampa, finitura |
| `min_price` | prezzo minimo, qualsiasi condizione e lingua |
| `min_price_nm` | minimo Near Mint o Mint |
| `min_price_nm_en` | minimo Near Mint inglese: il confronto più omogeneo nel tempo |
| `median_price` | prezzo centrale delle 25 |
| `ct0_min_price` | minimo acquistabile via CardTrader Zero |
| `n_listings`, `total_qty` | inserzioni e copie disponibili |
| `sold_qty_est` | copie scalate da inserzioni ancora presenti: vendite quasi certe |
| `removed_qty_est` | copie da inserzioni sparite: vendute o ritirate |
| `new_qty` | copie apparse: nuova offerta |
| `prev_date` | giorno della lettura usata per il confronto (utile se un giorno è saltato) |

### Perché tenere i campi "di contorno"

| Campo | Serve per |
| --- | --- |
| `scryfall_id` | collegare EDHREC, ristampe passate e legalità nei formati |
| `cardmarket_id`, `tcg_player_id` | confrontare con Cardmarket e il mercato americano |
| `category_id` | separare carte singole da token, sigillati e accessori |
| `version` | non mescolare stampa base e varianti (borderless, showcase…) |
| id inserzione | riconoscere la stessa inserzione da un giorno all'altro e stimare le vendite |
| condizione, lingua, foil | confrontare copie equivalenti |
| CardTrader Zero | sapere il costo d'acquisto reale |
| vacanza, gradata | escludere prezzi di copie non acquistabili o fuori mercato |

Uno storico non registrato oggi non si recupera più; un campo inutile si ignora in fase di analisi.

Per interrogare il database direttamente (es. con DB Browser for SQLite), la tabella da cui partire è
`daily_stats`, unita a `blueprints` per avere nomi e set:

```sql
SELECT d.date, b.name, d.min_price_nm_en, d.sold_qty_est
FROM daily_stats d JOIN blueprints b ON b.id = d.blueprint_id
WHERE b.name = 'Sheoldred, the Apocalypse' AND d.foil = 0
ORDER BY d.date;
```

## Limiti da tenere a mente

- L'API mostra solo le **25 inserzioni più economiche** per stampa. Le carte con centinaia di
  copie in vendita avranno stime di vendita sottostimate; per quelle conta di più l'andamento
  nel tempo che il numero assoluto.
- Lingue e condizioni sono mescolate nelle 25: usa `min NM EN` per confronti coerenti.
- I prezzi sono nella valuta del tuo account (euro).
- I prezzi Scryfall vengono aggiornati circa una volta al giorno: vanno bene per tendenze e
  confronti, non per il prezzo esatto del momento.
- Il database cresce di qualche decina di MB al giorno. `--save-raw` salva anche le risposte
  complete (molto più spazio) se in futuro vuoi ricalcolare statistiche diverse.
