"""Scout end-to-end mockato: Google Places + DeepSeek stubbati.
Verifica il ciclo streaming, il salvataggio lead e l'audit."""
import json
import sqlite3


def _fake_places_response(name="Villa Test", website="https://villa-test.example"):
    return {
        'results': [{
            'name': name,
            'place_id': 'test-place-id',
            'formatted_address': 'Portofino, Italia',
            'rating': 4.8,
            'user_ratings_total': 120,
            'types': ['lodging']
        }],
    }


def _fake_details_response(website="https://villa-test.example"):
    return {'result': {
        'website': website,
        'formatted_phone_number': '+390185111',
        'url': 'https://maps.google.com/?cid=test',
    }}


def _install_mocks(monkeypatch, ai_module_names=('ai_analyzer', 'autonomous_scout', 'app'),
                   places_json=None, details_json=None,
                   ai_spending=None, ai_json=None):
    import requests

    class FakeResp:
        def __init__(self, data): self._d = data
        def json(self): return self._d

    def fake_get(url, timeout=10):
        if 'textsearch' in url:
            return FakeResp(places_json or _fake_places_response())
        if 'details' in url:
            return FakeResp(details_json or _fake_details_response())
        # Scraper fetch — restituisci HTML minimo con qualche parola così
        # analyze_spending_capacity passa il filtro len>200
        html = '<html><head><title>Villa Test</title></head><body>' + ('luxury boutique villa Portofino heritage bespoke UHNWI ' * 20) + '</body></html>'
        return type('R', (), {'status_code': 200, 'text': html, 'url': url})()

    monkeypatch.setattr(requests, 'get', fake_get)

    # Mock DeepSeek: sostituiamo generate_json in ogni modulo che lo importa
    default_ai = ai_json or {
        'score': 8, 'reason': 'Test premium',
        'client_type': 'Luxury Concierge', 'is_premium': True,
        'selling_angle': 'Proponi charter privato weekend',
        'audit': 'Report AI test', 'niche': 'Concierge Lusso',
        'description': 'Nicchia test', 'queries': ['q1', 'q2'],
    }
    default_spending = ai_spending or default_ai

    import ai_analyzer, autonomous_scout, app as app_module
    for mod, attr in [
        (ai_analyzer, 'generate_json'),
        (autonomous_scout, 'generate_json'),
        (app_module, 'generate_json'),
    ]:
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, lambda prompt, _d=default_ai: dict(_d))
    monkeypatch.setattr(ai_analyzer, 'analyze_spending_capacity', lambda name, text, profile=None, _d=default_spending: dict(_d))
    monkeypatch.setattr(autonomous_scout, 'analyze_spending_capacity', lambda name, text, profile=None, _d=default_spending: dict(_d))


def test_scout_stream_end_to_end(app_with_db, monkeypatch):
    _install_mocks(monkeypatch)
    import autonomous_scout
    updates = list(autonomous_scout.stream_scout_cycle('owner', max_results=1, min_score=0))
    types = [u['type'] for u in updates]
    assert 'query_selected' in types
    assert 'places_results' in types
    assert any(t == 'lead_found' for t in types)
    assert types[-1] == 'final'

    # Il lead deve essere in DB con is_premium=1 e contacts_json valido
    conn = sqlite3.connect(app_with_db.DB_PATH)
    row = conn.execute("SELECT company_name, is_premium, contacts_json, category, selling_angle FROM leads WHERE user_id='owner'").fetchone()
    conn.close()
    assert row is not None
    assert row[1] == 1
    assert json.loads(row[2]) == [] or isinstance(json.loads(row[2]), list)


def test_partner_fit_score_is_derived_from_dimensions(monkeypatch):
    import ai_analyzer
    monkeypatch.setattr(ai_analyzer, 'generate_json', lambda prompt: {
        'score': 10,
        'fit_dimensions': {
            'audience': 2, 'geography': 2, 'capacity': 1,
            'positioning': 1, 'channel': 1,
        },
        'confidence': 'media',
        'evidence': ['Opera in Liguria'],
        'reason': 'Compatibilità concreta',
        'client_type': 'DMC',
        'selling_angle': 'Proponi un pacchetto',
    })
    result = ai_analyzer.analyze_spending_capacity(
        'Partner', 'contenuto verificabile ' * 30,
        {'name': 'Cosimo', 'business_type': 'hospitality'},
    )
    assert result['score'] == 7
    assert result['is_premium'] is True
    assert result['confidence'] == 'media'


def test_save_lead_endpoint_populates_flags(as_owner, app_with_db):
    r = as_owner.post('/api/leads/save', data=json.dumps({
        'name': 'A', 'website': 'https://a.example', 'email': 'info@a.example',
        'category': '🏨 Hotel', 'query': 'test',
    }), content_type='application/json')
    assert r.status_code == 200
    conn = sqlite3.connect(app_with_db.DB_PATH)
    row = conn.execute("SELECT is_generic_email, category FROM leads").fetchone()
    conn.close()
    assert row[0] == 1
    assert row[1] == '🏨 Hotel'


def test_draft_email_endpoint_caches(as_owner, app_with_db, monkeypatch):
    _install_mocks(monkeypatch, ai_json={
        'subject': 'Partnership Portofino',
        'body': 'Gentili, vi scrivo da CIBO Marine…\n\nUn saluto,\nTeam CIBO Marine — Portofino',
    })
    as_owner.post('/api/leads/save', data=json.dumps({
        'name': 'Villa X', 'website': 'https://villax.example', 'email': 'info@villax.example',
        'category': '🏨 Hotel', 'query': 'q',
    }), content_type='application/json')
    conn = sqlite3.connect(app_with_db.DB_PATH)
    lid = conn.execute("SELECT id FROM leads").fetchone()[0]
    conn.close()

    r1 = as_owner.post(f'/api/leads/{lid}/draft_email', data=json.dumps({}), content_type='application/json')
    assert r1.status_code == 200
    b1 = r1.get_json()
    assert b1['cached'] is False
    assert 'Partnership Portofino' in b1['email']

    # Seconda chiamata senza force → cached
    r2 = as_owner.post(f'/api/leads/{lid}/draft_email', data=json.dumps({}), content_type='application/json')
    assert r2.get_json()['cached'] is True


def test_ai_analyzer_scoring_low_input(app_with_db):
    """Con contenuto insufficiente lo score è 0."""
    from ai_analyzer import analyze_spending_capacity
    out = analyze_spending_capacity('Foo', 'poco testo')
    assert out['score'] == 0
    assert out['is_premium'] is False


def test_quota_blocks_when_over_budget(app_with_db, monkeypatch):
    """Se il costo mensile supera il budget, ensure_quota alza UsageLimitExceeded."""
    import app as m
    monkeypatch.setattr(m, 'PLACES_MONTHLY_BUDGET_USD', 0.001)  # praticamente zero
    # Iniettiamo una call che porta il costo oltre soglia
    m.track_api_call('places', 'textsearch', n=100)
    import pytest
    with pytest.raises(m.UsageLimitExceeded):
        m.ensure_quota('places')
