# Query Pools

Le query di ricerca Google Places che alimentano lo scout non sono infinite. Vivono in **quattro livelli**,
combinati dinamicamente da `generate_queries()` in `app.py:489`.

## I quattro livelli

### 1. Pool statico — `config.py:QUERY_POOLS`

Dizionario `{categoria: [query, ...]}` con 7 categorie hardcoded:
- 🏨 Hotel & Resort (chi consiglia le barche agli ospiti)
- 🌍 DMC & Incoming Italia
- 🇺🇸🇬🇧 Agenzie estere specializzate Italia
- 💍 Wedding & Eventi Liguria
- ⛵ Charter & Broker Nautici
- 🏢 Corporate & MICE
- 💎 Concierge & Lifestyle (UHNWI)

Total: ~60 query di partenza. Il nome della categoria include l'emoji + una spiegazione tra parentesi — il testo tra parentesi è **parte della key** del dict, non un commento.

### 2. Custom user queries — `user_settings.settings.custom_queries`

L'utente può aggiungere query personalizzate via `POST /api/queries/custom`. Sono salvate in `user_settings` sotto la chiave JSON `custom_queries`. Isolate per utente.

Il generator le tratta come **estensione del pool statico**, non le distingue in generazione.

### 3. Learned intelligence — nicchie apprese

Ogni volta che l'audit AI approfondito (solo per premium) genera una "nicchia", 3 query suggerite finiscono in `learned_intelligence.suggested_queries` (JSON list).

Il generator prende le **top-5 nicchie** per `confidence DESC` e inserisce le loro query.

### 4. Espansione AI on-demand

Se una categoria ha peso alto (≥80) **e** il suo pool disponibile è < 5 query, `generate_queries` chiama `expand_niche_queries(cat)` che invoca DeepSeek con:
> "Genera 10 query di ricerca per Google Maps (in inglese) per trovare aziende nella categoria '{cat}'. Rispondi solo con una lista JSON di stringhe."

Le nuove query vengono **iniettate nel dict `QUERY_POOLS` in memoria**. Vivono fino al restart. Vedi [[invarianti]].

## Come "esaurita" spegne le query

`search_history.is_exhausted = 1` quando una query ha prodotto risultati ma nessuno nuovo.

Il generator filtra via **tutte** le query esaurite prima di comporre il batch. Effetto: le query si "consumano" progressivamente, spingendo lo scout verso nuove nicchie.

## Pesi (weights)

`CATEGORY_WEIGHTS` in `config.py:87` — default:
- Hotel: 5
- Agenzie estere: 4
- Concierge: 4
- DMC: 3
- Wedding: 2
- MICE: 2
- Charter: 1

Peso 0 = categoria **disattivata**. Peso 5+ = categoria prioritaria (usata come filtro per espansione AI).

L'utente li modifica dalla UI (sliders Strategia) via `POST /api/settings/weights`. Salvati per utente in `user_settings.weights`.

## Come si compone il batch (algoritmo)

Chiamata: `generate_queries(n=20)` (default `n=12`).

```
1. Filtra le query esaurite (query ∈ search_history WHERE is_exhausted=1)
2. Aggiungi query dalle top-5 nicchie apprese (marcate con categoria "🎯 Nicchia: ...")
3. Per ogni categoria:
     - pool = QUERY_POOLS[cat] + custom[cat], filtrato "non esaurite"
     - se peso >= 80 e pool < 5 → chiama espansione AI
     - count = round((peso/totale_pesi) * target_std)
     - picks = random.sample(pool, min(count, len(pool)))
     - aggiungi picks al risultato
4. random.shuffle(risultato)
5. return risultato[:n]
```

Distribuzione: proporzionale ai pesi utente. Categoria con peso 5 su totale 21 riceve ~24% delle query del batch.

**Target minimo standard**: 10 query. Se le nicchie apprese ne portano già molte, il codice garantisce comunque ≥10 dal pool standard.

## Endpoint utili

- `GET /api/queries/pool` — pool completo con stato `new`/`used`/`exhausted` per categoria
- `GET /api/queries/stats` — conteggi sintetici (per tab "Settori")
- `GET /api/queries/generate` — genera batch di 12 (per la UI)
- `POST /api/queries/custom` / `DELETE` — CRUD query personali

## Osservazione strategica

Il sistema è **auto-esplorativo**: parte dalle categorie hardcoded, ogni premium trovato registra una nicchia,
le query di quella nicchia rientrano nel pool, generano nuovi lead, che generano nuove nicchie.

In teoria converge verso un pool che descrive esattamente il **cliente ideale** di CIBO Marine.
In pratica, la qualità del feedback loop dipende interamente dalla severità del prompt AI in [[ai_analyzer]].
