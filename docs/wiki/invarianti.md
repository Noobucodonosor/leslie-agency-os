# Invarianti (cose da NON toccare senza pensarci due volte)

Queste sono le "regole implicite" del progetto — cose che il codice sottintende ma non urla, e che se rompi silenziosamente producono bug difficili da diagnosticare.

Se stai per fare un refactor, **rileggi questo file prima**.

---

## 🚨 Persistenza & Cloud Run

### `--workers 1` è obbligatorio
Non passare a `--workers > 1`. `_ai_cache`, `_gcs_dirty`, e il worker singolo di upload GCS assumono un solo processo. Con più worker: cache duplicate, race sul flag dirty, upload multipli concorrenti su GCS.
Vedi [[decisioni]].

### `--max-instances 1` è (probabilmente) obbligatorio
Se due istanze scrivono contemporaneamente e uploadano su GCS, l'ultima vince → dati persi. Non c'è protezione lato blob (no `if-generation-match`). **Verifica lo stato del deploy** prima di aumentarlo.

### Ogni write DB deve chiamare `mark_dirty()`
Il decorator `@db_write` lo fa per te. Se scrivi endpoint nuovi e dimentichi `mark_dirty()`, le modifiche vivono solo nel container corrente e si perdono al prossimo cold-start.
Endpoint che chiamano `mark_dirty()` manualmente (non via decorator): `live_search`, `api_search_batch`, `_save_custom_queries`, `track_api_call`.

### Non toccare il DB direttamente durante l'esecuzione
Anche in dev locale, se apri il file `.sqlite` con un tool esterno mentre il server gira, WAL potrebbe non riflettere le tue viste. Ferma il server, lavora sul DB, riavvia.

---

## 🚨 Cost control

### Il tetto reale sulla spesa è `PLACES_MONTHLY_BUDGET_USD`, non il call-count
`ensure_quota` blocca su DUE condizioni: soglia call-count giornaliera **o** budget mensile in USD. Il budget default è cauto ($5 Places, $2 DeepSeek) e va sollevato via env se serve. Prima del 2026-07-02 esisteva solo il call-count giornaliero → bill-shock quando qualcuno usava molti scout con `max_results=20`.

Il cap è **real-time in-process**, i budget alert Google hanno 24h lag. Il cap è la difesa vera; l'alert Google è solo notifica.

### Il pricing Google Places non è uniforme: textsearch $32/k, details $20/k
Non riunire in un'unica costante `PLACES_COST_PER_1K`. `_cost_for(provider, endpoint, calls)` è la fonte unica di verità per USD ← chiamate.

### Rate limit scout: `SCOUT_MIN_INTERVAL_SECONDS` per-utente
`/api/scout/auto` e `/api/scout/stream` bloccano con 429 se lo stesso utente chiama entro l'intervallo. Default 20s. Motivo: uno scout costa ~$0.4-0.5, senza limite un click-storm può bruciare il budget in un pomeriggio.

### API key Maps ristretta a Places-only
La chiave Google Maps Platform del progetto era autorizzata su 33 API (directions, weather, geocoding, ecc.). Il codice usa solo Places. Il 2026-07-02 ristretta a `places-backend.googleapis.com` + `places.googleapis.com`. **Non** ri-espanderla senza motivo di dominio: ogni API aggiunta è potenziale bill-shock se la chiave leak-a.

---

## 🚨 Secrets

### `SECRET_KEY` è obbligatoria in produzione
Dal fix 2026-07-02, `app.py` solleva `RuntimeError` allo startup se `SECRET_KEY` non è impostata E `FLASK_ENV != development`. Motivo: il fallback statico precedente (`"leslie-default-marine-secret-2026"`) era hardcoded nel repo → chiunque poteva forgiare cookie di sessione con `user_id=dany`.

Se il deploy fallisce con `RuntimeError: SECRET_KEY mancante`, iniettare il secret in Cloud Run (`deploy.sh` lo fa già tramite `--update-secrets`). **Non** reintrodurre un fallback in chiaro nel codice.

