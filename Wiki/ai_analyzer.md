# AI Analyzer (DeepSeek)

Wrapper sottile su **DeepSeek chat API**, usato per:
1. **Scoring** dei lead (0-10) → `analyze_spending_capacity()`
2. **Analisi strategica** (audit, nicchia, query simili) → `generate_json()` con prompt custom
3. **Espansione query** → `generate_json()` per generare nuove query di ricerca

File: `ai_analyzer.py`.

## Modello & client

- Modello: `deepseek-chat`
- Base URL: `https://api.deepseek.com` (compatibile OpenAI)
- Client: lazy singleton, inizializzato al primo `get_client()`

Usiamo la lib `openai` come client perché DeepSeek espone un endpoint compatibile. Vedi [[decisioni]] su questa scelta.

## `generate_json(prompt)` — cuore del modulo

Tutta l'AI passa da qui. Contratto:
- **Input**: stringa prompt (deve chiedere output JSON)
- **Output**: dict Python parsato

Fa in sequenza:
1. **Cache lookup**: `md5(prompt)` → cache in-memory (`_ai_cache` dict). Se hit, ritorna subito.
2. **Quota check**: `ensure_quota("deepseek")`. Se soglia raggiunta → `UsageLimitExceeded`.
3. **Chiamata API** (timeout 30s).
4. **Track**: `track_api_call("deepseek", "chat")`.
5. **Parsing**: pulisce eventuali fence ` ```json ... ``` ` e fa `json.loads()`.
6. **Cache write**: salva il risultato.

## Retry policy

Decorato con `@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))` (libreria `tenacity`).

- 3 tentativi totali
- Backoff esponenziale: 2s, 4s, poi capped a 10s
- **Retry solo su Exception**, incluso `UsageLimitExceeded` — che è sbagliato, un limit exceeded non dovrebbe essere ritentato. Vedi [[invarianti]].

## Cache in-memory (attenzione)

`_ai_cache` è un `dict` a livello modulo. Conseguenze:
- **Si azzera a ogni cold start** di Cloud Run.
- **Non condivisa tra worker** — con `gunicorn --workers 1` non è un problema, ma se un giorno passi a >1 worker ognuno ha la sua.
- **Cresce senza limite**. In pratica non è mai un problema perché il processo muore prima.

Il rationale: la stessa **query di scout** su un lead che risponde con lo stesso testo → stesso prompt → stessa risposta. Cache hit legittimo. Nella realtà l'hit rate è basso perché il testo cambia sempre.

## Scoring: `analyze_spending_capacity(company_name, text_content)`

Prompt in italiano, persona "Leslie analista strategica per flotta yacht Portofino (CIBO Marine)".
Chiede all'AI di essere **severa**.

Contratto di ritorno:
```json
{
  "score": <int 1-10>,
  "reason": "spiegazione max 30 parole",
  "client_type": "es. UHNWI Concierge, Boutique Luxury DMC",
  "is_premium": <bool, true se score >= 7>,
  "selling_angle": "come approcciare, max 20 parole"
}
```

Criteri nel prompt (vedi `ai_analyzer.py:80-90`):
- **8-10**: hotel 5* con suite >2k€/notte, concierge internazionali (Quintessentially, John Paul), family office, DMC che nominano Portofino/Costa Smeralda/St. Barth
- **0-4**: agenzie generaliste, offerte all-inclusive standard

Guard sul testo: se `< 200 caratteri` → ritorna score 0 senza chiamare l'API (risparmio).

## Fallback

`analyze_spending_capacity` cattura **tutte** le eccezioni (`except Exception`) e ritorna
`{"score": 0, "reason": f"Errore AI: {e}", "is_premium": False}`.

Effetto: un errore AI → lead salvato con score 0 (se `min_score == 0`) o filtrato.
Non blocca il ciclo scout.

Il `generate_json` chiamato altrove (audit, espansione nicchia) **non ha** questo fallback → l'eccezione si propaga.

## Cosa NON fa

- Non usa **structured outputs** o **JSON mode** dell'API. Chiede JSON nel prompt e fa `json.loads` sperando bene.
- Non usa **streaming**. La risposta arriva tutta insieme.
- Non usa **tool use** / function calling.
- Non fa **prompt caching** lato provider — DeepSeek lo supporta ma la lib client non è configurata per usarlo.
