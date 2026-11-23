# Scraper

Modulo: `scraper.py`. Basato su `requests` + `BeautifulSoup4` (parser `html.parser`).

Nessuna dipendenza da headless browser (Playwright/Selenium). **Solo HTML statico**.
Siti fortemente SPA (React app senza SSR) escono con contenuto minimale.

## Funzioni pubbliche

### `scrape_lead_site(url)` — usata dallo scout

Ritorna:
```python
{
    'content': str,      # testo pulito, max 5000 char
    'emails': [str],     # email deduplicate, filtrate
    'metadata': {
        'title': str,
        'description': str,   # meta description
        'keywords': str,      # meta keywords
        'h1': [str],          # primi 3 H1
        'preview_url': str    # microlink.io screenshot URL
    }
}
```

Se la homepage non è raggiungibile → `None`.

### `scrape_for_contacts(url)` — versione light

Ritorna solo la lista di email. Usata dalla **live search** in `app.py:913` per email enrichment veloce (10 in parallelo).

Differenza: `scrape_lead_site` cerca contatti in slug `['contact', 'contatti', 'chi-siamo', 'about', 'about-us']`.
`scrape_for_contacts` cerca in `['contact', 'contatti', 'about', 'chi-siamo', 'get-in-touch']`.

Nota: **quasi identici** ma non allineati. Piccolo debito tecnico — vedi [[invarianti]].

## Come funziona lo scraping delle pagine contatto

Per ogni slug, prova `f"{base_url}/{slug}"`. Non fa nessun *sitemap discovery*.
Se il sito ha la pagina contatti in `/en/contact-us/`, non la trova.

Motivo del design: **90% dei siti di piccole aziende italiane** ha `/contatti` o `/contact`. Coprire questo 90% è >> che coprire tutti al 100%. Trade-off cosciente.

## Estrazione email

Regex naïve:
```python
r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
```

Filtri applicati (`_extract_emails` in `scraper.py:18`):
- **Junk patterns**: `noreply`, `wordpress`, `example.com`, `test@`, `webmaster@`, ecc.
- **Estensioni immagine/asset**: se l'email finisce con `.png`, `.jpg`, `.css`, `.js` — è un falso positivo (regex ha matchato un URL immagine mascherato)

Dedup con `set()`, tutte lowercase.

Le pattern di junk sono duplicate anche in `app.py:600` sotto `junk_email_patterns` nelle settings utente. Il codice **non usa** le settings — è un residuo di quando i pattern erano configurabili. Vedi [[invarianti]].

## Estrazione testo

- Rimuove `<script>`, `<style>`, `<noscript>`
- `soup.get_text(separator=' ')`
- Normalizza whitespace con `re.sub(r'\s+', ' ', ...)`
- **Trunca a 5000 caratteri** dopo aggregazione homepage + contact pages

Il taglio a 5000 è importante: il prompt AI accetta solo `text_content[:4000]` — vedi [[ai_analyzer]]. Se il testo è >4000 char, l'analisi si basa sui primi 4000 (che include header, hero, meno il footer).

Conseguenza pratica: siti con hero minimalista ("Welcome" + video) escono con score basso perché l'AI non ha materia. È un problema noto.

## Timeout & user-agent

- Timeout: 10s per request (`_fetch_page`)
- User-Agent: Chrome 120 fisso (`scraper.py:5-9`)
- `allow_redirects=True`
- Nessun retry sul singolo fetch — se cade, cade.

## Cosa NON fa

- Non rispetta `robots.txt` (l'uso è manuale su lead, non crawling di massa)
- Non gestisce cookie banner, JavaScript, lazy loading
- Non fa OCR su immagini
- Non estrae numeri di telefono (già li abbiamo da Places)
- Non estrae social handle
- Non fa dedup **cross-site** delle email (se la stessa email è su 2 siti, appare in entrambi)

## Uso da `process_unscraped_leads`

Funzione batch (`scraper.py:102`) che scrapa tutti i lead con `status='new'`. **Al momento non è chiamata da nessuna route attiva** — è codice orfano, residuo di un flusso precedente. Vedi [[invarianti]].
