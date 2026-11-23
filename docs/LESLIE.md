# LESLIE (C.I.B.O.) — Project Memory

Benvenuto in Leslie, il Chef Digitale (C.I.B.O.). Questo file è l'**indice** del Second Brain del progetto.
La conoscenza dettagliata vive nella cartella [[Wiki/README|Wiki]] — file monotematici, aggiornabili in modo chirurgico.

Se apri questa cartella come vault Obsidian, la Vista Grafo mostra le relazioni.

## 🚀 Stato Attuale
- **Fase**: Produzione (delivery "chiavi in mano" — 2026-07-15)
- **Obiettivo Corrente**: Uso operativo del funnel (saved → contacted → partner)
- **Multi-User**: Supporto Noobucodonosor/Partner con pesi e leads isolati per `user_id`
- **Multi-Cliente**: profili separati per nautica, ospitalità ed esperienze; control room con prossima azione e rete interna
- **Regione**: `europe-west1` (Belgio)
- **Come si opera**: leggi [[HANDOFF]] (runbook per utenti finali)

## 📖 Indice Wiki

### Architettura
- [[Wiki/architettura]] — componenti, dataflow end-to-end, quali file fanno cosa
- [[Wiki/schema_db]] — tabelle, colonne, indici, persistenza GCS
- [[Wiki/deploy_e_infra]] — Cloud Run, Docker, gunicorn, quote, secret

### Motore
- [[Wiki/flusso_scout]] — ciclo scouting autonomo streaming SSE
- [[Wiki/ai_analyzer]] — DeepSeek, scoring 0-10, cache, retry
- [[Wiki/scraper]] — HTML statico, homepage + pagine contatto, email filtering
- [[Wiki/query_pools]] — categorie, pesi, learned intelligence, custom queries

### Contesto
- [[Wiki/concetti_dominio]] — HNWI, lead, premium, nicchia, selling angle
- [[Wiki/decisioni]] — SQLite, DeepSeek, Cloud Run, workers=1 e perché
- [[Wiki/invarianti]] — regole implicite da non violare

## 🛠 Tech Stack (rapido)
- **Backend**: Python 3.11 (Flask + gunicorn `--workers 1 --threads 8`)
- **Database**: SQLite (`agency_db.sqlite`) in WAL mode, persistito su GCS
- **AI**: DeepSeek (`deepseek-chat`, OpenAI-compatible endpoint)
- **Discovery**: Google Places API (textsearch + details)
- **Frontend**: HTML/CSS/JS server-rendered (`templates/`, `static/`)
- **Infra**: Docker → Google Cloud Run + Google Cloud Storage

## 📂 Struttura Progetto (file reali)
- `app.py` — entry point Flask, route API, init DB, worker GCS, auth
- `autonomous_scout.py` — generator streaming per ciclo di scouting
- `ai_analyzer.py` — wrapper DeepSeek (`generate_json`, `analyze_spending_capacity`)
- `scraper.py` — BeautifulSoup + regex email + estrazione persone da /team, /about
- `config.py` — `QUERY_POOLS`, `CATEGORY_WEIGHTS`, `DB_PATH`
- `logging_setup.py` — structured JSON logging per Cloud Run
- `Dockerfile` — python:3.11-slim + gunicorn
- `deploy.sh` — deploy Cloud Run + iniezione secret
- `templates/` + `static/` — frontend
- `tests/` — pytest end-to-end mockato (21 test)
- `Wiki/` — **Second Brain** (questo indice + dettagli)
- `HANDOFF.md` — runbook operativo utente finale (delivery 2026-07-15)
- `Raw/` — dump grezzi (vuoto per ora)
- `Output/` — report generati (vuoto per ora)

## 🌿 Git Workflow
- Branch: `main` (prod), `develop` (dev), `feat/*`, `fix/*`
- Commit: Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`)
- L'agente propone un commit dopo ogni task completato

## 💰 Cost Control (essenziali)
- `--min-instances 0` → nessuna istanza sempre viva
- `--workers 1 --threads 8` (obbligatorio, vedi [[Wiki/invarianti]])
- Quote API: 1000/day Places, 200/day DeepSeek (override via env)
- Persistenza SQLite via download/upload GCS (bucket `agency-os-data-664856461640`)

Dettagli in [[Wiki/deploy_e_infra]] e [[Wiki/decisioni]].

## 📖 Manuale Operativo (utente finale)

> **Handoff completo → [[HANDOFF]]** (funnel, dashboard, cost widget, cron report, runbook problemi).

### 1. Discovery
- **Cliente obbligatorio** — il portafoglio personale è aggregato e sola lettura; ogni ricerca vive nel profilo selezionato
- **Partner Fit v2** — punteggio spiegabile 0–10 su pubblico, territorio, capacità, posizionamento e canale
- **Auto-Scout** ⚡ — ciclo autonomo streaming (SSE), pesato sulle categorie utente
- **Ricerca Manuale** — query diretta con email enrichment parallelo, salvataggio manuale
- **Salvataggio** — lead con `score >= min_score` finiscono in `leads`; `>= 7` sono `is_premium`

### 2. Leads Premium
- Cambia stato, esegui **AI Audit** (DeepSeek), elimina
- **Export CSV** — bottone in alto a destra (sanitizzazione anti-CSV-injection)

### 3. Intelligence
- **Niches** — segmenti di mercato appresi (in [[Wiki/schema_db]]#learned_intelligence)
- **History** — cronologia query + esaurimento

### 4. Strategia
- **Bilanciamento** — sliders pesi per categoria
- **Isolamento** — pesi e leads salvati per utente

---
*Wiki inizializzata: 2026-07-01. Ultimo aggiornamento: 2026-07-02 (bug-fix pass: search_history, audit resilience, SECRET_KEY, scraper redirect dedup, query expansion per-user).*
