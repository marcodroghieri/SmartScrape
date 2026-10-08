import os
import time

import psycopg2
import redis
from dotenv import load_dotenv
from serpapi import GoogleSearch

load_dotenv()

# ----- CONFIGURAZIONE -----
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

# Chiave di autenticazione per l'API esterna SerpApi (Google Shopping Wrapper)
# Impostata nel file .env locale (mai committato), vedi .env.example

SERPAPI_KEY = os.getenv("SERPAPI_KEY")

r = redis.Redis(host='localhost', port=6379, db=0)


# FUNZIONI DI PULIZIA

def pulisci_prezzo(prezzo_grezzo):
    
    if not prezzo_grezzo:
        return None
    try:
        prezzo_str = str(prezzo_grezzo).replace("€", "").strip()

        # Se il prezzo è un intervallo (es. "199 - 249"), tiene solo il primo valore
        for separatore in (" - ", "–", "-"):
            if separatore in prezzo_str:
                prezzo_str = prezzo_str.split(separatore)[0].strip()
                break

        # Gestione formato italiano con punti e virgole
        if "," in prezzo_str and "." in prezzo_str:
            prezzo_str = prezzo_str.replace(".", "").replace(",", ".")
        # Gestione formato solo virgola 
        elif "," in prezzo_str:
            prezzo_str = prezzo_str.replace(",", ".")
            
        # Rimuove tutto ciò che non è un numero o un punto decimale.
        prezzo_pulito = "".join(c for c in prezzo_str if c.isdigit() or c == ".").strip()
        return float(prezzo_pulito)
    except ValueError as e:
        print(f"{bcolors.FAIL} Errore pulizia prezzo ({prezzo_grezzo}): {e}{bcolors.ENDC}")
        return None


def pulisci_stelle(stelle_grezze):
    #Prende il rating e lo converte in float.
    if stelle_grezze is None:
        return 0.0
    try:
        # Trasforma in stringa, cambia le virgole in punti e pulisce gli spazi
        stelle_str = str(stelle_grezze).replace(",", ".").strip()
        return float(stelle_str)
    except ValueError:
        return 0.0


def pulisci_recensioni(recensioni_grezze):
    #Prende il numero delle recensioni e le converte in intero.
    if recensioni_grezze is None:
        return 0
    try:
        rec_str = str(recensioni_grezze).upper().strip()
        
        # Modifica del formato dal formato con la K a quello puramente numerico.
        if "K" in rec_str:
            rec_str = rec_str.replace("K", "").replace(",", ".")
            num_pulito = "".join(c for c in rec_str if c.isdigit() or c == ".")
            return int(float(num_pulito) * 1000)
        
        # Gestione del formato numerico standard o con "+" 
        num_pulito = "".join(c for c in rec_str if c.isdigit())
        return int(num_pulito) if num_pulito else 0
    except ValueError:
        return 0
    
# FUNZIONE DATABASE

def salva_nel_db(modello, prezzo, negozio, stelle, recensioni, link):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        # Inserimento del campo link e gestione dell'ON CONFLICT DO UPDATE di Postgres
        query = """
        INSERT INTO monitoraggio_prezzi (modello, prezzo, negozio, data_rilevazione, stelle, recensioni, link)
        VALUES (%s, %s, %s, NOW(), %s, %s, %s)
        ON CONFLICT (modello, negozio)
        DO UPDATE SET
            prezzo = EXCLUDED.prezzo,
            data_rilevazione = NOW(),
            stelle = EXCLUDED.stelle,
            recensioni = EXCLUDED.recensioni,
            link = EXCLUDED.link
        """
        cur.execute(query, (modello, prezzo, negozio, stelle, recensioni, link))
        conn.commit()
        cur.close()
        conn.close()
        print(f"{bcolors.OKGREEN} DB Salvato: {modello[:40]}... ({negozio}){bcolors.ENDC}")
    except psycopg2.Error as e:
        print(f"{bcolors.FAIL} Errore di salvataggio nel DB: {e}{bcolors.ENDC}")



