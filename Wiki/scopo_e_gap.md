# Vero scopo e gap dell'app

Documento nato dall'analisi 2026-07-02. Serve per non perdere di vista **cosa** l'app dovrebbe fare rispetto a **cosa** fa oggi. Aggiornare quando cambia la strategia commerciale di CIBO Marine.

## Il vero scopo

L'app **non è** un directory di aziende, **non è** un motore di ricerca e **non è** un CRM. È un **generatore di pipeline commerciale B2B per CIBO Marine** — flotta charter yacht di lusso a Portofino.

Il KPI che conta non è "quanti lead nel DB", è **quanti partner B2B attivi mandano UHNWI a bordo**. Ogni funzionalità dell'app va giudicata contro questa metrica.

## Il funnel completo (idealizzato)

```
1. DISCOVER   → Google Places textsearch                        [OK]
2. QUALIFY    → AI scoring 0-10 + is_premium ≥7                  [OK]
3. ENRICH     → email + SEO + selling_angle                     [PARZIALE]
4. OUTREACH   → contatto reale al lead                          [MANCA]
5. TRACK      → stato risposta / meeting                        [MANCA]
6. CONVERT    → partner attivo che invia clienti                [MANCA]
```

Solo gli step 1-3 sono nell'app. Gli step 4-6 vivono nella testa dei due utenti (o in un foglio Excel a parte), il che spiega perché "lead premium" e "partnership attive" oggi non correlano.

## Analisi multi-prospettiva

### 👷 Ingegnere del software

**Punti forti**
- Monolite semplice, in produzione, low-cost by design.
- Multi-user isolato con whitelist hard.
- Persistenza GCS con debounce, resiliente a cold-start.
- Fix 2026-07-02: search_history onesta, scout resiliente, SECRET_KEY fail-fast, dedup scraper, cost tracking realistico + hard cap mensile.

**Punti deboli**
- No test automatici → ogni deploy è un salto di fede.
- No logging strutturato → debug in produzione a occhio.
- Un solo processo, un solo container: crescita = riprogettazione (SQLite + workers=1).
- Nessuna coda: se il container muore a metà scout, il ciclo si perde.
- Cache AI in-memory: si azzera a ogni cold-start.
- `market_saturation` random è un buco cognitivo — l'utente lo vede e potrebbe dargli peso.

**Debiti tecnici da chiudere**
- Testing minimo (almeno il flusso scout end-to-end mockato).
- Structured logging con severity (WARN quando quota vicina, ERROR quando cap raggiunto).
- Rimuovere codice orfano rimasto (tabella `analysis`, `MODELS_TO_TRY`, `require_lead_owner`, `abort/send_file` non usati).

### 👤 Utente (Dany / Alessio)

**Cosa funziona**
- Auto-scout è "fire and forget", risultati streaming visivi.
- Lead premium ben separati dai non-premium.
- Il selling_angle AI-generated aiuta a capire "come parlargli".

**Cosa frustra o manca**
- Trovo il lead premium — **e poi?** L'app non mi aiuta a contattarlo. Devo copiare email a mano e scrivere mail da zero.
- L'AI-audit dà tre righe di profilo strategico ma nessun draft di prima email.
- Nessuna traccia di "l'ho contattato il X", "ha risposto il Y".
- Nessun promemoria follow-up dopo N giorni no reply.
- Email raccolte spesso `info@` — non decisore. Nessun tentativo di trovare persone specifiche (LinkedIn?).
- Costi visibili solo se apri un pannello nascosto: dopo una bolletta salata scopri di aver superato il budget.

### 💼 Business (CIBO Marine)

**Cosa conta davvero**
- Pipeline: partner in trattativa attiva > 0.
- Conversione: % partner attivati / lead premium contattati.
- Ripetizione: partner che manda clienti a bordo più di una volta.

**Cosa l'app misura vs cosa dovrebbe misurare**

| Metrica app | Metrica business |
|---|---|
| Numero lead premium salvati | Numero partner attivi (0 finora nel DB) |
| Chiamate API/mese | Costo per partner acquisito |
| Nicchie apprese | Partner per nicchia (revenue per nicchia) |
| Query esaurite | Categorie che convertono meglio |

