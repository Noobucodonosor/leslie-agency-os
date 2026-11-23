# Decisioni tecniche (e perché)

File-storia. Ogni voce risponde a **"perché così e non altrimenti"**.
Se una scelta era ovvia (es. `beautifulsoup` per parsing HTML) non è qui.
Sono qui solo le scelte con un trade-off reale.

Se un giorno vuoi cambiare qualcosa, leggi qui prima. La ragione originale potrebbe non valere più — o potrebbe essere ancora load-bearing.

---

## SQLite invece di Postgres/Cloud SQL

**Scelto perché**: costo. Cloud SQL parte a ~$10/mese anche fermo. SQLite + GCS costa cents.
Il progetto è single-tenant (2 utenti), con volumi bassi (< 10k lead totali attesi).

**Trade-off accettato**: nessuna concorrenza multi-processo, backup manuale, no replica.

**Quando riconsiderare**: se il DB supera i 100MB, se si aggiungono utenti reali (non whitelist), o se serve concorrenza cross-container.

Vedi [[schema_db]], [[deploy_e_infra]].

---

## DeepSeek invece di OpenAI/Claude

**Scelto perché**: **costo per token bassissimo** (~$0.001 per chat call in media, ~30-50x meno di GPT-4/Claude Opus). Per uno scout che chiama l'AI 10+ volte per ciclo, la differenza è massiccia.

**Trade-off accettato**: qualità della severità del giudizio è inferiore a Claude 4.x. Il prompt in italiano compensa con istruzioni molto rigide.

**Compatibilità**: DeepSeek espone endpoint OpenAI-compatible → usiamo la lib `openai` puntandola a `https://api.deepseek.com`. Nessun client custom.

**Quando riconsiderare**: se lo score si dimostra rumoroso su lead borderline (5-7), testare Claude Haiku 4.5 sarebbe naturale.

Vedi [[ai_analyzer]].

---

## Cloud Run + scale-to-zero invece di VM/App Engine

**Scelto perché**: costo zero quando l'app non è usata. Scout è lento ma non frequente → l'uso è bursty.

**Trade-off accettato**: **cold start 5-10s** al primo hit dopo idle. Include download DB da GCS.

**Non riconsiderare** finché il pattern d'uso resta "aperture sporadiche dal browser". Se serve un cron notturno o webhook frequenti, valutare `--min-instances 1`.

Vedi [[deploy_e_infra]].

---

## Persistenza DB via download/upload GCS, non Cloud SQL Auth Proxy

**Scelto perché**: coerenza con la scelta SQLite. Volevamo persistenza semplice, gratis (o quasi).

**Trade-off accettato**: **race condition teorica** se girano 2 istanze scrittrici contemporanee. Mitigato tenendo `--workers 1` e (implicitamente) `--max-instances 1` (che va confermato nel deploy — vedi [[invarianti]]).

**Debounce 2s** per aggregare più write in un singolo upload. Se il container muore dentro quei 2s, l'ultima write si perde.

---

## Server-Sent Events invece di WebSocket per lo scout streaming

**Scelto perché**: SSE è **unidirezionale server→client**, cosa che ci serve (yield del generator). Setup zero: `Response(..., mimetype='text/event-stream')`. WebSocket avrebbe richiesto una libreria e handshake.

**Trade-off accettato**: no reconnect automatico se cade la connessione. Il frontend deve gestire.

Vedi [[flusso_scout]].

---

## `--workers 1 --threads 8` invece di più worker

**Scelto perché**: mantenere stato in-process condiviso:
- `_ai_cache` dict
- `_gcs_dirty` Event
- Connessioni SQLite short-lived

Con `workers > 1` ogni processo ha la sua cache e potrebbero corrompere SQLite anche con WAL (ogni worker apre la sua connessione ma il file è unico, WAL protegge le write ma la coordinazione GCS no).

**Non riconsiderare** finché non c'è un vero backend condiviso (Redis, Cloud SQL).

Vedi [[architettura]], [[deploy_e_infra]].

---

## `is_premium` a soglia hardcoded (score ≥ 7)