---

## 🚨 Scout resilience

### L'audit premium deve catturare `Exception` (non `sqlite3.Error`)
`autonomous_scout.py:stream_scout_cycle` esegue un audit AI opzionale per i lead con score ≥7. Se questo audit solleva un'eccezione non-DB (quota DeepSeek, JSON malformato, timeout), il ciclo per QUEL lead salta ma il resto del batch continua. Prima del fix 2026-07-02 la clausola era `except sqlite3.Error`, e un audit fallito faceva crollare l'intero ciclo perdendo i lead precedenti (transazione non committata).

### `conn.commit()` va chiamato dopo ogni lead salvato, non solo alla fine
Regola correlata: senza commit per-lead, un errore in un'iterazione qualsiasi (dopo l'INSERT ma prima del commit finale) rollback tutti i lead accumulati. Da 2026-07-02 il commit è dentro il for loop, subito dopo l'INSERT del singolo lead.

---

## 🚨 Multi-user isolation

### **Ogni** query su `leads`, `search_history`, `learned_intelligence`, `user_settings` DEVE filtrare per `user_id`
Non c'è enforcement a DB level. È responsabilità del codice applicativo. Se aggiungi un endpoint e dimentichi il filtro, l'utente A vede i lead dell'utente B.

Pattern corretto:
```python
uid = get_user_id()
conn.execute("SELECT * FROM leads WHERE user_id=?", (uid,))
```

### `get_user_id()` non solleva mai
Fallback silenzioso a `'dany'`. Se qualcosa va storto (session persa, header mancante), le request finiscono sui dati di `dany`. Verifica sempre in produzione via log — `app.py:770` stampa un warning quando fa fallback.

### La whitelist `VALID_USER_IDS` è hardcoded in due punti
`app.py:756` (whitelist) e `app.py:787` (`api_login`). Aggiungere un utente richiede modifica in entrambi.

---

## 🚨 Schema DB

### `leads.website` UNIQUE è la chiave logica
Non permettere due lead con stesso sito. Gli INSERT usano `ON CONFLICT(website) DO UPDATE` per aggiornare. Se rimuovi l'index, gli INSERT falliscono silenziosamente in modo diverso e la logica di re-audit si rompe.

### `ON CONFLICT DO UPDATE` non aggiorna tutti i campi
Nel `save_lead` (`app.py:1039`) l'update tocca solo `status` e `user_id`. Nello scout tocca `notes`, `selling_angle`, `is_premium`, `seo_data`.

**Non** tocca mai: `phone`, `email`, `address`, `source_query`, `created_at`.
Se un sito cambia telefono o email, l'unico modo per aggiornarli è **delete + re-scout**.

### `status` ha una whitelist funnel: `{saved, contacted, replied, meeting, partner, rejected}`
Dal delivery "chiavi in mano" 2026-07-15, il funnel è a 6 stati. Ogni transizione a `contacted/replied/meeting/partner` popola il timestamp corrispondente **solo se non già settato** (`LEAD_STATUS_TIMESTAMP` in `app.py`). Se torni indietro (`contacted → saved → contacted`) `contacted_at` NON si resetta — regola pensata per non falsare le metriche del funnel.

Se aggiungi altri stati: aggiorna `LEAD_STATUSES`, `LEAD_STATUS_TIMESTAMP`, `STATUS_LABELS`/`STATUS_ORDER` in `static/app.js`, il selettore in `templates/index.html`, e la sezione 2 di `HANDOFF.md`.

### Le migrazioni ADD COLUMN sono idempotenti ma il DROP INDEX no
`init_db` ricrea `idx_history_user_query` a ogni startup (`DROP + CREATE`). Motivo storico: vecchi DB avevano l'indice sbagliato. **Non** aggiungere altri DROP a caso o rallenti lo startup.

