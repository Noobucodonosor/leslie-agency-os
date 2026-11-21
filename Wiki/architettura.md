# Architettura

Leslie è una **Flask app single-container** che gira su Cloud Run. Un unico processo, un unico DB SQLite,
persistito su Google Cloud Storage tra un cold-start e l'altro.

Non è microservizi. Non è orchestrato. È volutamente semplice per stare dentro il free-tier di Cloud Run.

## Componenti (fisici)

| File | Ruolo |
|---|---|
| `app.py` | Entry point Flask: route API + init DB + worker GCS + auth utenti |
| `autonomous_scout.py` | Generator che esegue un ciclo completo di scouting (streaming SSE) |
| `ai_analyzer.py` | Wrapper DeepSeek: `generate_json()` e `analyze_spending_capacity()` |
| `scraper.py` | BeautifulSoup: estrae testo, metadata SEO, email dalle pagine |
| `config.py` | `QUERY_POOLS` (categorie + query di default) e `CATEGORY_WEIGHTS` |
| `templates/` + `static/` | Frontend server-rendered + assets |
| `Dockerfile` | `gunicorn --workers 1 --threads 8` |
| `deploy.sh` | Deploy a Cloud Run + iniezione secret |

Vedi anche [[schema_db]] per la parte dati e [[deploy_e_infra]] per l'infra.

## Dataflow: dallo scout al lead salvato

```
      utente clicca "Auto-Scout"
              │
              ▼
   POST /api/scout/stream          (Server-Sent Events, app.py:1217)
              │
              ▼
   autonomous_scout.stream_scout_cycle()
              │
              ├── 1. scelta query        (random da QUERY_POOLS, o manuale)
              ├── 2. Google Places       (textsearch + details, con quota check)
              │       │
              │       └── ensure_quota("places") → 429 se limite raggiunto
              ├── 3. scraper.scrape_lead_site(website)
              │       ├── BeautifulSoup su homepage
              │       ├── + pagine /contact, /contatti, /chi-siamo, /about
              │       └── estrae: text, emails, metadata SEO
              ├── 4. ai_analyzer.analyze_spending_capacity(name, text)
              │       └── DeepSeek → {score, reason, client_type, is_premium, selling_angle}
              ├── 5. decisione: score >= min_score? salva. score >= 7? premium.
              ├── 6. se premium → audit profondo + apprendi nicchia
              │       └── INSERT ON CONFLICT su learned_intelligence
              └── 7. INSERT ON CONFLICT su leads
                      └── mark_dirty() → GCS upload debounced
```

Ogni step **yielda** un evento SSE al frontend, che aggiorna la UI in tempo reale.
Questo è **il** motivo per cui è un generator e non una funzione normale.

Dettagli in [[flusso_scout]].

## Dataflow: ricerca manuale (Live Search)

Path alternativo, senza AI scoring: l'utente digita una query, `GET /api/search?q=...` fa Places + scraping email (10 in parallelo con thread), non salva nulla nel DB `leads` — solo aggiorna `search_history`. Il salvataggio è **manuale** dal frontend via `POST /api/leads/save`.

Differenza chiave: **live search = discovery visiva**, scout = **discovery autonoma con scoring**.

## Multi-user

Non c'è un vero sistema di auth. `get_user_id()` in `app.py:760` legge da:
1. `session['user_id']` (via `/api/auth/login`)
2. Header `X-User-ID`
3. Query param `?user_id=...`
4. Fallback: `'dany'`

Whitelist hard: solo `{'dany', 'alessio'}`. Tutti i dati sono isolati per `user_id` sia in query che in INSERT.
Vedi [[decisioni]] su perché non c'è un vero login.

## Concorrenza

- Flask con `--threads 8` (gunicorn): 8 richieste in parallelo per istanza.
- Ricerche batch: `ThreadPoolExecutor(max_workers=5)` per query Places in parallelo.
- Live search: `threading.Thread` per email enrichment (max 10 in parallelo).
- Upload GCS: **worker singolo** con `threading.Event` per debounce.

SQLite gestisce la concorrenza via **WAL mode** (`PRAGMA journal_mode=WAL`).

## Startup

`safe_startup()` in `app.py:1236` viene eseguito **all'import** del modulo, non solo se `__main__`. Motivo: gunicorn importa `app:app` ma non esegue `if __name__ == '__main__'`. Startup fa in ordine:
1. `gcs_download()` — scarica il DB da GCS
2. `init_db()` — crea tabelle + indici + migrazioni idempotenti
3. `_start_gcs_worker()` — avvia il thread di upload debounced

Se uno step fallisce, gli altri procedono. Questo è voluto: meglio partire con un DB vuoto che non partire affatto.

## Cosa non è

- **Non è async**. È Flask sync + thread. Non c'è `asyncio`, non c'è `FastAPI`.
- **Non ha una code queue**. Tutto è in-process. Se il container muore a metà scout, quel ciclo si perde.
- **Non ha una vera cache condivisa**. La cache AI (`_ai_cache` in `ai_analyzer.py:24`) è in-memory del singolo processo, si svuota a ogni cold start.