L'app oggi ottimizza l'input (scouting) senza misurare l'output (conversione).

## Costi Google: perché la bolletta è arrivata salata

Diagnosi 2026-07-02 (basata sul DB e sull'infrastruttura):

1. **Nessun budget alert configurato** su Google Cloud → nessuna email quando la spesa saliva. Risolto lato-app con `PLACES_MONTHLY_BUDGET_USD` (default $5) che blocca in real-time (più affidabile degli alert Google che hanno 24h lag).
2. **Pricing sottostimato** in `/api/usage`: prima si usava un unico `PLACES_COST_PER_1K=17` per tutti gli endpoint. In realtà textsearch = $32/1k, details = $20/1k → costo reale del ~50% superiore a quello mostrato.
3. **API key Maps sovraestesa** — era autorizzata su 33 API (directions, distance-matrix, geocoding, weather, ecc.). Ristretta a solo `places-backend` + `places` il 2026-07-02.
4. **Container Registry storage** — 5 immagini in gcr.io, 4 senza tag. Ripulite il 2026-07-02.
5. **Nessun rate limit sull'endpoint scout** → un utente click-happy poteva bruciare $10 in un pomeriggio. Aggiunto `SCOUT_MIN_INTERVAL_SECONDS` (default 20s).

## Roadmap per "fulfill completely"

**Fase 1 — Cost sanity (fatto 2026-07-02)**
- [x] Pricing reale per-endpoint
- [x] Hard cap mensile in dollari
- [x] Rate limit scout per-utente
- [x] API key ristretta a Places-only
- [x] Container Registry cleanup

**Fase 2 — Chiudere il funnel (delivery 2026-07-15)**
- [x] Stati lead: `saved` → `contacted` → `replied` → `meeting` → `partner` (o `rejected`) con timestamp auto-popolati
- [x] Draft email one-click da `selling_angle` + tono CIBO Marine, cached in `leads.draft_email`
- [x] Follow-up: badge in header + filtro pagina Leads su `contacted_at > 7d && replied_at IS NULL`
- [x] Colonne `contacted_at`, `replied_at`, `meeting_at`, `partner_since` in `leads`
- [x] Dashboard conversione: nuova view "Pipeline" (funnel + breakdown categoria + serie 6 mesi + report mese)

**Fase 3 — Enrichment veri contatti (delivery 2026-07-15)**
- [x] `is_generic_email` flag + banner rosso "info@" nel dettaglio lead
- [x] Scraper estrae `[{name, role, email, is_generic_email}]` da slug team/staff/about → colonna `leads.contacts_json`
- [x] UI dettaglio lead mostra la lista persone identificate come card

**Fase 4 — Osservabilità e cost visibility (delivery 2026-07-15)**
- [x] Endpoint timeseries per grafico spesa mensile (era già fatto)
- [x] Widget sempre visibile in header: `$X / $Y   N%` con colori verde/giallo/rosso
- [x] Alert UI dismissibile quando budget Places o DeepSeek ≥ 80%
- [x] Endpoint `/api/reports/monthly` + `/api/reports/monthly.html` — cron esterno per email documentato in HANDOFF.md

**Fase 5 — Infrastruttura solida (delivery 2026-07-15)**
- [x] Test end-to-end mockati: 21 test in `tests/` (scout, save, audit, funnel, follow-ups, scraper, quota)
- [x] Structured JSON logging (`logging_setup.py`) su stdout, Cloud-Run friendly, WARN a 80% budget e ERROR al cap
- [x] Cleanup codice orfano: tabella `analysis`, `MODELS_TO_TRY`, `require_lead_owner`, `abort`/`send_file` rimossi
- [ ] Migrazione a Postgres — rimandata: attualmente il DB pesa ≪100MB e utenti = 2 (vedi [[decisioni]])

## Come si aggiorna questo documento

Ogni volta che:
- La strategia CIBO Marine cambia
- Un utente segnala "l'ho contattato ma non sapevo che avevo già scritto ieri" o simili → è un gap del funnel
- Arriva un'altra bolletta sorpresa → aggiorna la sezione "Costi Google"
