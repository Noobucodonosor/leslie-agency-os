import os
import json
from openai import OpenAI
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

_client = None

def get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            base_url="https://api.deepseek.com",
            api_key=os.getenv("DEEPSEEK_API_KEY"),
        )
    return _client

MODEL = "deepseek-chat"

# Cache in-memory per session (semplice dict per ora)
_ai_cache = {}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def generate_json(prompt):
    """Chiama DeepSeek e restituisce JSON parsato, con retry."""
    # Cache lookup semplice (basata su hash del prompt per risparmiare memoria)
    import hashlib
    prompt_hash = hashlib.md5(prompt.encode()).hexdigest()
    if prompt_hash in _ai_cache:
        return _ai_cache[prompt_hash]

    # Hard limit: blocca SUBITO se soglia giornaliera raggiunta (evita bill shock)
    try:
        from app import ensure_quota, UsageLimitExceeded, track_api_call
        ensure_quota("deepseek")
    except UsageLimitExceeded:
        raise
    except Exception:
        # Se ensure_quota non importabile (es. lib usata fuori da app), continua silenzioso
        track_api_call = None

    client = get_client()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        timeout=30  # Timeout sulla chiamata API
    )
    if track_api_call:
        try:
            track_api_call("deepseek", "chat")
        except Exception:
            pass
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    
    res = json.loads(raw)
    _ai_cache[prompt_hash] = res
    return res


def analyze_spending_capacity(company_name, text_content, client_profile=None):
    if not text_content or len(text_content) < 200:
        return {"score": 0, "reason": "Contenuto insufficiente", "is_premium": False}

    profile = client_profile or {}
    profile_context = f"""
CLIENTE PER CUI STIAMO CERCANDO PARTNER:
- Nome: {profile.get('name', 'attività turistica del territorio')}
- Tipo di attività: {profile.get('business_type', '')}
- Offerta: {profile.get('offer_name', '')}
- Mezzo o struttura: {profile.get('boat_type', '')}
- Numero di mezzi/strutture: {profile.get('inventory_count') or 1}
- Capienza massima: {profile.get('max_guests') or 'non indicata'} ospiti
- Stile esperienza: {profile.get('experience_style', '')}
- Pubblico ideale: {profile.get('target_customer', '')}
- Partner ideali: {profile.get('ideal_partner', '')}
- Esclusioni: {profile.get('exclusions', '')}
- Obiettivo: {profile.get('primary_goal', '')}
- Elementi distintivi: {profile.get('differentiators', '')}
""".strip()

    business_type = profile.get('business_type') or 'other'
    vertical_rules = {
        'nautical': "Premia chi può proporre esperienze in mare al pubblico giusto. Verifica capienza, stile, territorio e capacità di generare richieste compatibili.",
        'hospitality': "Premia chi può generare soggiorni, gruppi o prenotazioni ricorrenti. Verifica inventario, pubblico, territorio, stagionalità e compatibilità distributiva.",
        'experience': "Premia chi raggiunge lo stesso pubblico e può integrare l'esperienza in un'offerta concreta, senza sovrapporsi direttamente.",
        'other': "Premia sovrapposizione del pubblico, complementarità dell'offerta, territorio e capacità commerciale dimostrabile.",
    }
    prompt = f"""
Sei Leslie, un'analista senior di compatibilità fra partner commerciali nel turismo.
Il tuo compito è valutare questo lead esclusivamente rispetto al profilo del cliente, usando prove presenti nel sito.

{profile_context}

OBIETTIVO:
Identificare partner B2B realmente capaci di vendere l'offerta specifica del cliente.
Valuta soprattutto compatibilità tra pubblico, dimensione dei gruppi, stile dell'esperienza e territorio.

REGOLA DEL SETTORE ({business_type}):
{vertical_rules.get(business_type, vertical_rules['other'])}

RUBRICA (il totale deve corrispondere allo score 0-10):
- audience: 0-2, sovrapposizione del pubblico finale;
- geography: 0-2, compatibilità territoriale;
- capacity: 0-2, gruppi/inventario/capienza compatibili;
- positioning: 0-2, fascia e stile coerenti;
- channel: 0-2, capacità concreta di portare prenotazioni o referral.

Usa score 8-10 soltanto con almeno due evidenze specifiche. Se il sito è vago,
abbassa confidence e score. Non premiare automaticamente parole come "luxury".

AZIENDA: '{company_name}'
CONTENUTO SITO:
{text_content[:4000]}

Rispondi in formato JSON:
{{
  "score": <int 0-10 uguale alla somma delle dimensioni>,
  "reason": "<spiegazione concreta in italiano, max 35 parole>",
  "client_type": "<tipo di partner commerciale>",
  "is_premium": <bool, solo se score >= 7>,
  "fit_dimensions": {{"audience": <0-2>, "geography": <0-2>, "capacity": <0-2>, "positioning": <0-2>, "channel": <0-2>}},
  "confidence": "alta|media|bassa",
  "evidence": ["<prova breve dal sito>", "<seconda prova, se disponibile>"],
  "selling_angle": "<come presentare l'offerta specifica del cliente a questa azienda, max 20 parole>"
}}
"""
    try:
        result = generate_json(prompt)
        dimensions = result.get('fit_dimensions') if isinstance(result.get('fit_dimensions'), dict) else {}
        dimension_score = sum(max(0, min(2, int(dimensions.get(key, 0) or 0))) for key in ('audience', 'geography', 'capacity', 'positioning', 'channel'))
        if dimensions:
            result['score'] = dimension_score
        else:
            result['score'] = max(0, min(10, int(result.get('score', 0) or 0)))
        result['is_premium'] = result['score'] >= 7
        result['confidence'] = result.get('confidence') if result.get('confidence') in {'alta', 'media', 'bassa'} else 'bassa'
        result['evidence'] = result.get('evidence') if isinstance(result.get('evidence'), list) else []
        return result
    except Exception as e:
        return {"score": 0, "reason": f"Errore AI: {e}", "is_premium": False}
