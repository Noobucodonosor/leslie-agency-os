"""Test suite per Leslie 2.0:
- Smistamento automatico barche (1-6 pax -> Gabri, 7-40 pax -> Massi) e rilevamento conflitti
- Parsing richieste naturali WhatsApp/Email (IT/EN/FR)
- Preventivatore Barca + Ristorante sul Mare + Casa Vacanza (Ale/Cosimo)
- Pagina pubblica offerta (/offer/<token>) e conferma 1-click
- Portali Partner & Guest Pass QR (/p/<slug>) con attribuzione commissioni
"""


def test_commerce_overview_seeds_catalog_venues_and_portals(as_owner):
    resp = as_owner.get("/api/commerce/overview")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["offers"]) >= 6
    assert any(o["operator_slug"] == "gabri" and o["max_guests"] == 6 for o in data["offers"])
    assert any(o["operator_slug"] == "massi" and o["max_guests"] == 40 for o in data["offers"])
    assert any(v["venue_type"] == "restaurant" for v in data["venues"])
    assert any(v["venue_type"] == "holiday_home" for v in data["venues"])
    assert any(p["slug"] == "ospiti-ale" for p in data["portals"])
    assert any(p["slug"] == "ospiti-cosimo" for p in data["portals"])


def test_smart_dispatcher_routes_by_capacity_and_parses_natural_text(as_owner):
    # 1. Testo in inglese per 4 persone al tramonto con cena
    r_small = as_owner.post("/api/commerce/dispatch", json={
        "raw_text": "Hi! We are 4 people on 2026-08-14, we would like a sunset boat tour and restaurant dinner in Portofino"
    })
    assert r_small.status_code == 200
    body_small = r_small.get_json()
    assert body_small["parsed"]["guests_count"] == 4
    assert body_small["parsed"]["customer_language"] == "en"
    assert body_small["parsed"]["time_slot"] == "sunset"
    assert body_small["dispatch"]["assigned_operator"] == "gabri"
    assert body_small["dispatch"]["recommended_restaurant"] is not None

    # 2. Richiesta per gruppo da 18 persone con soggiorno -> Massi + Case di Cosimo
    r_large = as_owner.post("/api/commerce/dispatch", json={
        "raw_text": "Ciao, siamo un gruppo di 18 persone per un matrimonio, cerchiamo barca e appartamenti per 3 notti"
    })
    assert r_large.status_code == 200
    body_large = r_large.get_json()
    assert body_large["parsed"]["guests_count"] == 18
    assert body_large["dispatch"]["assigned_operator"] == "massi"
    assert body_large["dispatch"]["recommended_stay"]["slug"] == "case-vacanze-cosimo"


def test_quote_creation_public_offer_confirm_and_restaurant_whatsapp(as_owner, client):
    overview = as_owner.get("/api/commerce/overview").get_json()
    restaurant = next(v for v in overview["venues"] if v["venue_type"] == "restaurant")
    stay = next(v for v in overview["venues"] if v["venue_type"] == "holiday_home" and "ale" in v["slug"])

    # Crea preventivo completo: Barca Gabri + Ristorante San Fruttuoso + 2 notti da Ale
    created_resp = as_owner.post("/api/commerce/quotes", json={
        "customer_name": "Famiglia Smith",
        "customer_contact": "+1555123456",
        "customer_language": "en",
        "event_date": "2026-07-25",
        "time_slot": "full_day",
        "guests_count": 4,
        "restaurant_venue_id": restaurant["id"],
        "stay_venue_id": stay["id"],
        "stay_nights": 2,
        "source_portal_slug": "ospiti-ale",
    })
    assert created_resp.status_code == 201
    quote = created_resp.get_json()
    token = quote["public_token"]
    assert quote["assigned_operator"] == "gabri"
    assert quote["restaurant_covers"] == 4
    assert quote["restaurant_fee_eur"] > 0
    assert quote["stay_nights"] == 2
    assert quote["stay_fee_eur"] > 0
    assert quote["partner_commission_eur"] > 0
    assert f"/offer/{token}" in quote["custom_pitch"]

    # Verifica pagina pubblica dell'offerta
    pub_page = client.get(f"/offer/{token}")
    assert pub_page.status_code == 200
    html = pub_page.get_data(as_text=True)
    assert "Famiglia Smith" in html
    assert restaurant["name"] in html

    # Conferma 1-click dalla pagina pubblica
    conf = client.post(f"/api/public/offer/{token}/confirm")
    assert conf.status_code == 200
    assert conf.get_json()["status"] == "confirmed"

    # Verifica che il dispatcher rilevi ora il conflitto per Gabri sulla stessa data/fascia
    conflict_check = as_owner.post("/api/commerce/dispatch", json={
        "guests_count": 4,
        "event_date": "2026-07-25",
        "time_slot": "full_day",
    }).get_json()
    assert conflict_check["dispatch"]["conflict"] is not None
    assert "Famiglia Smith" in conflict_check["dispatch"]["alternative_note"]

    # Verifica link WhatsApp per prenotare il tavolo al ristorante
    wa_resp = as_owner.get(f"/api/commerce/quotes/{quote['id']}/restaurant-whatsapp")
    assert wa_resp.status_code == 200
    wa_data = wa_resp.get_json()
    assert "wa.me" in wa_data["whatsapp_url"]
    assert "4 persone" in wa_data["message"]


def test_partner_portal_public_request_routes_and_tracks_commission(as_owner, client):
    # Visita il portale pubblico degli ospiti di Cosimo
    page = client.get("/p/ospiti-cosimo")
    assert page.status_code == 200
    assert "Cosimo" in page.get_data(as_text=True)

    # Invia richiesta per 12 ospiti dal portale -> deve assegnare Massi e tracciare la commissione per ospiti-cosimo
    req = client.post("/api/public/p/ospiti-cosimo/request", json={
        "customer_name": "Gruppo Zurigo",
        "customer_contact": "+41791112233",
        "guests_count": 12,
        "event_date": "2026-09-10",
        "time_slot": "full_day",
        "wants_restaurant": True,
    })
    assert req.status_code == 201
    res = req.get_json()
    assert res["ok"] is True
    assert res["assigned_operator"] == "massi"
    assert res["offer_url"].startswith("/offer/LSL-")

    # Controlla nel cruscotto che il contatore richieste del portale sia salito
    overview = as_owner.get("/api/commerce/overview").get_json()
    cosimo_portal = next(p for p in overview["portals"] if p["slug"] == "ospiti-cosimo")
    assert cosimo_portal["views_count"] >= 1
    assert cosimo_portal["requests_count"] >= 1
