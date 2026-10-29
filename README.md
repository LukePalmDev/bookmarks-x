# X-Gemini Bookmarks 🔖✨

Software completo e automatico per la sincronizzazione dei post salvati (segnalibri / bookmarks) dal tuo account **X (Twitter)**, con download locale dei media, archiviazione in database SQLite, esportazione in Markdown e analisi automatica tramite **Google Gemini**.

---

## 🌟 Caratteristiche Principali

- **Connessione a X senza costi API**: Legge la sessione già attiva nel tuo browser (supporto nativo a Safari su macOS, Chrome, Brave, Firefox, Edge o tramite file `.env`), senza dover pagare il costoso piano Twitter Developer ($100/mese).
- **Sincronizzazione Completa e Incrementale**:
  - Scarica tutti i segnalibri storici già salvati.
  - Rileva in automatico i nuovi post che salverai in futuro.
- **Archiviazione Locale ("Scarica")**:
  - Salva metadati completi, testi lunghi (Note Tweets) e tweet citati in un database SQLite locale (`data/bookmarks.db`).
  - Scarica localmente in alta risoluzione tutte le immagini e i media allegati (`data/media/`).
  - Esporta automaticamente i post analizzati come file Markdown pronti per la lettura o per Obsidian/Notion (`data/archive/`).
  - Deduplicazione garantita: un post già archiviato non viene mai riscaricato o rianalizzato inutilmente.
