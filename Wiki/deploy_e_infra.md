# Deploy & Infrastructure

Leslie gira su **Google Cloud Run**, regione `europe-west1` (Belgio).
Single container, scale-to-zero, DB persistito su GCS.

Deploy script: `deploy.sh`. Dockerfile: `Dockerfile`.

## Cost model (perché è progettato così)

L'obiettivo dichiarato è **tenere il costo mensile vicino a zero** quando l'app non è usata.
Tutte le scelte infrastrutturali obbediscono a questo.

- `--min-instances 0` → nessuna istanza sempre viva
- `--allow-unauthenticated` → nessun costo di gateway auth
- SQLite → nessun DB gestito ($$$)
- Persistenza via GCS bucket (cents/mese) invece di Cloud SQL (decine di $/mese)
- Cache AI in-process (nessun Redis)

Trade-off cosciente: **cold start latency** ogni volta che nessuno usa l'app da qualche minuto. Il primo hit può richiedere ~5-10s (download DB da GCS incluso). Vedi [[decisioni]].

## Deploy

`./deploy.sh [PROJECT_ID] [REGION]`

Cosa fa:
1. `gcloud builds submit --tag gcr.io/$PROJECT_ID/leslie-agency-os .` (build container)
2. `gcloud run deploy leslie-agency-os` con:
   - `--memory 1Gi`, `--cpu 1`
   - `--timeout 3600` (per SSE long-lived: lo scout streaming può durare minuti)
   - `--set-env-vars FLASK_ENV=production, GCS_BUCKET=agency-os-data-664856461640`
   - `--update-secrets` per `GOOGLE_MAPS_API_KEY`, `DEEPSEEK_API_KEY`, `SECRET_KEY`

I secret vivono in **Google Secret Manager**, non nell'env visibile.

## Container: gunicorn

```
gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
```

- **`--workers 1`**: un solo processo Python. Ragione: l'`_ai_cache`, il `_gcs_dirty` event, la connessione SQLite in-proc funzionano solo con 1 worker (nessuno stato condiviso tra worker). Vedi [[architettura]] e [[invarianti]].
- **`--threads 8`**: 8 richieste in parallelo dentro l'unico worker.
- **`--timeout 0`**: nessun timeout richiesta. Necessario per SSE del scout stream.

## Persistenza GCS

DB SQLite vive nel filesystem del container, che è **effimero**. La persistenza reale è su:

```
gs://agency-os-data-664856461640/agency_db.sqlite
```

### Ciclo

1. **Startup**: `gcs_download()` → scarica il file dal bucket. Se non esiste, parte con DB vuoto.
2. **Write**: qualsiasi endpoint che modifica il DB chiama `mark_dirty()` (via `@db_write` decorator o esplicito).
3. **Worker debounced** (`_gcs_upload_worker` in `app.py:121`):
   - Aspetta il segnale `_gcs_dirty.wait()`
   - Sleep 2s (debounce: aggrega write ravvicinate)
   - Clear del flag
   - `PRAGMA wal_checkpoint(TRUNCATE)` per portare tutto nel file principale
   - Upload con 3 retry e backoff esponenziale
   - Se fallisce → riarma `_gcs_dirty` e ritenta dopo 5s
4. **Atexit**: `_gcs_final_flush()` fa un ultimo upload sincrono con timeout 12s prima che il container muoia.

### Cosa può andare storto

- **Container ucciso senza atexit**: se Cloud Run manda SIGKILL, atexit non gira → ultima write persa.
- **Debounce timing**: se il container muore dopo `mark_dirty()` ma prima dei 2s di debounce, write persa.
- **Race con altri container**: se per errore girano 2 istanze (max-instances > 1 con scritture), l'ultima ad uploadare vince. Attualmente **non c'è protezione** (nessun optimistic locking sul blob). Vedi [[invarianti]].

## Env vars

| Nome | Uso | Default |
|---|---|---|
| `PORT` | Cloud Run standard | 8080 |
| `GCS_BUCKET` | Bucket persistenza | non impostato in dev → skip GCS |
| `GOOGLE_MAPS_API_KEY` | Places API | secret |
| `DEEPSEEK_API_KEY` | DeepSeek | secret |
| `SECRET_KEY` | Flask session | secret (default insicuro se non set) |
| `CORS_ORIGINS` | Whitelist origin | `localhost:8080` in dev |
| `PLACES_DAILY_LIMIT` | quota | 1000 |
| `PLACES_MONTHLY_LIMIT` | quota | 10000 |
| `DEEPSEEK_DAILY_LIMIT` | quota | 200 |
| `DEEPSEEK_MONTHLY_LIMIT` | quota | 3000 |
| `PLACES_COST_PER_1K` | pricing display | 17.0 |
| `DEEPSEEK_COST_PER_CALL` | pricing display | 0.001 |
| `FLASK_ENV` | Debug mode | `production` |

## Health & monitoring

- `GET /health` — always 200 se il processo risponde
- `GET /api/status` — stato worker GCS (dirty, last_upload_ok, last_upload_ts)
- `GET /api/usage` — spesa e chiamate del mese corrente

Cloud Run logs sono l'unico monitoring. Non c'è APM, non c'è Sentry, non c'è dashboard Grafana.

## Development locale

```
export GOOGLE_MAPS_API_KEY=...
export DEEPSEEK_API_KEY=...
export FLASK_ENV=development
python app.py
```

Senza `GCS_BUCKET` → il DB vive solo in `./agency_db.sqlite` locale. Nessun upload/download.
