import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import quote_plus, urljoin

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36'
}

# Email da scartare (generiche o di sistema)
JUNK_PATTERNS = [
    'noreply', 'no-reply', 'wordpress', 'example.com', 'sentry',
    'wixpress', 'test@', 'email@', 'your@', 'name@', 'user@',
    'privacy@', 'abuse@', 'postmaster@', 'webmaster@'
]

# Prefissi generici — usati per distinguere email personali (nome.cognome@)
# da caselle catch-all (info@, contact@…). Le personali valgono di più per
# arrivare a un decisore.
GENERIC_PREFIXES = {
    'info', 'contact', 'contatti', 'hello', 'ciao', 'booking', 'reservation',
    'reservations', 'prenotazioni', 'admin', 'staff', 'office', 'mail',
    'sales', 'commerciale', 'support', 'help', 'assistenza', 'segreteria',
}

# Slug delle pagine dove tipicamente compaiono team, staff, dirigenti
TEAM_SLUGS = ['team', 'staff', 'our-team', 'nostro-team', 'people', 'chi-siamo', 'about', 'about-us', 'management']

# Titoli/ruoli riconosciuti — usati per stimare che una riga contenga davvero
# un nome + un ruolo e non un caption casuale.
ROLE_KEYWORDS = [
    'ceo', 'founder', 'co-founder', 'partner', 'director', 'direttore',
    'managing', 'manager', 'president', 'presidente', 'owner', 'titolare',
    'head', 'lead', 'chief', 'responsabile', 'coordinator', 'coordinatore',
    'general', 'sales', 'marketing', 'concierge', 'brand', 'reservation',
    'proprietario', 'amministratore',
]

def _extract_emails(text):
    """Estrae email valide da un blocco di testo, filtrando spazzatura."""
    raw = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    clean = []
    for e in raw:
        e_lower = e.lower()
        if any(junk in e_lower for junk in JUNK_PATTERNS):
            continue
        if e_lower.endswith(('.png', '.jpg', '.gif', '.svg', '.css', '.js')):
            continue
        clean.append(e_lower)
    return list(set(clean))

def _fetch_page(url, timeout=10):
    """Scarica una pagina e restituisce (soup, final_url) dopo redirect, oppure (None, None)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return BeautifulSoup(resp.text, 'html.parser'), resp.url
    except Exception:
        pass
    return None, None


def _normalize_url(u):
    """Chiave di dedup: schema+host+path senza trailing slash e query/fragment."""
    if not u:
        return ''
    u = u.split('#', 1)[0].split('?', 1)[0]
    return u.rstrip('/').lower()

def _clean_text(soup):
    """Restituisce il testo pulito della pagina (senza script/style)."""
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    return re.sub(r'\s+', ' ', soup.get_text(separator=' ')).strip()


def _is_generic_email(addr):
    """Ricalca app.is_generic_email — duplicata qui per non forzare
    l'import di app.py da scraper (evita ciclo)."""
    if not addr or '@' not in addr:
        return False
    prefix = addr.split('@', 1)[0].lower().strip().split('+', 1)[0]
    return prefix in GENERIC_PREFIXES


_NAME_ROLE_RE = re.compile(
    r'([A-ZÀ-Ý][a-zà-ÿ\'’]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ\'’]+){1,2})'  # 2-3 parole capitalizzate
    r'\s*[—–\-·|,:]\s*'                                          # separatore
    r'([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\'’&/]{1,60})',                    # ruolo (min 2 char)
)