#FUNZIONE DI INTERROGAZIONE DELL'API E SALVATAGGIO DEI DATI NEL DB
def elabora_ricerca_google(query_ricerca):
    print(f"\n{bcolors.WARNING} Interrogando Google Shopping via API per: '{query_ricerca}'{bcolors.ENDC}")
    
    params = {
        "engine": "google_shopping",
        "q": query_ricerca,
        "hl": "it",  
        "gl": "it",  
        "api_key": SERPAPI_KEY
    }
    
    try:
        search = GoogleSearch(params)
        results = search.get_dict()
        
        if "error" in results:
            print(f"{bcolors.FAIL} Errore restituito da SerpApi: {results['error']}{bcolors.ENDC}")
            return

        prodotti_shopping = results.get("shopping_results", [])
        
        if not prodotti_shopping:
            prodotti_shopping = results.get("inline_shopping_results", [])
            if prodotti_shopping:
                print(f"{bcolors.HEADER} Trovati prodotti nella sezione sponsorizzata 'inline_shopping'.{bcolors.ENDC}")

        print(f"{bcolors.OKCYAN} Ricevuti {len(prodotti_shopping)} prodotti grezzi da Google. {bcolors.ENDC}")
        
        contatore_salvati = 0
        
        for prodotto in prodotti_shopping:
            nome_modello = prodotto.get("title")
            negozio = prodotto.get("source", "Google Shopping")
            
            # 1. Chiamata alle funzioni di pulizia 
            prezzo_finale = pulisci_prezzo(prodotto.get("price"))
            stelle_finale = pulisci_stelle(prodotto.get("rating"))
            recensioni_finale = pulisci_recensioni(prodotto.get("reviews"))
            
            
            url_prodotto = prodotto.get("link") or prodotto.get("product_link")
            if isinstance(url_prodotto, dict):
                url_prodotto = url_prodotto.get("link") or url_prodotto.get("url")
            if not url_prodotto:
                url_prodotto = f"https://www.google.com/search?q={query_ricerca}&tbm=shop"
            
            
            if not nome_modello or prezzo_finale is None:
                continue
                
            # 2. Salvataggio nel DB pulito e ordinato
            salva_nel_db(nome_modello, prezzo_finale, negozio, stelle_finale, recensioni_finale, url_prodotto)
            contatore_salvati += 1
                
        print(f"{bcolors.OKBLUE} Fine elaborazione: {contatore_salvati} prodotti inseriti a DB.{bcolors.ENDC}")
        
    except Exception as e:  # noqa: BLE001 - the worker must survive any API failure
        print(f"{bcolors.FAIL} Errore durante la chiamata API di SerpApi: {e} {bcolors.ENDC}")



def lavora():
    if not SERPAPI_KEY:
        print(f"{bcolors.FAIL} SERPAPI_KEY non impostata. Crea un file .env (vedi .env.example) con la tua chiave SerpApi.{bcolors.ENDC}")
        return

    print(f"{bcolors.OKBLUE} Worker pronto! In ascolto sulla coda Redis per ricerche Google Shopping...{bcolors.ENDC}")

    while True:
        task = r.brpop('coda_prezzi', timeout=5)

        if task:
            query_ricerca = task[1].decode('utf-8')
            
            # SE ARRIVA UN VECCHIO URL DA GOOGLE, ESTRAE SOLO LA QUERY (per retrocompatibilità)
            if "google.com" in query_ricerca and "q=" in query_ricerca:
                query_ricerca = query_ricerca.split("q=")[1].split("&")[0]
                query_ricerca = query_ricerca.replace("+", " ")
            elabora_ricerca_google(query_ricerca)
            time.sleep(2)
        else:
            stato_attuale = r.get('stato_scraping')
            if stato_attuale and stato_attuale.decode('utf-8') == 'in_corso':
                r.set('stato_scraping', 'completato')
                print(f"{bcolors.OKGREEN} Tutti i task completati. Stato aggiornato su Redis: completato.{bcolors.ENDC}")

if __name__ == "__main__":
    lavora()


