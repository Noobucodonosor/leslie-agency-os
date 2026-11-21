# Leslie — Handoff "chiavi in mano"

Documento operativo per il proprietario dell'app (Dany/Alessio). Contiene
tutto ciò che serve per **usare**, **mantenere**, **deployare** e **risolvere
i problemi comuni** senza dover leggere il codice.

Se cerchi il "perché è così?" leggi [`Wiki/`](Wiki/README.md). Qui c'è solo il
"come si opera".

---

## 1. Cosa fa Leslie (breve)

Leslie è un **scout autonomo B2B** per CIBO Marine (charter yacht di lusso a
Portofino). Non cerca clienti finali (UHNWI): cerca **partner** — hotel 5*,
DMC, wedding planner, family office, concierge — che portano UHNWI a bordo.

Ogni riga in `leads` = **un candidato partner**. Il KPI di successo non è
"quanti lead", ma "quanti **partner attivi** che mandano clienti a bordo".

---

## 2. Il funnel di partnership (novità delivery Fase 2)

```
saved → contacted → replied → meeting → partner
                ↘ rejected
```

- **saved**: Leslie l'ha trovato e scoraggiato con AI.
- **contacted**: hai spedito la prima mail (registra `contacted_at`).
- **replied**: ha risposto (registra `replied_at`).
- **meeting**: prima call/incontro fissato (registra `meeting_at`).
- **partner**: sta effettivamente mandando clienti (registra `partner_since`).
- **rejected**: escluso — non fa per noi.

Il cambio di stato si fa dal **dettaglio del lead** (click sulla card →
banda "Stato Trattativa" con 6 pill cliccabili). I timestamp si
autopopolano alla prima transizione e **non vengono sovrascritti** se torni
indietro (così le metriche di funnel restano oneste).

**Follow-up automatici**: qualsiasi lead in stato `contacted` da più di 7
giorni senza `replied_at` compare nel badge "N follow-up" in header e nel
filtro "Solo Follow-up" nella pagina Contatti. Click sul badge = filtra
subito i lead da risollecitare.

---

## 3. Bozza email one-click (Fase 2)

Nel dettaglio lead → sezione "Bozza email B2B" → **Genera con AI**.

Genera in italiano una email di prima uscita B2B di ~140 parole, tono
elegante-non-commerciale, calibrata sul `selling_angle` + `category` + titolo
sito del lead. Chiusura standard: "Team CIBO Marine — Portofino".

- La bozza viene **persistita** in `leads.draft_email`: le rigenerazioni
  costano una call DeepSeek in più, quelle "cached" no.
- Il bottone "Apri nel client mail" apre il default mail client con `to:`,
  `subject:`, `body:` pre-compilati.
- Le persone identificate sulla pagina Team (Nome — Ruolo — email) appaiono
  sopra la bozza — usale invece dell'`info@` quando ci sono.

**Warning `info@`**: se la mail primaria è generica (info@, contact@,
booking@…) appare un banner rosso "Email generica — probabilmente non un
decisore". Non è un blocco, è un pro-memoria.

---

## 4. Pipeline dashboard (Fase 2)

Nuova voce nel menu laterale → **Pipeline**. Contiene:

1. **Hero tiles**: Lead totali, Contattati o oltre, Partner attivi, %
   conversione contatto→partner.
2. **Funnel bar** a 6 stadi con conteggi live.
3. **Nicchie che convertono meglio**: barra composta per categoria (colori
   in linea con gli stadi). Utile per capire dove concentrare pesi/query.
4. **Serie 6 mesi**: contattati vs partner nuovi per mese.
5. **Report mese corrente**: nuovi lead, premium, contattati, partner nuovi,
   costo, costo per partner nuovo.

Nessuna interazione richiesta — è pura reportistica.

---

## 5. Cost visibility (Fase 4)

- **Widget in header** (sempre visibile): `$X / $Y  N%`. Verde <50%, giallo
  50-80%, rosso ≥80%. Click → tab Impostazioni → Costi.
- **Alert modale in alto a destra** quando l'utilizzo di *uno qualsiasi* dei
  budget (Places o DeepSeek) raggiunge l'80%. Dismissibile per il mese
  corrente (via `sessionStorage`).
- **Cap in-app** (Fase 1): `ensure_quota` blocca fisicamente lo scout
  quando si raggiunge `PLACES_MONTHLY_BUDGET_USD` o `DEEPSEEK_MONTHLY_BUDGET_USD`.
  Default cauti: $5 e $2 rispettivamente. Override via env.

### Report mensile via email (opzionale, cron manuale)

L'endpoint HTML pronto per il body-mail è:

```
GET /api/reports/monthly.html?month=YYYY-MM
Header:  X-User-ID: dany   (o alessio)
```

Esempio cron cron-only (macOS/Linux con `mail`):

```cron
# 1° del mese alle 09:00 UTC — invia report del mese precedente
0 9 1 * *  curl -s -H 'X-User-ID: dany' \
  "https://<cloud-run-url>/api/reports/monthly.html?month=$(date -u -d 'last month' +%Y-%m)" \
  | mail -a 'Content-Type: text/html' -s "Leslie report $(date -u -d 'last month' +%Y-%m)" you@example.com
```

Se non hai SMTP locale, apri l'URL nel browser e inoltra a mano — o
schedulalo su GitHub Actions / Cloud Scheduler colpendo l'endpoint e usando
un servizio SMTP (SendGrid, SES).

---

## 6. Structured logging (Fase 5)

Tutti i log applicativi rilevanti sono ora **JSON su stdout** (Cloud Logging
li assorbe nativamente). Chiavi principali:

- `severity` = `INFO` | `WARNING` | `ERROR`
- `logger` = `leslie.app` | `leslie.scout` | `leslie.ai`
- Payload variabile con `uid`, `provider`, `cost_usd`, `budget_usd`,
  `pct`, `error`, `lead_id`, `attempt` a seconda dell'evento.

**Eventi importanti da tenere d'occhio su Cloud Logging**:

- `quota_close_to_cap` — WARN: budget mensile ≥80% per un provider.
- `quota_monthly_exceeded` / `quota_daily_exceeded` — ERROR: scout bloccato.
- `gcs_upload_retry` / `gcs_upload_failed_all_retries` — problemi di
  persistenza (rischi di perdere le ultime scritture al cold-start).
- `gcs_download_failed` — DB non ripristinato all'avvio (nuova istanza
  ricomincerà da zero).

Livello configurabile via `LESLIE_LOG_LEVEL` env (default `INFO`).

---

## 7. Test automatici (Fase 5)

```
cd agency_os
./venv314/bin/pytest tests/
```

Copre: schema/migrazioni, transizioni di stato, follow-ups, dashboard,
draft-email, scout end-to-end mockato, quota block, scraper (email + team
pages), classificazione email generica.

**Esegui i test prima di ogni deploy** — evitano il "salto di fede" citato
in `scopo_e_gap.md`.

---

## 8. Deploy

Nulla cambia rispetto alla Fase 1:

```
cd agency_os
./deploy.sh   # o: ./deploy.sh <PROJECT_ID> <REGION>
```

Note critiche (non violare):

- `--workers 1 --threads 8` **obbligatorio**: cache AI, worker GCS e flag
  dirty assumono processo singolo.
- `--max-instances 1` **obbligatorio**: nessun `if-generation-match` su GCS,
  concorrenza = last-write-wins → potenziale perdita dati.
- `SECRET_KEY` **richiesta**: senza, l'app si rifiuta di partire (fail-fast
  contro il "in prod senza secret").

---

## 9. Impostazioni budget / limiti (via env var Cloud Run)

| Variabile                       | Default | Cosa fa                                                           |
| ------------------------------- | ------: | ----------------------------------------------------------------- |
| `PLACES_MONTHLY_BUDGET_USD`     |     `5` | Cap mensile hard su Google Places (blocca scout).                 |
| `DEEPSEEK_MONTHLY_BUDGET_USD`   |     `2` | Cap mensile hard su DeepSeek.                                     |
| `PLACES_DAILY_LIMIT`            |  `1000` | Limite chiamate/giorno Places.                                    |
| `DEEPSEEK_DAILY_LIMIT`          |   `200` | Limite chiamate/giorno DeepSeek.                                  |
| `SCOUT_MIN_INTERVAL_SECONDS`    |    `20` | Rate limit per-utente sull'endpoint scout.                        |
| `LESLIE_LOG_LEVEL`              |  `INFO` | `INFO` \| `WARNING` \| `ERROR` (JSON su stdout).                  |
| `PLACES_TEXTSEARCH_COST_PER_1K` |    `32` | Pricing pubblico Google 2025 (aggiorna se cambia).                |
| `PLACES_DETAILS_COST_PER_1K`    |    `20` | idem.                                                             |

Aggiornali su Cloud Run senza redeploy:
`gcloud run services update leslie-agency-os --update-env-vars KEY=VALUE`.

---

## 10. Runbook problemi comuni

| Sintomo                                                        | Cosa fare                                                                                                                                              |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| "Scout bloccato" con toast rate-limit                          | Aspetta 20s (o abbassa `SCOUT_MIN_INTERVAL_SECONDS`). Se scatta il cap mensile, cambia mese o alza `*_MONTHLY_BUDGET_USD`.                             |
| Widget costo rosso da giorni                                   | Verifica su Cloud Logging gli eventi `quota_close_to_cap`. Alza il budget solo se giustificato; considera aggressive-throttling di `max_results`.      |
| "Sync GCS: Errore" nell'indicator                              | Cloud Run logs → filtra `severity=ERROR AND logger="leslie.app" AND "gcs"`. Solitamente permessi service-account su bucket. Riprova con Force Sync.    |
| Lead premium non compare più negli scout                       | Query esaurita per l'utente (`is_exhausted=1`). Verifica in tab Ricerche o cambia pesi in tab Settori.                                                  |
| Email drafted "cached=true" ma vuoi rigenerare                 | Nel modal → click "Rigenera" (chiama con `force: true`).                                                                                                |
| Nuovo lead salvato manualmente ma senza selling_angle          | Click "Audit AI" nella card → aggiorna anche `contacts_json`.                                                                                          |
| Vuoi un cron report mensile via email                          | Vedi sezione 5.                                                                                                                                        |
| Test locali falliscono per DB inconsistente                    | Non condividere `agency_db.sqlite` fra dev e prod. I test usano `tmp_path` isolato — se falliscono controlla che `SECRET_KEY` sia settato in fixture.  |

---

## 11. Sostenibilità (chi lo mantiene)

- Nuove feature → aggiornare i test in `tests/`.
- Cambio invariante → aggiornare `Wiki/invarianti.md` (unica fonte di verità).
- Nuova env var → aggiornare la tabella sezione 9 di questo file.
- Prima di rifattorizzare grosso: leggi `Wiki/decisioni.md` (perché le
  scelte "strane" sono strane) e `Wiki/scopo_e_gap.md` (dove il vero valore
  è ancora incompleto).

Documento aggiornato: 2026-07-15 (delivery "chiavi in mano" — Fase 2/3/4/5
completate).