**Scelto perché**: 7 è un numero che il prompt AI conosce (viene detto esplicitamente "is_premium: bool, solo se score >= 7"). L'accordo tra prompt e codice va tenuto sincronizzato.

**Trade-off accettato**: cambiare soglia significa cambiare il prompt AI **e** il codice **e** rifare test sui lead esistenti.

**Se cambi**: aggiorna `ai_analyzer.py:98` (prompt), `autonomous_scout.py:210`, [[concetti_dominio]], [[schema_db]].

---

## `market_saturation` random

**È un placeholder**. Nessun calcolo reale. Vive come un range 10-45 (o 20-60 per premium) per non mostrare "0" in UI.

**Perché sopravvive**: il valore è display-only nella UI, e ricalcolarlo davvero (contare lead nella stessa nicchia? cercare concorrenti su Places?) è un progetto a sé.

**Non fidarti** di questo numero per nessuna decisione. Vedi [[invarianti]].

---

## Auth via whitelist di 2 UID, senza password

**Scelto perché**: l'app è privata, il link non è pubblicizzato, e i due utenti sono co-owner del business.
Aggiungere OAuth avrebbe richiesto UX di login, Google Console setup, ecc. Non ne valeva la pena.

**Trade-off accettato**: chiunque scopra l'URL e conosca gli UID (`owner`, `partner`) ha accesso.
Mitigato dal fatto che l'URL Cloud Run è **randomicamente generato** (non indicizzato) e non è pubblicizzato.

**Se un giorno pubblicizzi l'URL**: aggiungi vera auth immediatamente. Vedi `app.py:756`.

---

## CORS: default same-origin

**Scelto perché**: il frontend è servito dalla stessa origine (Flask serve template). Non serve CORS in produzione.

**Override**: env `CORS_ORIGINS` per dev cross-port.

---

## `--timeout 3600` su Cloud Run

**Scelto perché**: lo scout streaming SSE può durare **molti minuti** (10 aziende × ~15s AI + scraping = 2-3 min). Il default Cloud Run di 5 min è troppo stretto per max_results alti.

**Trade-off accettato**: se il client sparisce, l'istanza rimane occupata fino al timeout.

---

## Nessun logging strutturato

**Scelto perché**: Cloud Run Logs cattura stdout, e in dev bastano i `print`. Aggiungere `logging` avrebbe richiesto configurazione senza guadagno immediato.

**Da riconsiderare** se in futuro vuoi filtri per severity o alert. Per ora si legge tutto a occhio nei logs.

---

## Query espanse persistite in `custom_queries` invece di `QUERY_POOLS` globale (2026-07-02)

**Scelto perché**: la vecchia `QUERY_POOLS[cat].extend(new_qs)` aveva due problemi:
1. **Leak cross-user**: il dict è a livello modulo, condiviso tra `owner` e `partner`.
2. **Non persistente**: al restart del container, le query espanse si perdevano.

Persistendole in `user_settings.settings.custom_queries[cat]` risolvi entrambi in un colpo, senza tabelle nuove.

**Trade-off accettato**: due writer sulla stessa colonna `settings` (questo path e `api_scout_settings`). Con `workers=1` non c'è race processo-processo, ma due thread concorrenti potrebbero clobber. Improbabile ma non impossibile.

**Quando riconsiderare**: se un giorno vuoi query espanse condivise (marketplace di nicchie) o se il set diventa grande, sposta in una tabella dedicata `expanded_queries(user_id, category, query, source, created_at)`.

---

## Fail-fast su `SECRET_KEY` mancante (2026-07-02)

**Scelto perché**: il fallback statico `"leslie-default-marine-secret-2026"` era hardcoded nel repo pubblico. Chiunque leggesse il codice poteva firmare cookie di sessione validi con `user_id=owner` e bypassare la whitelist.

**Comportamento**: startup solleva `RuntimeError` se `SECRET_KEY` manca E `FLASK_ENV != development`. Meglio un container che non parte di uno che parte insicuro.

**Trade-off accettato**: se `deploy.sh` è configurato male, il primo deploy dopo il fix rompe. Facile da diagnosticare (errore chiaro nei Cloud Run logs).
