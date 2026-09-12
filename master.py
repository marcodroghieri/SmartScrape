import redis
from flask import Flask, render_template, jsonify, request
import psycopg2

app = Flask(__name__)

DB_CONFIG = {
    "host": "localhost",
    "database": "smartscrape",
    "user": "admin",
    "port": "5433"
}

class bcolors:  #ci aiutera a distinguere i log dal terminale
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

r = redis.Redis(host='localhost', port=6379, db=0)

# Modifica la funzione per accettare il criterio di ordinamento
def leggi_prodotti_dal_db(criterio_ordine="prezzo"):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        # Gestione query in base alla scelta dell'utente
        if criterio_ordine == "stelle":
            # Dalle stelle più alte a quelle più basse
            query = "SELECT modello, prezzo, negozio, stelle, recensioni, link FROM monitoraggio_prezzi ORDER BY stelle DESC, prezzo ASC;"
        elif criterio_ordine == "recensioni":
            # Da quello con più recensioni a scendere
            query = "SELECT modello, prezzo, negozio, stelle, recensioni, link FROM monitoraggio_prezzi ORDER BY recensioni DESC, prezzo ASC;"
        else:
            # Di base ordina per prezzo dal più basso al più alto
            query = "SELECT modello, prezzo, negozio, stelle, recensioni, link FROM monitoraggio_prezzi ORDER BY prezzo ASC;"
            
        cur.execute(query)
        prodotti = cur.fetchall()
        cur.close()
        conn.close()
        return prodotti
    except Exception as e:
        print(f"{bcolors.FAIL} Errore di lettura del DB: {e}{bcolors.ENDC}")
        return []




def inizializza_db(): #fa il reset della tabella
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS monitoraggio_prezzi;")
        query_creazione = """
        CREATE TABLE monitoraggio_prezzi (
            id SERIAL PRIMARY KEY,
            modello VARCHAR(512) NOT NULL,
            prezzo NUMERIC(10, 2),
            negozio VARCHAR(100) NOT NULL,
            stelle NUMERIC(2, 1) DEFAULT 0.0,      
            recensioni INTEGER DEFAULT 0,
            link VARCHAR(2048), 
            data_rilevazione TIMESTAMP DEFAULT NOW(),
            CONSTRAINT unique_modello_negozio UNIQUE (modello, negozio)
        );
        """
        cur.execute(query_creazione)
        conn.commit()
        cur.close()
        conn.close()
        print("Database inizializzato per la nuova ricerca.")
    except Exception as e:
        print(f"Errore durante l'inizializzazione del DB: {e}")


@app.route('/')
def index():
    # Cattura il parametro 'ordina' dall'URL, Se non c'è, usa 'prezzo'
    criterio = request.args.get('ordina', 'prezzo')
    
    prodotti = leggi_prodotti_dal_db(criterio)
    # Passiamo anche il criterio attuale al template HTML per ricordarci cosa ha scelto l'utente
    return render_template('index.html', prodotti=prodotti, criterio_attuale=criterio)


@app.route('/avvia-scraping', methods=['POST'])
def avvia_scraping():

    dati = request.get_json()
    stringa_ricerca = dati.get('query', '').strip()

    if not stringa_ricerca:
        return jsonify({"status": "errore", "messaggio": "Termine di ricerca vuoto"}), 400

    stato_corrente = r.get('stato_scraping')
    if stato_corrente and stato_corrente.decode('utf-8') == 'in_corso':
        return jsonify({"status": "errore", "messaggio": "Uno scraping è già in corso"}), 409

    # Pulisce la vecchia tabella nel DB Postgres
    inizializza_db()
    
    # Svuota la coda da vecchi residui
    r.delete('coda_prezzi')
    
    # Imposta lo stato su "in_corso"
    r.set('stato_scraping', 'in_corso')
    
    # Spinge la query nella coda Redis (il worker la leggerà direttamente)
    r.lpush('coda_prezzi', stringa_ricerca)

    print(f"--- Master: Coda pronta. Inviata query di ricerca: '{stringa_ricerca}' ---") #log
    return jsonify({"status": "avviato", "query": stringa_ricerca})

@app.route('/stato-scraping')
#Endpoint di Interrogazione utilizzato dal frontend per monitorare in tempo reale lo stato di avanzamento dello scraping del worker
#Ritorna informazioni sulla lunghezza residua della coda e sullo stato del processo.
def stato_scraping(): 
    lunghezza_coda = r.llen('coda_prezzi')
    stato_attuale = r.get('stato_scraping')
    
    if stato_attuale:
        stato_attuale = stato_attuale.decode('utf-8')
    else:
        stato_attuale = 'inattivo'
    # Condizione per determinare il termine del job distribuito
    finito = (lunghezza_coda == 0 and stato_attuale == 'completato')
    
    return jsonify({
        "finito": finito,
        "rimanenti": lunghezza_coda,
        "stato": stato_attuale
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)