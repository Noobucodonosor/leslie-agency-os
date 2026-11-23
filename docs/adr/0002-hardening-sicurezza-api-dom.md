# ADR-0002: Hardening Sicurezza Endpoint Flask, Validazione JSON, Security Headers e Sanitizzazione DOM

- **Stato:** Accettato
- **Priorità:** P0 — Critica
- **Data:** 2026-11-23
- **Autori:** Dany (`Noobucodonosor`)
- **Fonti:** `app.py`, `static/app.js`, `tests/test_funnel_endpoints.py`

---

## 1. Contesto e Problema

Gli endpoint di mutazione lead (`/api/leads/save`, `/api/leads/<id>/status`, `/api/leads/<id>/notes`) accedevano direttamente a `request.json` senza `get_json(silent=True)` né verifica del tipo `dict`, rischiando errori `500` o leak di eccezioni interne su payload malformati. Inoltre nel frontend (`static/app.js`), `showUndoToast` interpolava `message` in `innerHTML` senza `escapeHTML()`.

---

## 2. Decisione Architetturale

1. **Limiti Payload & Validazione JSON (`app.py`):**
   - `app.config["MAX_CONTENT_LENGTH"] = 256 * 1024` (256 KB).
   - Uso sistematico di `request.get_json(silent=True)` con verifica `isinstance(payload, dict)` e campo `name` obbligatorio in `/api/leads/save`, restituendo `400 Bad Request` strutturato senza esporre stacktrace interni.
2. **Security Headers HTTP (`@app.after_request`):**
   - Impostazione automatica di `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN` e `Referrer-Policy: strict-origin-when-cross-origin`.
3. **Sanitizzazione DOM (`static/app.js`):**
   - Sanitizzazione tramite `escapeHTML(message)` in `showUndoToast()`.

---

## 3. Task Operativi Collegati

- [x] **TASK-SEC-01**: Audit sicurezza endpoint Flask, SSE e sanitizzazione input (`closes #3`)
