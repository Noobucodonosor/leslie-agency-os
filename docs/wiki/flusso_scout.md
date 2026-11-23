# Flusso Scout Autonomo

Lo "scout autonomo" è il modo in cui Leslie trova nuovi lead **senza input umano**. Il nome inganna un po':
non è un cron, non è un daemon. È **triggerato dall'utente** (bottone Auto-Scout nella UI), ma una volta partito
esegue tutta la pipeline in autonomia.

Entry point: `POST /api/scout/stream` in `app.py:1217`.
Motore: `autonomous_scout.stream_scout_cycle()` in `autonomous_scout.py:23`.

## Perché è un generator, non una funzione

La funzione **yielda eventi SSE** (Server-Sent Events) al frontend. Ogni step della pipeline emette un
messaggio strutturato, la UI aggiorna una timeline visiva. Quando l'utente vede "AI Analysis Result: score 8/10"
sta leggendo un yield del generator.

Esiste anche `run_scout_cycle()` (sincrono, `autonomous_scout.py:14`) che consuma il generator senza streaming,
usato dall'endpoint non-streaming `POST /api/scout/auto`. Serve come fallback API-only.

## Parametri

- `max_results` (1-20, default 10) — quante aziende Google Places restituisce da analizzare
- `min_score` (0-10, default 0) — soglia minima per salvare il lead nel DB

Impostati dall'utente via slider nel frontend, clampati server-side in `app.py:1220`.

## Sequenza degli eventi (yield)

| # | `type` | Cosa fa |
|---|---|---|
| 1 | `query_selected` | Sceglie una query random da [[query_pools]] (o usa quella passata) |
| 2 | `status` | Log human-readable |
| 3 | `places_results` | Chiama Google Places textsearch, riporta quanti risultati |
| 4a | `analysis_start` | Inizia analisi del lead i-esimo |
| 4b | `analysis_skip` | (opz) skip se manca website o è social |
| 4c | `scraping_start` | Sta scaricando il sito |
| 4d | `scrape_result` | Testo estratto + email + SEO |
| 4e | `ai_analysis_start` | Sta chiamando DeepSeek |
| 4f | `ai_analysis_result` | Risposta DeepSeek: score, reason, client_type |
| 4g | `niche_learned` | (solo premium) Nuova nicchia registrata |
| 4h | `lead_found` / `analysis_complete` | Lead salvato o filtrato |
| 5 | `final` | Riepilogo del ciclo |

Loop dal punto 4a al 4h per ogni azienda tornata da Places.

## La decisione: salvare o no?

Dopo `analyze_spending_capacity()` abbiamo `score` (0-10).

```python
should_save = score >= min_score      # soglia scelta dall'utente
is_premium  = score >= 7              # soglia hardcoded
```

- Se `should_save == False` → `analysis_complete` con `decision: "filtered"`, non tocca il DB.
- Se `should_save == True` e `is_premium == False` → salva il lead con `is_premium = 0`.
- Se `is_premium == True` → **audit approfondito** (secondo prompt AI) + registra nicchia in [[schema_db]]#learned_intelligence.

**Perché due chiamate AI per i premium**: la prima è cheap (scoring), la seconda è costosa (analisi strategica).
Fare la seconda solo se il primo passo ha già confermato che è un lead premium risparmia soldi.
Vedi [[decisioni]] sui budget.

## Sicurezza: quota check

Prima di **ogni** chiamata Places o DeepSeek:

```python
ensure_quota("places")   # o "deepseek"
```

Se la soglia giornaliera è raggiunta, solleva `UsageLimitExceeded` → 429 al client.
Nello scout il generator yielda `{"type": "error"}` e termina.

Soglie di default (override via env, vedi [[deploy_e_infra]]):
- Places: 1000/day, 10000/month
- DeepSeek: 200/day, 3000/month

## Filtri hard-coded

Alcuni skip **non** passano dall'AI, sono euristiche pratiche:

1. **Nessun sito web** → skip (l'AI non avrebbe niente da leggere)
2. **Website contiene `facebook.com`/`instagram.com`/`twitter.com`** → skip
3. **Content < 200 caratteri** dopo scraping → l'AI ritorna `score: 0` (vedi `ai_analyzer.py:69`)

## Cosa succede a un lead già presente

L'INSERT usa `ON CONFLICT(website) DO UPDATE SET notes=..., selling_angle=..., is_premium=..., seo_data=...`.

**Effetto**: rifare lo scout su un lead esistente **aggiorna** l'audit, non lo duplica. `created_at` non cambia.

Attenzione: **non aggiorna** `phone`, `email`, `address`, `source_query`. Se il sito ha cambiato numero, lo scout non lo vede — solo un `delete_lead` + re-scout lo pulirebbe. Vedi [[invarianti]].

## Cosa NON fa

- **Non gira in background**. Se l'utente chiude il tab dopo aver premuto Auto-Scout, il ciclo continua sul server ma la UI non riceve più eventi.
- **Non ha retry sulla query intera**. Se Places fallisce, il ciclo termina con `error`.
- **Non parallelizza le aziende dentro il ciclo**. Le processa una alla volta (per ordine di streaming SSE prevedibile).

Per ricerca **veloce e parallela** senza AI c'è `api_search_batch` (`app.py:950`), un path completamente diverso.