- **Analisi Automatica con Gemini**:
  - Analizza il contenuto del post ed estrae con precisione:
    1. **Autore del post** (`Nome (@username)`)
    2. **Titolo del post** (sintesi chiara ed esplicativa dell'argomento principale, max 10-15 parole)
  - Architettura modulare (`src/analyzer/prompts.py`): pronta per essere personalizzata con qualsiasi ulteriore istruzione di analisi dettagliata che vorrai impostare in seguito.
- **Notifiche Istantanee su Telegram**:
  - Ogni volta che salvi un nuovo post su X, il bot Telegram invia una scheda formattata con la foto in alta risoluzione, l'autore, il titolo sintetico di Gemini e il link diretto al post.
- **Esecuzione in Background su macOS**:
  - Include uno script `install-service.py` per registrare un servizio LaunchAgent nativo (`com.user.x-gemini-bookmarks`), attivo 24/7 in background anche dopo il riavvio del Mac.

---

## 🚀 Come Usare il Tuo Abbonamento Gemini

### Chiarimento Tecnico: Google One AI Premium vs API Gemini
Google adotta una distinzione tra i suoi servizi:
1. **Google One AI Premium / Gemini Advanced**: è l'abbonamento consumer per l'interfaccia web (`gemini.google.com`).
2. **Google AI Studio (Piattaforma Sviluppatori)**: è la piattaforma ufficiale con cui software esterni, script e app possono comunicare programmaticamente con Gemini tramite API.

### Come collegare il tuo account in 30 secondi:
1. Accedi con il tuo account Google su: **[https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)**
2. Clicca sul pulsante blu **"Create API Key"** (Crea chiave API).
3. Con il tuo account Google hai a disposizione l'accesso API **gratuito e permanente** a modelli all'avanguardia come `gemini-2.5-flash` con limiti ampi (fino a 15 richieste al minuto e 1.500 al giorno, ampiamente sufficienti per qualsiasi mole di segnalibri).
4. Crea o apri il file `.env` nella cartella del progetto (`/Users/luke/Developer/x-gemini-bookmarks/.env`):
   ```bash
   GEMINI_API_KEY=AIzaSyTuachiavequi...
   ```

---

## 🛠️ Guida Rapida ai Comandi CLI

Dalla cartella del progetto (`cd /Users/luke/Developer/x-gemini-bookmarks`):

### 1. Diagnostica di Sistema
Verifica che la sessione di X, la chiave Gemini e il database locale siano operativi:
```bash
.venv/bin/python main.py test
```

### 2. Sincronizzazione Immediata
Scarica i segnalibri più recenti da X, scarica i media e avvia l'analisi Gemini:
```bash
.venv/bin/python main.py sync
```
Per forzare il recupero di tutto lo storico passato:
```bash
.venv/bin/python main.py sync --all
```

### 3. Analizzare i Post in Attesa
Se hai scaricato dei post prima di inserire la chiave Gemini, analizzali tutti con un solo comando:
```bash
.venv/bin/python main.py analyze-pending
```

### 4. Visualizzare i Segnalibri Archiviati
Mostra una tabella riassuntiva dei post salvati, con autore, titolo generato da Gemini e stato:
```bash
.venv/bin/python main.py list
```
Per visualizzare i dettagli completi e il testo di uno specifico tweet:
```bash
.venv/bin/python main.py show <TWEET_ID>
```

### 5. Monitoraggio Continuo in Terminale (Watch Mode)
Avvia il controllo periodico (di default ogni 5 minuti):
```bash
.venv/bin/python main.py watch --interval 300
```

---

## ⚙️ Esecuzione Automatica 24/7 su macOS (LaunchAgent)

Per fare in modo che il software funzioni **completamente in automatico in background**, controllando e analizzando i nuovi segnalibri anche senza aprire il terminale:

### Installa e avvia il demone:
```bash
.venv/bin/python install-service.py --install
```

### Controlla lo stato del servizio:
```bash
.venv/bin/python install-service.py --status
```

### Leggi i log in tempo reale:
```bash
tail -f logs/service.log
```

### Disinstalla il servizio:
```bash
.venv/bin/python install-service.py --uninstall
```

---

## 🧩 Come Personalizzare l'Analisi in Futuro

Attualmente Gemini è impostato per individuare **Autore** e generare il **Titolo del post**.

Quando vorrai dirmi come analizzarli nel dettaglio (ad esempio estrazione di tag tematici, codice, riassunti strutturati, livello di complessità o punti chiave), potrai:
1. Chiedermelo direttamente qui in chat (es. *"aggiungi all'analisi l'argomento e 3 punti salienti"*).
2. Oppure modificare direttamente:
   - Lo schema e il prompt in: [`src/analyzer/prompts.py`](file:///Users/luke/Developer/x-gemini-bookmarks/src/analyzer/prompts.py)
   - I parametri del modello in: [`config/config.yaml`](file:///Users/luke/Developer/x-gemini-bookmarks/config/config.yaml)

Tutte le modifiche si integreranno istantaneamente senza dover riconfigurare la connessione a X o il database!

---

## 📁 Struttura della Cartella

```text
x-gemini-bookmarks/
├── config/
│   └── config.yaml          # Parametri di sincronizzazione, modello Gemini e percorsi
├── data/
│   ├── bookmarks.db         # Database SQLite con storico, testi e metadati
│   ├── media/               # Immagini e miniature scaricate in locale
│   └── archive/             # File Markdown esportati per ogni post analizzato
├── logs/
│   └── service.log          # Log delle esecuzioni in background
├── src/
│   ├── auth/                # Estrazione automatica dei cookie di sessione da Safari/browser
│   ├── x_client/            # Client GraphQL asincrono per X Bookmarks con paginazione
│   ├── storage/             # Database SQLite, downloader media ed esportatore Markdown
│   ├── analyzer/            # Client Gemini, prompt e schemi di analisi
│   ├── service/             # Servizio di sincronizzazione incrementale e loop continuo
│   └── cli/                 # Interfaccia a riga di comando (CLI Rich)
├── tests/
│   └── test_components.py   # Test unitari automatizzati
├── install-service.py       # Gestore del servizio macOS LaunchAgent
├── main.py                  # Entrypoint dell'applicazione
├── requirements.txt         # Dipendenze Python
└── .env                     # File per GEMINI_API_KEY e credenziali opzionali
```
