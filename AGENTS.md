# AGENTS.md — Contesto & Linee Guida per Agenti AI e Sviluppatori (`leslie-agency-os`)

> **REGOLA AUREA (MANDATORY):**
> Prima di modificare lo schema SQLite, aggiungere endpoint Flask o modificare la logica di scouting/commerce, **DEVI leggere [`docs/adr/README.md`](./docs/adr/README.md)**, gli ADR in [`docs/adr/`](./docs/adr/) e la documentazione tecnica in [`Wiki/`](./Wiki/).

---

## 1. Panoramica del Progetto

- **Nome Progetto:** `Leslie — Agency OS (B2B AI Scouting & CRM)`
- **Repository:** `Noobucodonosor/leslie-agency-os`
- **Vetrina Noobucodonosor:** Servizio AI prescaricato (`https://noobucodonosor-hub-664856461640.us-central1.run.app/?app=leslie`, profilo `compact-json`) + Anteprima GitHub Pages (`https://noobucodonosor.github.io/leslie-agency-os/`).
- **Stack Tecnologico:**
  - **Backend:** Python 3.11+, Flask, Gunicorn (`--workers 1 --threads 8`), SQLite WAL sincronizzato su Google Cloud Storage
  - **AI & Discovery:** DeepSeek API (`OpenAI-compatible`), Google Places API, `BeautifulSoup4`
  - **Frontend:** HTML5 (`templates/`), CSS3 (`static/style.css`), Vanilla JS (`static/app.js`)
  - **Test:** `pytest` (`tests/`)

---

## 2. Mappa dei File Principali

| File / Cartella | Ruolo |
| :--- | :--- |
| [`docs/adr/README.md`](./docs/adr/README.md) | Indice degli ADR, diagramma architetturale e Master Task Backlog. |
| [`Wiki/`](./Wiki/) | Documentazione di dominio dettagliata (`architettura.md`, `schema_db.md`, `invarianti.md`, `decisioni.md`). |
| [`app.py`](./app.py) | Entry point Flask, persistenza SQLite WAL + worker GCS, endpoint lead/funnel/clienti. |
| [`autonomous_scout.py`](./autonomous_scout.py) | Generator streaming SSE per lo scouting B2B multi-nicchia. |
| [`ai_analyzer.py`](./ai_analyzer.py) | Qualificazione AI JSON e scoring capacità di spesa (0–10). |
| [`commerce_engine.py`](./commerce_engine.py) | Blueprint CRM/Commerce B2B, offerte pubbliche e portale partner. |
| [`scraper.py`](./scraper.py) | Estrazione statica contatti, email e metadata SEO da siti web. |
| [`static/app.js`](./static/app.js) | Logica frontend SPA multi-cliente e streaming SSE. |
| [`tests/`](./tests/) | Suite di test end-to-end mockati (`pytest`). |

---

## 3. Comandi Rapidi di Sviluppo e Test

```bash
# Creazione ambiente virtuale e esecuzione test
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
FLASK_ENV=development .venv/bin/pytest -q

# Controllo sintassi Python
python3 -m compileall -q app.py autonomous_scout.py ai_analyzer.py commerce_engine.py scraper.py config.py tests

# Verifica pre-release ADR e sicurezza
python3 ~/.gemini/config/skills/adr-github-methodology/scripts/pre_release_check.py .
```
