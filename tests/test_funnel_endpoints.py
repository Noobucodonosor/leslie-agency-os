"""Save lead → cambio stato → verifica timestamp + follow-ups + dashboard."""
import sqlite3
import json


def _save_lead(as_owner, name="Hotel Splendido", website="https://splendido.example", email="info@splendido.example"):
    return as_owner.post('/api/leads/save', data=json.dumps({
        'name': name, 'website': website, 'phone': '+390185111',
        'email': email, 'address': 'Portofino', 'query': 'luxury hotel Portofino',
        'category': '🏨 Hotel & Resort',
    }), content_type='application/json')


def test_save_lead_sets_generic_email_flag(as_owner, app_with_db):
    r = _save_lead(as_owner, email="info@splendido.example")
    assert r.status_code == 200
    conn = sqlite3.connect(app_with_db.DB_PATH)
    row = conn.execute("SELECT is_generic_email FROM leads WHERE user_id='owner'").fetchone()
    conn.close()
    assert row[0] == 1


def test_save_lead_personal_email_not_flagged(as_owner, app_with_db):
    r = _save_lead(as_owner, website="https://a.example", email="mario.rossi@a.example")
    assert r.status_code == 200
    conn = sqlite3.connect(app_with_db.DB_PATH)
    row = conn.execute("SELECT is_generic_email FROM leads WHERE user_id='owner'").fetchone()
    conn.close()
    assert row[0] == 0


