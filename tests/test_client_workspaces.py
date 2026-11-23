def test_create_and_list_client_workspace(client):
    created = client.post('/api/clients', headers={'X-User-ID': 'owner'}, json={
        'name': 'Villa Aurora',
        'sector': 'Hospitality di lusso',
        'geography': 'Liguria',
        'offer_name': 'Tour privato al tramonto',
        'boat_type': 'Barca in legno',
        'max_guests': 6,
        'experience_style': 'Intima e autentica',
        'target_customer': 'Coppie e famiglie premium',
        'ideal_partner': 'Wedding planner e concierge',
    })
    assert created.status_code == 201
    assert created.get_json()['slug'] == 'villa-aurora'
    assert created.get_json()['max_guests'] == 6

    listed = client.get('/api/clients', headers={'X-User-ID': 'owner'}).get_json()
    assert [workspace['name'] for workspace in listed] == ['Villa Aurora']


def test_leads_are_isolated_by_client_workspace(client):
    headers_a = {'X-User-ID': 'owner', 'X-Client-ID': 'cliente-a'}
    headers_b = {'X-User-ID': 'owner', 'X-Client-ID': 'cliente-b'}
    payload = {'name': 'Partner condiviso', 'website': 'https://partner.example'}

    assert client.post('/api/leads/save', headers=headers_a, json=payload).status_code == 200
    assert client.post('/api/leads/save', headers=headers_b, json=payload).status_code == 200

    leads_a = client.get('/api/leads', headers=headers_a).get_json()
    leads_b = client.get('/api/leads', headers=headers_b).get_json()
    default_leads = client.get('/api/leads', headers={'X-User-ID': 'owner'}).get_json()
    assert len(leads_a) == 1
    assert len(leads_b) == 1
    assert default_leads == []


def test_client_profile_drives_generated_queries(client):
    client.post('/api/clients', headers={'X-User-ID': 'owner'}, json={
        'name': 'Gabri',
        'geography': 'Portofino',
        'ideal_partner': 'boutique hotel, wedding planner',
        'boat_type': 'Barca in legno',
        'max_guests': 6,
    })
    queries = client.get(
        '/api/queries/generate',
        headers={'X-User-ID': 'owner', 'X-Client-ID': 'gabri'},
    ).get_json()
    assert any(item['category'] == '🎯 Gabri' for item in queries)
    assert any('Portofino' in item['query'] for item in queries if item['category'] == '🎯 Gabri')


def test_invalid_business_type_is_rejected(client):
    response = client.post('/api/clients', headers={'X-User-ID': 'owner'}, json={
        'name': 'Invalido', 'business_type': 'spaceship',
    })
    assert response.status_code == 400


def test_real_app_blocks_search_without_client(client, app_with_db):
    app_with_db.app.config['TESTING'] = False
    response = client.get('/api/search?q=hotel', headers={'X-User-ID': 'owner'})
    assert response.status_code == 400
    assert response.get_json()['error'] == 'client_required'


def test_hospitality_queries_are_prioritized_and_not_nautical(client):
    client.post('/api/clients', headers={'X-User-ID': 'owner'}, json={
        'name': 'Cosimo', 'business_type': 'hospitality', 'inventory_count': 10,
        'geography': 'Promontorio di Portofino',
        'ideal_partner': 'DMC, wedding planner, corporate retreat',
    })
    response = client.get('/api/queries/generate', headers={
        'X-User-ID': 'owner', 'X-Client-ID': 'cosimo',
    })
    queries = response.get_json()
    assert queries[0]['category'] == '🎯 Cosimo'
    assert not any(item['category'].startswith('⛵') for item in queries)
    assert not any(item['category'].startswith('🏨') for item in queries)


def test_network_includes_cross_sell_and_capacity_handoffs(client):
    profiles = [
        {'name': 'Gabri', 'business_type': 'nautical', 'max_guests': 6},
        {'name': 'Massi', 'business_type': 'nautical', 'max_guests': 40},
        {'name': 'Ale', 'business_type': 'hospitality', 'inventory_count': 2},
        {'name': 'Cosimo', 'business_type': 'hospitality', 'inventory_count': 10},
    ]
    for profile in profiles:
        assert client.post('/api/clients', headers={'X-User-ID': 'owner'}, json=profile).status_code == 201
    opportunities = client.get('/api/opportunities/network', headers={'X-User-ID': 'owner'}).get_json()
    titles = {item['title'] for item in opportunities}
    assert 'Ale + Cosimo' in titles
    assert 'Gabri + Massi' in titles
    assert any({'Ale', 'Gabri'} == set(item['title'].split(' + ')) for item in opportunities)


def test_profile_update_invalidates_cached_email(client, app_with_db):
    client.post('/api/clients', headers={'X-User-ID': 'owner'}, json={
        'name': 'Ale', 'business_type': 'hospitality',
    })
    import sqlite3
    conn = sqlite3.connect(app_with_db.DB_PATH)
    conn.execute("INSERT INTO leads (user_id, company_name, website, draft_email) VALUES ('owner::ale','Partner','https://p.example','vecchia')")
    conn.commit()
    conn.close()
    response = client.patch('/api/clients/ale', headers={'X-User-ID': 'owner'}, json={'primary_goal': 'Più prenotazioni'})
    assert response.status_code == 200
    conn = sqlite3.connect(app_with_db.DB_PATH)
    assert conn.execute("SELECT draft_email FROM leads WHERE user_id='owner::ale'").fetchone()[0] == ''
    conn.close()