def _extract_people(soup):
    """Estrae possibili (nome, ruolo, email) da una pagina team/about.
    Euristica volutamente semplice: cerca coppie "Nome Cognome — Ruolo" nei
    tag h2/h3/h4/p/li e collega la mail più vicina se contiene un token del
    nome (es. mario.rossi@)."""
    people = []
    if not soup:
        return people
    text_nodes = []
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'p', 'li', 'div']):
        s = tag.get_text(' ', strip=True)
        if not s or len(s) > 200:
            continue
        text_nodes.append(s)

    page_emails = _extract_emails(str(soup))
    for line in text_nodes:
        m = _NAME_ROLE_RE.search(line)
        if not m:
            continue
        name = m.group(1).strip()
        role = m.group(2).strip().rstrip('.').rstrip(',')
        role_lc = role.lower()
        if not any(k in role_lc for k in ROLE_KEYWORDS):
            continue
        # Cerca la mail più plausibile per questa persona
        tokens = [t.lower() for t in re.split(r'\s+', name) if len(t) > 1]
        matched_email = None
        for e in page_emails:
            local = e.split('@', 1)[0].lower()
            if any(t in local for t in tokens):
                matched_email = e
                break
        people.append({
            'name': name,
            'role': role,
            'email': matched_email or '',
            'is_generic_email': _is_generic_email(matched_email) if matched_email else False,
        })
    # Dedup su (nome, ruolo)
    dedup = {}
    for p in people:
        dedup[(p['name'].lower(), p['role'].lower())] = p
    return list(dedup.values())

def scrape_lead_site(url):
    """Scrapa un sito e restituisce un dizionario con dati estratti e SEO.
    
    Returns: {
        'content': str,
        'emails': list,
        'metadata': {
            'title': str,
            'description': str,
            'keywords': str,
            'h1': list,
            'preview_url': str
        }
    }
    """
    soup, final_url = _fetch_page(url)
    if not soup:
        return None

    # Metadata extraction
    meta_desc = soup.find('meta', attrs={'name': 'description'})
    meta_keys = soup.find('meta', attrs={'name': 'keywords'})
    h1s = [h1.get_text().strip() for h1 in soup.find_all('h1')]

    metadata = {
        'title': soup.title.string if soup.title else '',
        'description': meta_desc['content'] if meta_desc and 'content' in meta_desc.attrs else '',
        'keywords': meta_keys['content'] if meta_keys and 'content' in meta_keys.attrs else '',
        'h1': h1s[:3],
        'preview_url': f"https://api.microlink.io/?url={quote_plus(url)}&screenshot=true&embed=screenshot.url"
    }

    full_html = str(soup)
    all_emails = _extract_emails(full_html)
    text = _clean_text(soup)
    people = _extract_people(soup)

    # Sub-pagine contatto/about + team — molti siti rediragono slug inesistenti
    # alla homepage. Il controllo su final_url evita duplicazioni che, senza
    # dedup, fanno saturare i 5000 char di taglio e cancellano il contenuto
    # utile delle pagine contatto/team.
    seen_urls = {_normalize_url(final_url)}
    base = url.rstrip('/')
    slugs = ['contact', 'contatti', 'chi-siamo', 'about', 'about-us'] + TEAM_SLUGS
    for slug in dict.fromkeys(slugs):  # dedup preservando ordine
        sub_soup, sub_final = _fetch_page(f"{base}/{slug}")
        if not sub_soup:
            continue
        norm = _normalize_url(sub_final)
        if norm in seen_urls:
            continue
        seen_urls.add(norm)
        all_emails.extend(_extract_emails(str(sub_soup)))
        text += ' ' + _clean_text(sub_soup)
        people.extend(_extract_people(sub_soup))

    all_emails = list(set(all_emails))
    # Dedup finale delle persone (le pagine team spesso ripetono le stesse card)
    dedup_people = {}
    for p in people:
        dedup_people[(p['name'].lower(), p['role'].lower())] = p

    return {
        'content': text[:5000],
        'emails': all_emails,
        'people': list(dedup_people.values()),
        'metadata': metadata
    }


def scrape_for_contacts(url):
    """Versione leggera: restituisce solo le email trovate su un sito.
    Dedup post-redirect per non ri-scaricare la homepage 5 volte."""
    soup, final_url = _fetch_page(url)
    if not soup:
        return []

    emails = _extract_emails(str(soup))
    seen_urls = {_normalize_url(final_url)}

    base = url.rstrip('/')
    for slug in ['contact', 'contatti', 'about', 'chi-siamo', 'get-in-touch']:
        sub, sub_final = _fetch_page(f"{base}/{slug}")
        if not sub:
            continue
        norm = _normalize_url(sub_final)
        if norm in seen_urls:
            continue
        seen_urls.add(norm)
        emails.extend(_extract_emails(str(sub)))

    return list(set(emails))