def test_personal_notes_do_not_overwrite_ai_audit(as_owner, app_with_db):
    _save_lead(as_owner, website="https://notes.example")
    conn = sqlite3.connect(app_with_db.DB_PATH)
    lid = conn.execute("SELECT id FROM leads").fetchone()[0]
    conn.execute("UPDATE leads SET ai_audit='Analisi Leslie' WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    response = as_owner.post(f'/api/leads/{lid}/notes', json={'notes': 'Richiamare venerdì'})
    assert response.status_code == 200
    conn = sqlite3.connect(app_with_db.DB_PATH)
    row = conn.execute("SELECT ai_audit, user_notes FROM leads WHERE id=?", (lid,)).fetchone()
    conn.close()
    assert row == ('Analisi Leslie', 'Richiamare venerdì')


def test_status_transition_sets_timestamp(as_owner, app_with_db):
    _save_lead(as_owner)
    conn = sqlite3.connect(app_with_db.DB_PATH)
    lid = conn.execute("SELECT id FROM leads WHERE user_id='owner'").fetchone()[0]
    conn.close()

    r = as_owner.post(f'/api/leads/{lid}/status', data=json.dumps({'status': 'contacted'}), content_type='application/json')
    assert r.status_code == 200
    conn = sqlite3.connect(app_with_db.DB_PATH)
    row = conn.execute("SELECT status, contacted_at, replied_at FROM leads WHERE id=?", (lid,)).fetchone()
    conn.close()
    assert row[0] == 'contacted'
    assert row[1] is not None
    assert row[2] is None


def test_invalid_status_rejected(as_owner, app_with_db):
    _save_lead(as_owner)
    conn = sqlite3.connect(app_with_db.DB_PATH)
    lid = conn.execute("SELECT id FROM leads").fetchone()[0]
    conn.close()
    r = as_owner.post(f'/api/leads/{lid}/status', data=json.dumps({'status': 'bogus'}), content_type='application/json')
    assert r.status_code == 400
    assert 'allowed' in r.get_json()


def test_status_timestamp_not_overwritten_on_regression(as_owner, app_with_db):
    """Se torno da 'contacted' a 'saved' e poi di nuovo a 'contacted',
    contacted_at NON deve ripartire (la data del primo contatto è quella
    che conta per le metriche di funnel)."""
    _save_lead(as_owner)
    conn = sqlite3.connect(app_with_db.DB_PATH)
    lid = conn.execute("SELECT id FROM leads").fetchone()[0]
    conn.close()

    as_owner.post(f'/api/leads/{lid}/status', data=json.dumps({'status': 'contacted'}), content_type='application/json')
    conn = sqlite3.connect(app_with_db.DB_PATH)
    ts1 = conn.execute("SELECT contacted_at FROM leads WHERE id=?", (lid,)).fetchone()[0]
    conn.close()

    as_owner.post(f'/api/leads/{lid}/status', data=json.dumps({'status': 'saved'}), content_type='application/json')
    as_owner.post(f'/api/leads/{lid}/status', data=json.dumps({'status': 'contacted'}), content_type='application/json')
    conn = sqlite3.connect(app_with_db.DB_PATH)
    ts2 = conn.execute("SELECT contacted_at FROM leads WHERE id=?", (lid,)).fetchone()[0]
    conn.close()
    assert ts1 == ts2, "contacted_at deve preservare la prima transizione"


def test_followups_endpoint(as_owner, app_with_db):
    _save_lead(as_owner)
    conn = sqlite3.connect(app_with_db.DB_PATH)
    lid = conn.execute("SELECT id FROM leads").fetchone()[0]
    # Simuliamo un contatto di 30 giorni fa
    conn.execute("UPDATE leads SET status='contacted', contacted_at=datetime('now','-30 days') WHERE id=?", (lid,))
    conn.commit()
    conn.close()

    r = as_owner.get('/api/leads/followups?days=7')
    assert r.status_code == 200
    body = r.get_json()
    assert body['count'] == 1
    assert body['items'][0]['id'] == lid

    # Se ha già risposto → esce dai follow-up
    conn = sqlite3.connect(app_with_db.DB_PATH)
    conn.execute("UPDATE leads SET status='replied', replied_at=CURRENT_TIMESTAMP WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    r2 = as_owner.get('/api/leads/followups?days=7')
    assert r2.get_json()['count'] == 0


def test_dashboard_conversion(as_owner, app_with_db):
    # Popolo 3 lead: 1 saved, 1 partner, 1 rejected
    for i, s in enumerate(['saved', 'partner', 'rejected']):
        _save_lead(as_owner, website=f'https://a{i}.example', email=f'x{i}@a.example')
    conn = sqlite3.connect(app_with_db.DB_PATH)
    ids = [r[0] for r in conn.execute("SELECT id FROM leads ORDER BY id").fetchall()]
    conn.execute("UPDATE leads SET status='partner', partner_since=CURRENT_TIMESTAMP WHERE id=?", (ids[1],))
    conn.execute("UPDATE leads SET status='rejected' WHERE id=?", (ids[2],))
    conn.commit()
    conn.close()

    r = as_owner.get('/api/dashboard/conversion')
    assert r.status_code == 200
    data = r.get_json()
    assert data['funnel']['saved'] == 1
    assert data['funnel']['partner'] == 1
    assert data['funnel']['rejected'] == 1
    assert data['totals']['partners'] == 1


def test_dashboard_and_reports_scoped_by_user(client, app_with_db):
    # owner e partner devono vedere solo i propri lead
    client.post('/api/leads/save', json={'name': 'D', 'website': 'https://d.example', 'email': 'a@d.example'},
                headers={'X-User-ID': 'owner'})
    client.post('/api/leads/save', json={'name': 'A', 'website': 'https://a.example', 'email': 'a@a.example'},
                headers={'X-User-ID': 'partner'})
    r_owner = client.get('/api/dashboard/conversion', headers={'X-User-ID': 'owner'})
    r_ale = client.get('/api/dashboard/conversion', headers={'X-User-ID': 'partner'})
    assert r_owner.get_json()['funnel']['saved'] == 1
    assert r_ale.get_json()['funnel']['saved'] == 1


def test_security_headers_and_malformed_json_rejected(as_owner, app_with_db):
    r_empty = as_owner.post('/api/leads/save', data='not-json', content_type='application/json')
    assert r_empty.status_code == 400
    assert r_empty.headers.get('X-Content-Type-Options') == 'nosniff'
    assert r_empty.headers.get('X-Frame-Options') == 'SAMEORIGIN'

    r_missing_name = as_owner.post('/api/leads/save', json={'website': 'https://x.example'})
    assert r_missing_name.status_code == 400

