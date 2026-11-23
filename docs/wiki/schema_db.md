# Schema DB

DB: **SQLite** singolo file, `agency_db.sqlite`. Percorso in `config.py:4`.
Modalità: **WAL** (`PRAGMA journal_mode=WAL`) + `foreign_keys=ON`.

Le tabelle vengono create al primo avvio da `init_db()` in `app.py:357`.
Le migrazioni sono **idempotenti**: `ALTER TABLE ... ADD COLUMN` in `try/except OperationalError`.
Se una colonna esiste già, l'ALTER fallisce e viene ignorata.

## Tabelle

### `leads` — il cuore del sistema

Ogni azienda trovata (via scout o live search) e **salvata** finisce qui.

| Colonna | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `user_id` | TEXT | `'owner'` o `'partner'`, default `'owner'` |
| `company_name` | TEXT NOT NULL | nome ufficiale da Google Places |
| `website` | TEXT UNIQUE | **chiave logica**: due lead con stesso website non possono coesistere |
| `phone` | TEXT | da Places `formatted_phone_number` |
| `email` | TEXT | CSV di email trovate dallo scraper (può essere vuoto) |
| `address` | TEXT | da Places `formatted_address` |
| `source_query` | TEXT | la query che l'ha originato |
| `status` | TEXT | funnel: `saved` \| `contacted` \| `replied` \| `meeting` \| `partner` \| `rejected` (vedi [[invarianti]]) |
| `notes` | TEXT | contiene l'**audit AI** (formato markdown) |
| `selling_angle` | TEXT | suggerimento commerciale AI, 15-25 parole |
| `is_premium` | BOOLEAN | true se `score >= 7` |
| `seo_data` | TEXT | JSON serializzato: `{title, description, keywords, h1, preview_url}` |
| `contacted_at` | TIMESTAMP | popolato la prima volta che `status='contacted'` |
| `replied_at` | TIMESTAMP | popolato la prima volta che `status='replied'` |
| `meeting_at` | TIMESTAMP | popolato la prima volta che `status='meeting'` |
| `partner_since` | TIMESTAMP | popolato la prima volta che `status='partner'` |
| `is_generic_email` | BOOLEAN | 1 se la mail primaria è `info@`, `contact@`, ecc. (banner rosso in UI) |
| `contacts_json` | TEXT | JSON: `[{name, role, email, is_generic_email}]` — persone estratte da /team, /about, /staff |
| `draft_email` | TEXT | cache dell'ultima bozza AI generata (`Oggetto: …\n\n…`), evita di ripagare DeepSeek |
| `category` | TEXT | categoria di scouting (es. `🏨 Hotel & Resort …`), usata per breakdown dashboard |
| `created_at` | TIMESTAMP | default `CURRENT_TIMESTAMP` |

**Invariante `website UNIQUE`**: gli INSERT dello scout usano `ON CONFLICT(website) DO UPDATE`, non `IGNORE`. Motivo: se rifaccio audit sullo stesso sito, voglio **aggiornare** `notes`, `selling_angle`, `is_premium`, `seo_data`. Vedi `autonomous_scout.py:271`.

### `search_history` — cronologia query

| Colonna | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | TEXT | isolamento multi-user |
| `query` | TEXT NOT NULL | |
| `results_count` | INTEGER | totale risultati Places |
| `new_count` | INTEGER | quanti risultati non erano già in `leads` |
| `is_exhausted` | BOOLEAN | 1 se `results > 0 AND new_count == 0` |
| `last_run` | TIMESTAMP | |

**Unique**: `(user_id, query)`. Se rifai la stessa query, l'INSERT usa `ON CONFLICT DO UPDATE`.

**Concetto di "esaurita"**: una query è esaurita quando Google Places restituisce risultati ma **tutti** sono già nel DB. Serve al generator di query in `app.py:489` per **non riproporre** query improduttive.

### `learned_intelligence` — nicchie apprese

Ogni volta che si fa un audit AI approfondito su un lead premium, l'AI ne estrae una "nicchia" (es. `"UHNWI Concierge Milano"`) e la registra qui insieme a query simili suggerite.

| Colonna | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | TEXT NOT NULL | |
| `niche_key` | TEXT NOT NULL | nome breve della nicchia |
| `description` | TEXT | descrizione strategica |
| `market_saturation` | INTEGER | 10-60 (oggi **random**, vedi [[decisioni]]) |
| `suggested_queries` | TEXT | JSON list, riusate dal generator |
| `confidence` | INTEGER | incrementato di 1 a ogni riscontro |
| `created_at` | TIMESTAMP | |

**Unique**: `(user_id, niche_key)`. Ogni conflict incrementa `confidence`.

**Uso**: `generate_queries()` in `app.py:489` peca le top-5 per confidence e inserisce le loro query nel pool corrente. Vedi [[query_pools]].

### `analysis` — audit legacy (probabilmente inutilizzata)

Dichiarata in `init_db()` ma non ho trovato SELECT/INSERT attivi nel codice.
Probabile candidato per rimozione. Vedi [[invarianti]].

### `user_settings` — pesi e configurazione per utente

| Colonna | Tipo | Note |
|---|---|---|
| `user_id` | TEXT PK | `'owner'` o `'partner'` |
| `weights` | TEXT | JSON: `{categoria: peso}` |
| `settings` | TEXT | JSON: contiene `custom_queries`, parametri scout |

Due campi separati per **retrocompatibilità**: `weights` esisteva prima, `settings` è stato aggiunto dopo per contenere il resto senza rompere il codice esistente.

### `api_usage` — tracking chiamate a pagamento

| Colonna | Tipo | Note |
|---|---|---|
| `day` | TEXT | `YYYY-MM-DD` UTC |
| `provider` | TEXT | `'places'` \| `'deepseek'` |
| `endpoint` | TEXT | `'textsearch'` \| `'details'` \| `'chat'` |
| `calls` | INTEGER | contatore |

PK composta `(day, provider, endpoint)`. INSERT usa `ON CONFLICT DO UPDATE SET calls = calls + excluded.calls`.

Serve al **quota check**: `ensure_quota(provider)` solleva `UsageLimitExceeded` se la soglia giornaliera è stata raggiunta. Vedi [[decisioni]] sulle soglie di default.

## Indici

Tutti creati in `init_db()`, tutti `IF NOT EXISTS`:

- `idx_leads_website` (UNIQUE) — enforce del vincolo di unicità
- `idx_history_user_query` (UNIQUE) — enforce del vincolo, ricreato ogni startup con `DROP INDEX IF EXISTS` (per correggere DB vecchi)
- `idx_intel_user_niche` (UNIQUE)
- `idx_leads_user`, `idx_leads_user_status`, `idx_leads_user_created` — query più frequenti
- `idx_history_user_last`, `idx_intel_user_conf`, `idx_analysis_lead`, `idx_usage_day`

## Persistenza (importante)

Il file `agency_db.sqlite` in Cloud Run vive nel **filesystem effimero** del container. **Non è persistente**.
La persistenza vera vive su `gs://agency-os-data-664856461640/agency_db.sqlite`.

Ciclo:
1. Startup: `gcs_download()` scarica il DB dal bucket
2. Ogni write: `mark_dirty()` sveglia il worker
3. Worker (debounced 2s): `PRAGMA wal_checkpoint(TRUNCATE)` + upload
4. `atexit`: flush finale sincrono con timeout 12s

**Se il container muore prima del debounce**, quella write si perde al prossimo cold-start.
Vedi [[deploy_e_infra]] per i dettagli e [[invarianti]] per le regole di sicurezza.
