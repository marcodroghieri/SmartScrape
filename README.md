# SmartScrape – Google Shopping Monitor

![CI](https://github.com/marcodroghieri/SmartScrape/actions/workflows/ci.yml/badge.svg)

> Corso di Sistemi Paralleli e Distribuiti
> Anno Accademico: 2025/2026
> Docente: Francesco Sportolari
> Studente: Marco Droghieri – 368956
> Studente: Samuele Vitali – 373100

## Indice

1. [Descrizione del progetto](#-descrizione-del-progetto)
2. [A cosa serve](#-a-cosa-serve)
3. [Architettura](#-architettura)
4. [Stack tecnologico](#-stack-tecnologico)
5. [Flusso dei dati](#-flusso-dei-dati)
6. [Struttura del repository](#-struttura-del-repository)
7. [Modello dati](#-modello-dati)
8. [API esposte dal Master](#-api-esposte-dal-master)
9. [Guida all'installazione e all'uso](#-guida-allinstallazione-e-alluso)
10. [Interfaccia utente](#-interfaccia-utente)
11. [Scelte di design e come è stato costruito](#-scelte-di-design-e-come-è-stato-costruito)
12. [Limiti noti](#-limiti-noti)
13. [Possibili sviluppi futuri](#-possibili-sviluppi-futuri)

---

## 📌 Descrizione del progetto

**SmartScrape** è un'architettura distribuita basata sul modello **Master-Worker** per il monitoraggio asincrono dei prezzi su **Google Shopping**. L'utente inserisce un termine di ricerca (es. "rtx 5090") da un'interfaccia web; il sistema interroga Google Shopping in background, pulisce e normalizza i dati grezzi (prezzi, valutazioni, recensioni) e li rende consultabili in tempo reale, ordinabili per prezzo, valutazione o numero di recensioni.

Il progetto nasce come esercitazione per il corso di *Sistemi Paralleli e Distribuiti* con l'obiettivo di applicare concretamente i concetti di **disaccoppiamento tra produttore e consumatore**, **coda di messaggi distribuita** e **stato condiviso tra processi indipendenti**, evitando al tempo stesso i problemi legali/tecnici dello scraping diretto delle pagine HTML di Google (blocchi anti-bot, CAPTCHA, markup instabile).

## 🎯 A cosa serve

- Confrontare rapidamente i prezzi di un prodotto tra più negozi/venditori indicizzati da Google Shopping.
- Osservare come uno stesso prodotto viene proposto da fonti diverse, con relativa valutazione e numero di recensioni, per farsi un'idea di affidabilità oltre che di prezzo.
- Fungere da base didattica dimostrativa per un'architettura **Master-Worker disaccoppiata tramite coda**, estendibile con più worker concorrenti senza modificare la logica applicativa.

Non è pensato per un uso in produzione così com'è (vedi [Limiti noti](#-limiti-noti)), ma l'architettura è già pronta per scalare orizzontalmente aggiungendo istanze di `worker.py`.

## 🏗️ Architettura

```
┌──────────────┐        HTTP         ┌──────────────┐
│   Browser    │ ◄─────────────────► │ Master        │
│ (index.html) │   /avvia-scraping   │ (Flask)       │
└──────────────┘   /stato-scraping   └──────┬────────┘
                                             │
                          push query   │ leggi/scrivi stato
                                             ▼
                                     ┌──────────────┐
                                     │    Redis      │
                                     │ coda_prezzi   │  (broker FIFO)
                                     │ stato_scraping│  (stato condiviso)
                                     └──────┬────────┘
                                             │ brpop (blocking)
                                             ▼
                                     ┌──────────────┐
                                     │   Worker      │
                                     │ (worker.py)   │
                                     └──────┬────────┘
                                             │ query
                                             ▼
                                     ┌──────────────┐
                                     │   SerpApi     │
                                     │ Google        │
                                     │ Shopping API  │
                                     └──────┬────────┘
                                             │ pulizia + upsert
                                             ▼
                                     ┌──────────────┐
                                     │ PostgreSQL    │
                                     │ monitoraggio_ │
                                     │ prezzi        │
                                     └──────────────┘
```

Il **Master** e il **Worker** sono due processi Python completamente indipendenti: non comunicano mai direttamente tra loro, ma solo attraverso **Redis**, che funge sia da message broker (coda FIFO `coda_prezzi`) sia da stato condiviso distribuito (chiave `stato_scraping`, letta dal Master per informare il frontend e scritta dal Worker a fine elaborazione). Questo disaccoppiamento è ciò che rende l'architettura tollerante ai guasti e scalabile: si possono avviare più istanze di `worker.py` in parallelo (anche su macchine diverse) senza toccare una riga di codice, perché Redis distribuisce automaticamente i task tra i consumer in ascolto sulla stessa coda.

## 🛠️ Stack tecnologico

| Livello | Tecnologia | Ruolo |
|---|---|---|
| Web server / orchestrazione | **Flask 3.0** | Espone l'interfaccia utente e le API REST |
| Message broker + stato condiviso | **Redis** (`redis:alpine`) | Coda FIFO dei task e stato distribuito del job |
| Persistenza | **PostgreSQL 16** | Storicizza i prodotti trovati con upsert su `(modello, negozio)` |
| Reperimento dati | **SerpApi** (`google-search-results`) | Wrapper ufficiale per l'API di Google Shopping, evita scraping diretto |
| Containerizzazione | **Docker / Docker Compose** | Isola Redis e PostgreSQL dall'ambiente host |
| Frontend | HTML + CSS + JavaScript vanilla | Polling asincrono dello stato, nessun framework |
| Driver DB | `psycopg2-binary` | Comunicazione Python ↔ PostgreSQL |

## 🔄 Flusso dei dati

1. L'utente digita un termine di ricerca nell'interfaccia e preme **CERCA**.
2. Il **Master** (`POST /avvia-scraping`) valida la richiesta, rifiuta la richiesta con `409` se uno scraping è già `in_corso`, poi:
   - svuota e ricrea la tabella `monitoraggio_prezzi` (nuova ricerca = nuovo dataset);
   - svuota la coda Redis da eventuali residui;
   - imposta `stato_scraping = in_corso`;
   - inserisce la query nella coda `coda_prezzi` (`LPUSH`).
3. Il **Worker**, in ascolto continuo con `BRPOP` (bloccante, timeout 5s), preleva la query.
4. Il Worker interroga **SerpApi** (`engine=google_shopping`, locale `it/it`) e riceve i risultati grezzi.
5. Ogni prodotto viene **pulito e normalizzato**:
   - `pulisci_prezzo`: gestisce formati italiani (`1.234,56 €`) e range di prezzo (`199 - 249 €` → tiene il primo valore);
   - `pulisci_stelle`: converte il rating in `float`, con fallback a `0.0`;
   - `pulisci_recensioni`: converte notazioni come `1,2K` in numero intero (`1200`).
6. I prodotti validi vengono salvati in PostgreSQL con una `INSERT ... ON CONFLICT (modello, negozio) DO UPDATE`, così una stessa coppia prodotto/negozio viene aggiornata invece che duplicata.
7. Quando la coda resta vuota per oltre 5 secondi e lo stato era `in_corso`, il Worker imposta `stato_scraping = completato`.
8. Il frontend, che nel frattempo esegue polling su `GET /stato-scraping` ogni 2 secondi, rileva il completamento e ricarica automaticamente la pagina mostrando i risultati ordinati.

## 📂 Struttura del repository

```
SmartScrape/
├── master.py              # Web server Flask: UI, API, inizializzazione DB, gestione coda
├── worker.py               # Processo demone: consuma la coda, chiama SerpApi, pulisce e salva i dati
├── requirements.txt         # Dipendenze Python
├── docker-compose.yml       # Servizi Redis + PostgreSQL containerizzati
├── .env.example              # Template della variabile SERPAPI_KEY (nessun segreto reale)
├── .env                       # Chiave SerpApi reale, locale, MAI committato (creato da te)
├── templates/
│   └── index.html           # Interfaccia utente (Jinja2 + JS per il polling)
├── static/
│   └── style.css             # Tema dark dell'interfaccia
└── README.md
```

## 🗄️ Modello dati

Tabella `monitoraggio_prezzi` (PostgreSQL), ricreata da zero ad ogni nuova ricerca da `inizializza_db()`:

| Colonna | Tipo | Note |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | |
| `modello` | `VARCHAR(512) NOT NULL` | Titolo del prodotto |
| `prezzo` | `NUMERIC(10,2)` | Prezzo pulito in euro |
| `negozio` | `VARCHAR(100) NOT NULL` | Venditore/negozio (`source` di Google Shopping) |
| `stelle` | `NUMERIC(2,1) DEFAULT 0.0` | Valutazione media, `0.0` se assente |
| `recensioni` | `INTEGER DEFAULT 0` | Numero di recensioni |
| `link` | `VARCHAR(2048)` | URL al prodotto (fallback a una ricerca Google Shopping se assente) |
| `data_rilevazione` | `TIMESTAMP DEFAULT NOW()` | Aggiornato ad ogni upsert |

Vincolo `UNIQUE (modello, negozio)` usato come chiave del conflitto nell'`UPSERT` — evita duplicati quando lo stesso prodotto/negozio ricompare in ricerche successive.

## 🔌 API esposte dal Master

| Metodo | Endpoint | Descrizione | Risposta |
|---|---|---|---|
| `GET` | `/` | Pagina principale, supporta `?ordina=prezzo\|stelle\|recensioni` | HTML |
| `POST` | `/avvia-scraping` | Avvia una nuova ricerca. Body: `{"query": "..."}` | `200 {"status":"avviato","query":...}` · `400` se query vuota · `409` se uno scraping è già in corso |
| `GET` | `/stato-scraping` | Stato del job corrente, interrogato dal frontend ogni 2s | `{"finito": bool, "rimanenti": int, "stato": "inattivo\|in_corso\|completato"}` |

## 🚀 Guida all'installazione e all'uso

### 1. Prerequisiti

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [Python 3.8+](https://www.python.org/)
- Una chiave [SerpApi](https://serpapi.com/) (il piano gratuito include 100 ricerche/mese, sufficienti per il testing)

### 2. Configurazione della chiave API

`worker.py` usa SerpApi per interrogare Google Shopping senza subire blocchi anti-bot. La chiave viene letta da una variabile d'ambiente, caricata automaticamente da un file `.env` locale (mai tracciato da git):

1. Registrati su [serpapi.com](https://serpapi.com/) e copia la chiave dalla Dashboard ("Your Private API Key").
2. Copia il template e crea il tuo `.env`:
   ```bash
   copy .env.example .env      # Su Windows
   # cp .env.example .env       # Su macOS/Linux
   ```
3. Apri `.env` e incolla la tua chiave al posto del placeholder:
   ```
   SERPAPI_KEY=IL_TUO_TOKEN_SERPAPI
   ```
   > `.env` è nel `.gitignore`: la tua chiave resta solo sulla tua macchina e non finisce mai in un commit.

### 3. Avvio dell'infrastruttura (Redis + PostgreSQL)

```bash
docker-compose up -d
```

Redis viene esposto sulla porta `6379`, PostgreSQL sulla `5433` (per non entrare in conflitto con un'eventuale istanza locale sulla `5432` di default). L'immagine PostgreSQL è fissata alla versione `16` (non `latest`): le major version più recenti hanno cambiato il layout della directory dati e romperebbero il mount configurato in `docker-compose.yml`.

### 4. Installazione delle dipendenze Python

```bash
python -m venv venv
venv\Scripts\activate      # Su Windows
# source venv/bin/activate  # Su macOS/Linux

pip install -r requirements.txt
```

### 5. Avvio dell'applicazione

Servono due processi separati, in due terminali distinti:

**Terminale 1 – Worker** (resta in ascolto sulla coda Redis):
```bash
python worker.py
```

**Terminale 2 – Master** (avvia il server web):
```bash
python master.py
```

Apri il browser su **http://localhost:5000**.

### 6. Arresto

```bash
# Ferma i due processi Python con Ctrl+C nei rispettivi terminali, poi:
docker-compose stop     # ferma i container mantenendo i dati
docker-compose down     # ferma i container e rimuove anche la rete (i dati restano nel volume)
```

## 🖥️ Interfaccia utente

- **Campo di ricerca + pulsante CERCA**: invia la query al Master, disabilita i controlli e mostra un messaggio di caricamento finché il Worker non ha finito.
- **Menu "Ordina per"**: ricarica la pagina con l'ordinamento scelto (prezzo, stelle, recensioni) senza rieseguire lo scraping.
- **Tabella risultati**: modello (link cliccabile al prodotto), prezzo, negozio, stelle (o `N/D` se assenti), numero recensioni.
- **Persistenza dello stato di caricamento**: se la pagina viene ricaricata mentre uno scraping è in corso, `localStorage` lo ricorda e riattiva automaticamente il polling.

## 🧩 Scelte di design e come è stato costruito

- **SerpApi invece di scraping diretto**: interrogare direttamente le pagine HTML di Google Shopping è fragile (markup che cambia, CAPTCHA, blocchi IP) e in una zona grigia dal punto di vista dei termini di servizio. SerpApi fornisce un'API strutturata e stabile, permettendo al progetto di concentrarsi sulla parte distribuita/architetturale anziché sul web scraping in sé.
- **Redis come broker *e* come stato condiviso**: invece di introdurre un secondo sistema (es. un message broker dedicato + un DB per lo stato), Redis copre entrambi i ruoli con strutture dati semplici (una lista per la coda, una stringa per lo stato), riducendo la complessità operativa per un progetto didattico.
- **`ON CONFLICT DO UPDATE` invece di semplici `INSERT`**: ricerche ripetute sullo stesso termine aggiornano i prezzi esistenti invece di accumulare righe duplicate, mantenendo la tabella pulita.
- **Guardia di concorrenza sul Master**: `/avvia-scraping` controlla lo stato in Redis prima di accettare una nuova richiesta e risponde `409` se uno scraping è già `in_corso`. Senza questo controllo, due richieste ravvicinate potrebbero far ripartire `DROP TABLE` mentre il Worker sta ancora scrivendo la ricerca precedente.
- **Pulizia dati robusta**: le funzioni `pulisci_*` gestiscono esplicitamente le peculiarità del formato italiano (virgola come separatore decimale, punto come separatore delle migliaia), le notazioni abbreviate (`1,2K` recensioni) e i range di prezzo, per evitare che un formato inatteso finisca in un `NUMERIC` con un valore sbagliato invece di essere scartato.
- **Versione di PostgreSQL fissata**: l'immagine è pinnata a `postgres:16` invece di `latest` proprio perché durante lo sviluppo un aggiornamento automatico a una major version successiva (che ha cambiato il path dei dati nel container) ha impedito l'avvio del database — un promemoria concreto del perché **non** si dovrebbe mai usare `latest` per un'immagine stateful in un progetto che deve restare riproducibile.

## ⚠️ Limiti noti

- **Stato globale singolo**: `stato_scraping` è una singola chiave Redis condivisa da tutti gli utenti collegati al Master. Con più utenti simultanei, uno vedrebbe lo stato di ricerca di un altro. Adeguato per una demo/singolo utente, non per un deployment multi-tenant.
- **Reset del dataset ad ogni ricerca**: ogni nuova ricerca cancella e ricrea l'intera tabella (`DROP TABLE` in `inizializza_db()`); non c'è uno storico di ricerche precedenti.
- **`DB_CONFIG` non usa variabili d'ambiente**: a differenza di `SERPAPI_KEY`, i parametri di connessione a PostgreSQL restano hardcoded in `master.py`/`worker.py`. Non è un segreto (l'autenticazione è `trust` in locale), ma non è nemmeno configurabile senza modificare il codice.
- **Server di sviluppo Flask**: `master.py` gira con `debug=True` e il server integrato di Flask, non adatto alla produzione (vedi il warning stampato all'avvio).
- **Piano gratuito SerpApi**: limitato a 100 ricerche/mese; oltre la soglia le richieste falliscono (gestito con un log di errore, non con un crash).

## 🔮 Possibili sviluppi futuri

- Spostare anche `DB_CONFIG` su variabili d'ambiente (`SERPAPI_KEY` lo è già, vedi `.env.example`).
- Storico dei prezzi nel tempo (grafico per prodotto) invece del reset ad ogni ricerca.
- Stato di scraping per sessione/utente invece che globale.
- Containerizzare anche Master e Worker in `docker-compose.yml` per un avvio a comando singolo.
- Pool di più worker concorrenti per parallelizzare ricerche multiple.