### `search_history.is_exhausted` va calcolato dai `known_websites`, non hardcoded
Regola: `is_exhausted = 1 se ci sono place con sito valido MA nessuno era nuovo per l'utente, altrimenti 0`. Vale sia per `stream_scout_cycle` che per `live_search` e `api_search_batch`. Prima del fix 2026-07-02, scout e batch hardcodavano `is_exhausted=1` a ogni run: ogni query risultava "esaurita" al primo giro e sparire dal pool suggerito.

Regola parallela: l'`ON CONFLICT DO UPDATE` della history DEVE aggiornare tutti i campi (`results_count`, `new_count`, `is_exhausted`, `last_run`), non solo `last_run`. Prima del fix, in scout gli altri 3 restavano congelati al primo run.

---

## 🚨 AI Analyzer

### La cache `_ai_cache` cresce senza limite
Nessun TTL, nessun cap. Sopravvive fintanto che vive il processo. In pratica non è mai un problema (il container cicla), ma se un giorno passi a min-instances=1, valuta un LRU.

### `@retry` gira anche su `UsageLimitExceeded`
Bug latente: se sfori la quota DeepSeek, `tenacity` ritenta 3 volte con backoff. Non fa danno (l'`ensure_quota` boccia subito ogni ritentativo), ma sono 3 chiamate `ensure_quota` inutili. Considera di catturare esplicitamente e non ritentare.

### La soglia `is_premium = score >= 7` esiste in **tre** posti
- Prompt AI: `ai_analyzer.py:98` (`"is_premium: <bool, solo se score >= 7>"`)
- Codice scout: `autonomous_scout.py:210`
- Documentazione: [[concetti_dominio]], [[decisioni]]

Cambiare la soglia richiede update coordinato.

### DeepSeek risponde a volte con fence Markdown
Il parsing in `generate_json` (`ai_analyzer.py:57`) rimuove ` ```json ` in modo naïve. Se DeepSeek inizia a rispondere con formati diversi (es. `<json>...</json>`), il parser rompe.

---

## 🚨 Scraper

### Non c'è retry sul singolo fetch
Se il sito è lento/giù, `_fetch_page` ritorna `None` e l'analisi per quel lead salta. Nessun log utile.

### `scrape_for_contacts` e `scrape_lead_site` hanno slug diversi
`scrape_lead_site`: `['contact', 'contatti', 'chi-siamo', 'about', 'about-us']`
`scrape_for_contacts`: `['contact', 'contatti', 'about', 'chi-siamo', 'get-in-touch']`

Se cambi una lista ricordati dell'altra. Idealmente sarebbero una costante condivisa. Piccolo debito tecnico.

### Le sub-pagine vanno deduplicate per URL finale post-redirect
`_fetch_page` restituisce `(soup, resp.url)`; entrambe le funzioni di scraping tengono un set `seen_urls` di URL normalizzati (`_normalize_url`) e skippano le pagine il cui URL finale è già stato scaricato.

Motivo: moltissimi siti rediragono slug inesistenti (`/about`, `/contatti`) alla homepage. Senza dedup, il testo homepage veniva appeso 5 volte e il taglio a `content[:5000]` cancellava il contenuto reale della vera contact page. **Non** rimuovere il set `seen_urls`.

### La lista `junk_email_patterns` è duplicata in 2 posti
Hardcoded in `scraper.py:12` **e** proposta come impostazione utente in `app.py:600`. Il codice **non** usa quella dell'utente. Se un giorno la usi davvero, ricordati.

---

## 🚨 Espansione query AI

### Le query espanse si persistono in `user_settings.settings.custom_queries`
Dal fix 2026-07-02, `generate_queries` NON muta più `QUERY_POOLS` globale: le query prodotte da `expand_niche_queries` finiscono in `custom_queries[cat]` per l'utente, via `_save_custom_queries`. Questo ha due effetti:
- **No leak cross-user**: `dany` non "regala" più le sue query espanse ad `alessio`.
- **Persistenza**: sopravvivono al restart del container.

Se in futuro si aggiunge un altro punto di generazione query dinamiche, **non** tornare al pattern `QUERY_POOLS[cat].extend(...)`.

### `expand_niche_queries` valida il tipo di output
DeepSeek a volte risponde con dict (es. `{"queries": [...]}`) invece di list. Il codice ora estrae la prima list-value dal dict, e scarta qualunque altra forma. **Non** rimuovere il controllo `isinstance(..., list)` — senza, si iniettavano chiavi di dict come query.

### `expand_niche_queries` non traccia il costo
Chiama `generate_json` che passa da `ensure_quota` — verifica che sia sempre così se refactor.

---

## 🚨 UI / API contract

### Il frontend fa polling con `X-User-ID` header
Se cambi il pattern di auth (es. cookie-only), aggiorna anche il fetch dei template `templates/index.html`.

### Route SSE `/api/scout/stream` DEVE inviare `data: ... \n\n`
Il formato SSE è rigido. Il codice usa `yield f"data: {json.dumps(update)}\n\n"`. Un solo `\n` in meno e il browser non riceve nulla.

---

## 🧹 Codice orfano rimosso (2026-07-15 delivery)

Prima esistevano diversi residui inutilizzati; oggi il codebase è pulito:

- ~~`scraper.py:process_unscraped_leads`~~ — rimosso 2026-07-02.
- ~~Tabella `analysis`~~ — rimossa 2026-07-15 (mai letta/scritta, sostituita dai timestamp funnel su `leads`).
- ~~`MODELS_TO_TRY`~~ — rimosso 2026-07-15.
- ~~`require_lead_owner`~~ — rimosso 2026-07-15 (ogni route filtra a mano con `WHERE user_id=?`).
- ~~`abort`, `send_file` in `app.py`~~ — rimossi 2026-07-15.

Se torni a introdurne di simili, aggiungili in questa sezione con motivazione.

---

## 🚨 Delivery "chiavi in mano" 2026-07-15

### `contacts_json` è JSON: **non** stringa vuota
Il default di colonna è `'[]'`. Il codice fa `JSON.parse(l.contacts_json || '[]')` — non introdurre INSERT che passano `''` o `null`, romperesti il parser frontend.

### `leads.draft_email` è "cached email" — rigenera solo con `force=true`
`POST /api/leads/<id>/draft_email` senza body → ritorna la bozza esistente (`cached: true`). Con `{"force": true}` → paga DeepSeek e sovrascrive. La UI passa `force: true` solo quando l'utente clicca "Rigenera".

### `is_generic_email` viene calcolato in **due** posti
`app.is_generic_email()` (usato in `save_lead` e via `import` da `autonomous_scout`) e `scraper._is_generic_email()` (usato per marcare i contatti estratti dal team page). La lista `GENERIC_EMAIL_PREFIXES` / `GENERIC_PREFIXES` è duplicata. Se aggiungi un prefisso, aggiornali entrambi.

### Il widget budget usa la % massima fra Places e DeepSeek
Sono budget indipendenti — la % totale non è definita. La UI mostra la % del più stressato dei due, così l'utente vede subito quale sta per bloccarsi. Non calcolare una media.

### L'alert 80% è dismissibile solo per il mese corrente
`sessionStorage.budget_alert_dismissed_month = 'YYYY-MM'` — ricompare al cambio mese o hard-refresh. Comportamento voluto: la dismissione non deve nascondere il problema permanentemente.

### Logging: preferisci `extra={"chiave": valore}` a f-string
Cloud Logging indicizza i campi top-level del JSON. `log.warning("slow_upload", extra={"seconds": 12.4})` è filtrabile per `seconds`; `log.warning(f"upload slow: 12.4")` no. Vale per `leslie.app`, `leslie.scout`, `leslie.ai`.

---

## 📝 Come si aggiorna questo file

Ogni volta che scopri un'invariante non scritta (leggendo codice o rompendo qualcosa), aggiungila.
Ogni voce dovrebbe rispondere a: **"Che regola non ovvia ho appena scoperto?"** e **"Cosa succede se la violo?"**.
