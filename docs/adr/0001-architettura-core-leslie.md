# ADR-0001: Architettura Core — Single-Container Flask, Scouting SSE, SQLite WAL + GCS Sync

- **Stato:** Accettato
- **Priorità:** P1 — Alta
- **Data:** 2026-11-23
- **Autori:** `Noobucodonosor`
- **Fonti:** `Wiki/architettura.md`, `Wiki/decisioni.md`, `Wiki/schema_db.md`, `LESLIE.md`

---

## 1. Contesto e Problema

Le agenzie B2B nei settori nautica, ospitalità ed esperienze premium necessitano di uno strumento unificato per ricerca lead, qualificazione tramite AI e gestione funnel multi-cliente, mantenendo costi infrastrutturali prossimi a zero su Google Cloud Run.

---

## 2. Decisione Architetturale

1. **Monolite Single-Container (`app.py` + `commerce_engine.py`):**
   - Unica istanza Flask servita da Gunicorn (`--workers 1 --threads 8`) per condividere il file SQLite in modalità WAL e il worker di sincronizzazione GCS.
2. **Persistenza SQLite WAL + Debounced GCS Sync:**
   - All'avvio (`safe_startup`), il database viene scaricato da GCS (`agency_db.sqlite`); ogni mutazione decorata con `@db_write` attiva `mark_dirty()` che esegue `PRAGMA wal_checkpoint(TRUNCATE)` e carica lo snapshot su GCS con debounce di 2 secondi.
3. **Scouting Autonomo Streaming via Server-Sent Events (`autonomous_scout.py`):**
   - `stream_scout_cycle()` opera come generator Python che interroga Google Places (con controllo quota preventivo), esegue scraping statico (`scraper.py`) e qualifica il lead via `ai_analyzer.py`, emettendo aggiornamenti progressivi SSE.

---

## 3. Conseguenze

- **Positive:** Costo infrastrutturale minimo (scale-to-zero su Cloud Run), nessuna dipendenza da database gestiti esterni, feedback in tempo reale durante lo scouting.
- **Vincoli:** Esecuzione vincolata a singola istanza per evitare conflitti di scrittura sul blob SQLite su GCS.

---

## 4. Task Operativi Collegati

- [x] **TASK-ADR-01**: Inizializzare `AGENTS.md`, `docs/adr/` e template Issue/PR (`closes #1`)
- [x] **TASK-CI-01**: Aggiungere workflow GitHub Actions CI (`pytest` + `adr-pre-release`) (`closes #2`)
