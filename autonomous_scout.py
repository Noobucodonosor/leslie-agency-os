import os
import sqlite3
import random
import requests
import time
from urllib.parse import quote_plus
from scraper import scrape_lead_site
from ai_analyzer import analyze_spending_capacity, generate_json
from logging_setup import get_logger
import json

from config import DB_PATH
GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
log = get_logger("leslie.scout")


def _load_client_profile(scope_id):
    """Risolvi il profilo dal tenant key `owner::slug` senza dipendere dal request context."""
    if '::' not in scope_id:
        return {}
    owner, slug = scope_id.split('::', 1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM client_workspaces WHERE owner_id=? AND slug=? AND archived=0",
            (owner, slug),
        ).fetchone()
        return dict(row) if row else {}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()

def run_scout_cycle(user_id='dany', query_obj=None):
    """Esegue un ciclo completo e ritorna il riepilogo (Sincrono)."""
    gen = stream_scout_cycle(user_id, query_obj)
    final_result = None
    for update in gen:
        if update.get('type') == 'final':
            final_result = update['data']
    return final_result

def _select_smart_query(user_id, client_profile, query_pools):
    """Seleziona una query prioritaria basata sul profilo cliente attivo, nicchie apprese e query non esaurite."""
    import re
    exhausted = set()
    learned_candidates = []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        exhausted = {
            r["query"]
            for r in conn.execute(
                "SELECT query FROM search_history WHERE user_id=? AND is_exhausted=1",
                (user_id,),
            ).fetchall()
        }
        for row in conn.execute(
            "SELECT niche_key, suggested_queries FROM learned_intelligence WHERE user_id=? ORDER BY confidence DESC LIMIT 5",
            (user_id,),
        ).fetchall():
            for q in json.loads(row["suggested_queries"] or "[]"):
                if q and q not in exhausted:
                    learned_candidates.append((f"🎯 Nicchia: {row['niche_key']}", q))
        conn.close()
    except Exception:
        pass

    # 1. Priorità: query dal profilo cliente attivo (ideal_partner + geography)
    if client_profile:
        geography = client_profile.get("geography") or "Portofino"
        terms = [
            t.strip()
            for t in re.split(r"[,;\n]+", client_profile.get("ideal_partner") or "")
            if t.strip()
        ]
        profile_queries = [f"{t} {geography}" for t in terms[:8] if f"{t} {geography}" not in exhausted]
        if profile_queries:
            cat = f"🎯 {client_profile.get('name', 'Cliente')}"
            return cat, random.choice(profile_queries), profile_queries, "profile"

    # 2. Seconda scelta: query apprese non esaurite
    if learned_candidates:
        cat, q_text = random.choice(learned_candidates)
        all_q = [q for _, q in learned_candidates]
        return cat, q_text, all_q, "learned"

    # 3. Pool standard filtrando esaurite e categorie incompatibili col business_type
    btype = (client_profile or {}).get("business_type")
    valid_cats = [
        c for c in query_pools.keys()
        if not (btype == "hospitality" and (c.startswith("🏨") or c.startswith("⛵")))
    ] or list(query_pools.keys())
    cat = random.choice(valid_cats)
    available = [q for q in query_pools[cat] if q not in exhausted] or list(query_pools[cat])
    return cat, random.choice(available), list(query_pools[cat]), "random"


def stream_scout_cycle(user_id='dany', query_obj=None, max_results=10, min_score=0):
    """
    Generator che esegue lo scouting e yielda messaggi di progresso dettagliati.

    Args:
        user_id: ID utente
        query_obj: query specifica (opzionale, altrimenti smart/random)
        max_results: quanti risultati Google Places analizzare (1-20)
        min_score: soglia minima score per salvare (0 = salva tutti)
    """
    if not GOOGLE_API_KEY:
        yield {"type": "error", "message": "API Key Google Maps mancante"}
        return

    from config import QUERY_POOLS
    client_profile = _load_client_profile(user_id)

    # --- STEP 1: Selezione Query ---
    if not query_obj:
        cat, q_text, cat_queries, sel_method = _select_smart_query(user_id, client_profile, QUERY_POOLS)
        pool_size = len(cat_queries)
        total_categories = len(QUERY_POOLS)
        total_queries = sum(len(v) for v in QUERY_POOLS.values())

        yield {
            "type": "query_selected",
            "data": {
                "query": q_text,
                "category": cat,
                "selection_method": sel_method,
                "pool_size": pool_size,
                "total_categories": total_categories,
                "total_queries_available": total_queries,
                "all_queries_in_category": cat_queries
            }
        }
    else:
        q_text = query_obj['query']
        cat = query_obj.get('category', 'Auto-Scout')
        yield {
            "type": "query_selected",
            "data": {
                "query": q_text,
                "category": cat,
                "selection_method": "manual",
                "pool_size": 0,
                "total_categories": 0,
                "total_queries_available": 0,
                "all_queries_in_category": []
            }
        }

    yield {"type": "status", "message": f"Avvio Auto-Scout per: {q_text} ({cat})"}

    # --- STEP 2: Google Places Search ---
    from app import track_api_call, ensure_quota, UsageLimitExceeded
    try:
        ensure_quota("places")
    except UsageLimitExceeded as e:
        yield {"type": "error", "message": f"Soglia giornaliera Google Places raggiunta ({e.today}/{e.limit}). Riprova domani o aumenta PLACES_DAILY_LIMIT."}
        return

    url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={quote_plus(q_text)}&key={GOOGLE_API_KEY}"
    try:
        raw_response = requests.get(url, timeout=10).json()
        all_places = raw_response.get('results', [])
        places = all_places[:max_results]
        track_api_call("places", "textsearch")
    except Exception as e:
        yield {"type": "error", "message": f"Errore ricerca Google: {e}"}
        return

    yield {
        "type": "places_results",
        "data": {
            "total_found": len(all_places),
            "analyzing": len(places),
            "max_requested": max_results,
            "query": q_text,
            "estimated_api_calls": 1 + len(places),  # 1 textsearch + N details
        }
    }

    found_premium = 0
    found_total = 0
    new_count = 0
    analyzed_with_site = 0
    conn = sqlite3.connect(DB_PATH)

    # Siti già noti per questo utente — servono a calcolare is_exhausted onestamente
    known_websites = {
        row[0] for row in conn.execute(
            "SELECT website FROM leads WHERE user_id=? AND website IS NOT NULL",
            (user_id,)
        ).fetchall()
    }

    for i, p in enumerate(places):
        name = p.get('name')
        place_id = p.get('place_id', '')

        yield {
            "type": "analysis_start",
            "data": {
                "index": i + 1,
                "total": len(places),
                "name": name,
                "address": p.get('formatted_address', ''),
                "place_id": place_id,
                "rating": p.get('rating', 0),
                "user_ratings_total": p.get('user_ratings_total', 0),
                "types": p.get('types', [])
            }
        }

        # --- Details API ---
        try:
            det_url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place_id}&fields=website,formatted_phone_number,url&key={GOOGLE_API_KEY}"
            det = requests.get(det_url, timeout=10).json().get('result', {})
            track_api_call("places", "details")
        except Exception as e:
            yield {"type": "analysis_skip", "data": {"name": name, "reason": f"Errore API details: {e}", "index": i + 1}}
            continue

        website = det.get('website', '')
        phone = det.get('formatted_phone_number', '')

        if not website:
            yield {"type": "analysis_skip", "data": {"name": name, "reason": "Nessun sito web trovato", "index": i + 1}}
            continue

        if any(s in website for s in ["facebook.com", "instagram.com", "twitter.com"]):
            yield {"type": "analysis_skip", "data": {"name": name, "reason": f"Sito social non idoneo: {website}", "index": i + 1}}
            continue

        analyzed_with_site += 1
        if website not in known_websites:
            new_count += 1
            known_websites.add(website)

        yield {
            "type": "scraping_start",
            "data": {"name": name, "website": website, "index": i + 1}
        }

        # --- Scraping ---
        scrape_data = scrape_lead_site(website) if website else None
        if not isinstance(scrape_data, dict):
            scrape_data = {}

        text_content = scrape_data.get('content', '')
        emails = scrape_data.get('emails', [])
        seo_meta = scrape_data.get('metadata', {})

        yield {
            "type": "scrape_result",
            "data": {
                "name": name,
                "website": website,
                "index": i + 1,
                "content_length": len(text_content),
                "content_preview": text_content[:500] if text_content else "(nessun contenuto estratto)",
                "emails_found": emails,
                "seo": {
                    "title": seo_meta.get('title', ''),
                    "description": seo_meta.get('description', ''),
                    "keywords": seo_meta.get('keywords', ''),
                    "h1": seo_meta.get('h1', [])
                },
                "pages_scraped": ["homepage", "contact", "contatti", "chi-siamo", "about", "about-us"]
            }
        }

        # --- AI Analysis ---
        yield {
            "type": "ai_analysis_start",
            "data": {"name": name, "index": i + 1, "content_sent_chars": min(len(text_content), 4000)}
        }

        try:
            analysis = analyze_spending_capacity(name, text_content, client_profile)
        except TypeError:
            # Compatibilità con integrazioni/test che espongono ancora la firma storica.
            analysis = analyze_spending_capacity(name, text_content)
        score = analysis.get('score', 0)
        reason = analysis.get('reason', 'Nessuna motivazione fornita.')
        client_type = analysis.get('client_type', 'Target generico')
        selling_angle = analysis.get('selling_angle', 'Approccio standard Portofino.')
        is_premium_flag = 1 if analysis.get('is_premium', False) else 0
        fit_dimensions = analysis.get('fit_dimensions') if isinstance(analysis.get('fit_dimensions'), dict) else {}
        fit_confidence = str(analysis.get('confidence', ''))
        fit_evidence = analysis.get('evidence') if isinstance(analysis.get('evidence'), list) else []

        yield {
            "type": "ai_analysis_result",
            "data": {
                "name": name,
                "index": i + 1,
                "score": score,
                "reason": reason,
                "client_type": client_type,
                "selling_angle": selling_angle,
                "is_premium": is_premium_flag == 1,
                "raw_analysis": analysis
            }
        }

        # --- Decisione: salvare o no ---
        should_save = score >= min_score
        is_premium = score >= 7

        if not should_save:
            yield {
                "type": "analysis_complete",
                "data": {
                    "name": name, "index": i + 1, "score": score,
                    "decision": "filtered",
                    "reason": f"Score {score}/10 sotto la soglia minima di {min_score}",
                    "client_type": client_type
                }
            }
            continue

        audit_text = f"Score: {score}/10. {reason}"

        # Audit profondo solo per premium
        if is_premium:
            try:
                audit_prompt = f"""
                Analizza '{name}'. Genera report JSON:
                {{
                  "audit": "- 🎯 TARGET: ...\\n- 💎 POSIZIONAMENTO: ...\\n- 💡 ANGOLO DI VENDITA: ...",
                  "niche": "nome breve",
                  "description": "breve descrizione strategica (max 15 parole)",
                  "queries": ["q1", "q2", "q3"]
                }}
                Il partner deve essere valutato per questo cliente: {json.dumps(client_profile, ensure_ascii=False)}
                Testo: {text_content[:3000]}
                """
                audit_data = generate_json(audit_prompt)
                audit_text = audit_data.get('audit', audit_text)
                desc = audit_data.get('description', 'Espansione nicchia automatizzata.')
                saturation = random.randint(20, 60)

                from app import get_db_conn
                tmp_conn = get_db_conn()
                tmp_conn.execute('''INSERT INTO learned_intelligence (user_id, niche_key, description, market_saturation, suggested_queries)
                                 VALUES (?, ?, ?, ?, ?)
                                 ON CONFLICT(user_id, niche_key) DO UPDATE SET
                                     confidence = confidence + 1,
                                     description = excluded.description
                ''', (user_id, audit_data.get('niche','Generica'), desc, saturation, json.dumps(audit_data.get('queries',[]))))
                tmp_conn.commit()
                tmp_conn.close()

                yield {
                    "type": "niche_learned",
                    "data": {
                        "name": name,
                        "niche": audit_data.get('niche', 'Generica'),
                        "description": desc,
                        "suggested_queries": audit_data.get('queries', []),
                        "market_saturation": saturation
                    }
                }
            except Exception as e:
                # Un fallimento dell'audit (AI down, quota, JSON malformato, DB lock)
                # non deve MAI far crollare l'intero ciclo di scout: perderemmo
                # tutti i lead accumulati. Logghiamo, cadiamo in audit_text base.
                yield {"type": "log", "message": f"[audit] fallito per '{name}': {e}"}

        # --- Salvataggio lead ---
        email_str = ",".join(emails) if emails else ""
        people = scrape_data.get('people') or []
        first_email = emails[0] if emails else ''
        from app import is_generic_email
        generic_flag = 1 if is_generic_email(first_email) else 0
        try:
            conn.execute('''INSERT INTO leads (user_id, company_name, website, phone, email, address, source_query, status, ai_audit, selling_angle, is_premium, seo_data, is_generic_email, contacts_json, category, fit_score, fit_reason, fit_dimensions_json, fit_confidence, fit_evidence_json, analyzed_at, analysis_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'saved', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'fit-v2')
                ON CONFLICT(user_id, website) DO UPDATE SET ai_audit=excluded.ai_audit, selling_angle=excluded.selling_angle, is_premium=excluded.is_premium, seo_data=excluded.seo_data, is_generic_email=excluded.is_generic_email, contacts_json=excluded.contacts_json, category=excluded.category, fit_score=excluded.fit_score, fit_reason=excluded.fit_reason, fit_dimensions_json=excluded.fit_dimensions_json, fit_confidence=excluded.fit_confidence, fit_evidence_json=excluded.fit_evidence_json, analyzed_at=CURRENT_TIMESTAMP, analysis_version='fit-v2'
            ''', (user_id, name, website, phone,
                  email_str, p.get('formatted_address'), q_text, audit_text, selling_angle, 1 if is_premium else 0, json.dumps(seo_meta), generic_flag, json.dumps(people), cat,
                  score, reason, json.dumps(fit_dimensions), fit_confidence, json.dumps(fit_evidence)))
            # Commit per-lead: se un'iterazione successiva esplode, i lead già
            # salvati sopravvivono al rollback implicito.
            conn.commit()

            found_total += 1
            if is_premium:
                found_premium += 1

            yield {
                "type": "lead_found",
                "data": {
                    "name": name,
                    "website": website,
                    "score": score,
                    "audit": audit_text,
                    "email": email_str,
                    "seo": seo_meta,
                    "is_premium": is_premium,
                    "selling_angle": selling_angle,
                    "client_type": client_type,
                    "reason": reason,
                    "phone": phone,
                    "address": p.get('formatted_address', '')
                }
            }

            yield {
                "type": "analysis_complete",
                "data": {
                    "name": name, "index": i + 1, "score": score,
                    "decision": "premium" if is_premium else "saved",
                    "reason": reason,
                    "client_type": client_type
                }
            }
        except sqlite3.Error as e:
            yield {"type": "log", "message": f"[save] lead insert failed: {e}"}

    # Update history — una query è "esaurita" solo se ha prodotto risultati
    # (almeno un place con sito valido) ma nessuno era nuovo per questo utente.
    is_exhausted = 1 if (analyzed_with_site > 0 and new_count == 0) else 0
    conn.execute('''INSERT INTO search_history (user_id, query, results_count, new_count, is_exhausted, last_run)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, query) DO UPDATE SET
            results_count=excluded.results_count,
            new_count=excluded.new_count,
            is_exhausted=excluded.is_exhausted,
            last_run=excluded.last_run
    ''', (user_id, q_text, len(places), new_count, is_exhausted))

    conn.commit()
    conn.close()

    yield {
        "type": "final",
        "data": {
            "query": q_text,
            "category": cat,
            "premium_found": found_premium,
            "total_saved": found_total,
            "total_analyzed": len(places),
            "total_available": len(all_places) if 'all_places' in dir() else len(places)
        }
    }

if __name__ == "__main__":
    run_scout_cycle()
