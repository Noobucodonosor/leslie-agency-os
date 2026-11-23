"""
Leslie 2.0 — Commerce, Boat Dispatch, Restaurant/Stay Referral & Partner Portal Engine.

Trasforma Leslie da semplice scraper B2B a motore operativo per:
1. Vendere giri in barca (smistamento automatico 1-6 pax -> Gabri, 7-40 pax -> Massi).
2. Abbinare e tracciare prenotazioni verso Ristoranti sul mare e Case Vacanza (Ale, Cosimo e partner).
3. Generare preventivi interattivi in 30 secondi (/offer/<token>) in IT/EN/FR.
4. Fornire Portali Partner & Guest Pass con QR (/p/<slug>) per Hotel, Host Airbnb e Case Vacanza.
"""
import json
import re
import secrets
import sqlite3
from urllib.parse import quote_plus
from flask import Blueprint, jsonify, render_template, request

import config

commerce_bp = Blueprint("commerce", __name__)


def _db_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_commerce_tables(conn):
    """Crea le tabelle di Leslie 2.0 in modo idempotente e popola il catalogo iniziale."""
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS catalog_offers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id TEXT NOT NULL DEFAULT 'owner',
        slug TEXT NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'boat_tour',
        operator_slug TEXT NOT NULL DEFAULT 'gabri',
        operator_name TEXT NOT NULL DEFAULT 'Gabri',
        vessel_or_property TEXT DEFAULT '',
        min_guests INTEGER DEFAULT 1,
        max_guests INTEGER NOT NULL DEFAULT 6,
        duration_hours REAL DEFAULT 4.0,
        base_price_eur INTEGER NOT NULL DEFAULT 450,
        price_unit TEXT DEFAULT 'flat',
        route_summary TEXT DEFAULT '',
        includes_text TEXT DEFAULT '',
        partner_commission_pct INTEGER DEFAULT 12,
        active BOOLEAN DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(owner_id, slug)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS partner_venues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id TEXT NOT NULL DEFAULT 'owner',
        slug TEXT NOT NULL,
        name TEXT NOT NULL,
        venue_type TEXT NOT NULL DEFAULT 'restaurant',
        location TEXT NOT NULL DEFAULT 'Portofino',
        boat_access BOOLEAN DEFAULT 1,
        max_party_size INTEGER DEFAULT 20,
        contact_name TEXT DEFAULT '',
        whatsapp_phone TEXT DEFAULT '',
        email TEXT DEFAULT '',
        commission_model TEXT DEFAULT 'per_cover',
        commission_value REAL DEFAULT 10.0,
        avg_spend_eur INTEGER DEFAULT 85,
        specialty_note TEXT DEFAULT '',
        active BOOLEAN DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(owner_id, slug)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS partner_portals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id TEXT NOT NULL DEFAULT 'owner',
        slug TEXT NOT NULL,
        partner_name TEXT NOT NULL,
        partner_type TEXT DEFAULT 'accommodation',
        lead_id INTEGER,
        welcome_headline TEXT DEFAULT '',
        commission_pct INTEGER DEFAULT 12,
        contact_whatsapp TEXT DEFAULT '',
        contact_email TEXT DEFAULT '',
        views_count INTEGER DEFAULT 0,
        requests_count INTEGER DEFAULT 0,
        active BOOLEAN DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(owner_id, slug)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS bookings_and_quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id TEXT NOT NULL DEFAULT 'owner',
        public_token TEXT NOT NULL UNIQUE,
        customer_name TEXT NOT NULL,
        customer_contact TEXT DEFAULT '',
        customer_language TEXT DEFAULT 'it',
        event_date TEXT DEFAULT '',
        time_slot TEXT DEFAULT 'full_day',
        guests_count INTEGER NOT NULL DEFAULT 2,
        assigned_operator TEXT NOT NULL DEFAULT 'gabri',
        offer_id INTEGER,
        offer_title TEXT NOT NULL,
        boat_price_eur INTEGER NOT NULL DEFAULT 0,
        restaurant_venue_id INTEGER,
        restaurant_name TEXT DEFAULT '',
        restaurant_covers INTEGER DEFAULT 0,
        restaurant_time TEXT DEFAULT '13:30',
        restaurant_status TEXT DEFAULT 'none',
        restaurant_fee_eur REAL DEFAULT 0.0,
        stay_venue_id INTEGER,
        stay_name TEXT DEFAULT '',
        stay_nights INTEGER DEFAULT 0,
        stay_price_eur INTEGER DEFAULT 0,
        stay_fee_eur REAL DEFAULT 0.0,
        source_portal_slug TEXT DEFAULT '',
        source_lead_id INTEGER,
        partner_commission_eur REAL DEFAULT 0.0,
        total_price_eur INTEGER NOT NULL DEFAULT 0,
        net_margin_eur REAL NOT NULL DEFAULT 0.0,
        status TEXT NOT NULL DEFAULT 'quote_sent',
        custom_pitch TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        confirmed_at TIMESTAMP
    )''')

    c.execute("CREATE INDEX IF NOT EXISTS idx_bookings_owner_date ON bookings_and_quotes(owner_id, event_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bookings_owner_status ON bookings_and_quotes(owner_id, status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_venues_owner_type ON partner_venues(owner_id, venue_type)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_portals_slug ON partner_portals(slug)")

    _seed_defaults_if_empty(c, owner_id="owner")
    conn.commit()


def _seed_defaults_if_empty(cursor, owner_id="owner"):
    """Popola il catalogo reale iniziale (Gabri, Massi, Ale, Cosimo + Ristoranti) se vuoto."""
    offers_count = cursor.execute(
        "SELECT COUNT(*) FROM catalog_offers WHERE owner_id=?", (owner_id,)
    ).fetchone()[0]
    if offers_count == 0:
        default_offers = [
            (
                owner_id,
                "gabri-sunset-portofino",
                "Sunset Tour Privato & Aperitivo a Portofino",
                "boat_tour",
                "gabri",
                "Gabri",
                "Barca classica in legno (max 6 ospiti)",
                1,
                6,
                2.5,
                380,
                "flat",
                "Santa Margherita · Paraggi · Portofino · Punta Chiappa",
                "Skipper dedicato, aperitivo ligure con focaccia e prosecco, teli mare, carburante incluso",
                12,
            ),
            (
                owner_id,
                "gabri-day-boat-lunch",
                "Giornata Privata in Barca in Legno + Tavolo sul Mare",
                "boat_dining",
                "gabri",
                "Gabri",
                "Barca classica in legno (max 6 ospiti)",
                1,
                6,
                6.0,
                690,
                "flat",
                "Portofino · Abbazia di San Fruttuoso · Punta Chiappa · Camogli",
                "Tour privato intera giornata, sosta bagno nelle cale, prenotazione prioritaria ristorante sul mare con sbarco diretto",
                12,
            ),
            (
                owner_id,
                "gabri-half-day-hidden-coves",
                "Mezza Giornata Tra le Cale Segrete del Promontorio",
                "boat_tour",
                "gabri",
                "Gabri",
                "Barca classica in legno (max 6 ospiti)",
                1,
                6,
                3.5,
                460,
                "flat",
                "Paraggi · Portofino · Cristo degli Abissi (San Fruttuoso)",
                "Skipper locale, attrezzatura snorkeling, bevande fresche a bordo, carburante incluso",
                12,
            ),
            (
                owner_id,
                "massi-group-charter-tigullio",
                "Escursione Privata in Mare per Gruppi & Eventi",
                "group_charter",
                "massi",
                "Massi",
                "Navetta panoramica (fino a 40 ospiti)",
                7,
                40,
                4.0,
                1250,
                "flat",
                "Santa Margherita · Portofino · San Fruttuoso · Camogli",
                "Navetta riservata in esclusiva con equipaggio, impianto audio, soste panoramiche e sbarco al ristorante per gruppi",
                10,
            ),
            (
                owner_id,
                "massi-wedding-corporate-shuttle",
                "Charter Matrimoni, Incentive & Transfer Invitati via Mare",
                "group_charter",
                "massi",
                "Massi",
                "Navetta panoramica (fino a 40 ospiti)",
                10,
                40,
                5.0,
                1650,
                "flat",
                "Tigullio · Portofino · San Fruttuoso · Venue sul mare",
                "Coordinamento logistico con wedding/event planner, welcome drink a bordo, orari flessibili dedicati all'evento",
                10,
            ),
            (
                owner_id,
                "ale-stay-and-sail-boutique",
                "Stay & Sail: Soggiorno Casa Boutique Ale + Giro in Barca con Gabri",
                "stay_sail",
                "ale",
                "Ale + Gabri",
                "Casa Vacanza Boutique + Barca in legno (max 6 ospiti)",
                2,
                6,
                48.0,
                890,
                "flat",
                "Promontorio di Portofino + Tour via mare San Fruttuoso",
                "2 notti nelle case curate di Ale + uscita privata in barca in legno con Gabri e tavolo ristorante riservato",
                10,
            ),
            (
                owner_id,
                "cosimo-group-stay-and-boat",
                "Ospitalità Diffusa Gruppi (Case Cosimo) + Charter Navetta Massi",
                "stay_sail",
                "cosimo",
                "Cosimo + Massi",
                "10 Case Vacanze coordinate + Navetta 40 ospiti",
                8,
                40,
                72.0,
                2600,
                "flat",
                "Promontorio di Portofino + Crociere private nel Golfo",
                "Alloggio distribuito per gruppi/invitati nelle proprietà di Cosimo + escursione privata in mare con Massi",
                10,
            ),
        ]
        cursor.executemany(
            '''INSERT OR IGNORE INTO catalog_offers
               (owner_id, slug, title, category, operator_slug, operator_name, vessel_or_property,
                min_guests, max_guests, duration_hours, base_price_eur, price_unit, route_summary,
                includes_text, partner_commission_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            default_offers,
        )

    venues_count = cursor.execute(
        "SELECT COUNT(*) FROM partner_venues WHERE owner_id=?", (owner_id,)
    ).fetchone()[0]
    if venues_count == 0:
        default_venues = [
            (
                owner_id,
                "baia-san-fruttuoso-mare",
                "Ristorante Baia di San Fruttuoso (Sbarco in Barca)",
                "restaurant",
                "San Fruttuoso (Camogli)",
                1,
                24,
                "Responsabile Sala",
                "+393400000001",
                "prenotazioni@sanfruttuoso.example",
                "per_cover",
                12.0,
                90,
                "Raggiungibile via mare con Gabri o Massi; tavolo vista abbazia garantito per i nostri ospiti.",
            ),
            (
                owner_id,
                "punta-chiappa-scoglio",
                "Tavola sul Mare — Punta Chiappa",
                "restaurant",
                "Punta Chiappa · Camogli",
                1,
                16,
                "Titolare",
                "+393400000002",
                "info@puntachiappa.example",
                "per_cover",
                10.0,
                80,
                "Ormeggio comodo per barca in legno; crudi di mare e pescato ligure del giorno.",
            ),
            (
                owner_id,
                "portofino-calata-bistrot",
                "Ristorante Calata di Portofino",
                "restaurant",
                "Portofino Piazzetta",
                1,
                12,
                "Maître Concierge",
                "+393400000003",
                "concierge@calataportofino.example",
                "pct_bill",
                10.0,
                130,
                "Ideale per sbarco dopo Sunset Tour o pranzo esclusivo in Piazzetta.",
            ),
            (
                owner_id,
                "santa-margherita-gruppi-mare",
                "Terrazza Ligure — Santa Margherita (Fino a 40 pax)",
                "restaurant",
                "Santa Margherita Ligure",
                1,
                45,
                "Direttore Eventi",
                "+393400000004",
                "eventi@terrazzaligure.example",
                "pct_bill",
                10.0,
                75,
                "Perfetto per i gruppi della navetta di Massi: menu degustazione pesce e veranda sul porto.",
            ),
            (
                owner_id,
                "case-vacanze-ale",
                "Case Vacanze Boutique di Ale (2 Proprietà)",
                "holiday_home",
                "Promontorio di Portofino",
                0,
                6,
                "Ale",
                "+393400000005",
                "ale@ospitalita.example",
                "pct_bill",
                10.0,
                220,
                "Due case curate nei dettagli per coppie e famiglie che cercano autenticità + giro in barca con Gabri.",
            ),
            (
                owner_id,
                "case-vacanze-cosimo",
                "Ospitalità Diffusa di Cosimo (10 Case Vacanze)",
                "holiday_home",
                "Promontorio di Portofino / Tigullio",
                0,
                40,
                "Cosimo",
                "+393400000006",
                "cosimo@ospitalita.example",
                "pct_bill",
                10.0,
                190,
                "Dieci appartamenti coordinati per gruppi, invitati di matrimoni, retreat aziendali e famiglie.",
            ),
        ]
        cursor.executemany(
            '''INSERT OR IGNORE INTO partner_venues
               (owner_id, slug, name, venue_type, location, boat_access, max_party_size,
                contact_name, whatsapp_phone, email, commission_model, commission_value,
                avg_spend_eur, specialty_note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            default_venues,
        )

    portals_count = cursor.execute(
        "SELECT COUNT(*) FROM partner_portals WHERE owner_id=?", (owner_id,)
    ).fetchone()[0]
    if portals_count == 0:
        default_portals = [
            (
                owner_id,
                "ospiti-ale",
                "Ospiti Case Vacanze di Ale",
                "accommodation",
                None,
                "Benvenuto da Ale! Prenota la tua uscita privata in barca a Portofino con tavolo riservato sul mare.",
                10,
                "+393400000005",
                "ale@ospitalita.example",
            ),
            (
                owner_id,
                "ospiti-cosimo",
                "Ospiti Case Vacanze di Cosimo",
                "accommodation",
                None,
                "Benvenuto nelle case di Cosimo! Scegli il tuo tour in barca (privato fino a 6 ospiti o navetta per gruppi).",
                10,
                "+393400000006",
                "cosimo@ospitalita.example",
            ),
            (
                owner_id,
                "concierge-partner-vip",
                "Concierge & Hotel Partner Desk — Portofino",
                "hotel_concierge",
                None,
                "Canale prioritario per Concierge, Hotel e Wedding Planner: disponibilità immediata barche + ristoranti.",
                12,
                "",
                "",
            ),
        ]
        cursor.executemany(
            '''INSERT OR IGNORE INTO partner_portals
               (owner_id, slug, partner_name, partner_type, lead_id, welcome_headline,
                commission_pct, contact_whatsapp, contact_email)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            default_portals,
        )


def _slugify(value):
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return s[:48] or f"item-{secrets.token_hex(3)}"


def _calculate_venue_fee(venue, guests_or_nights):
    """Calcola la fee/commissione generata mandando clienti a un ristorante o casa vacanza."""
    if not venue:
        return 0.0
    model = venue.get("commission_model") or "per_cover"
    val = float(venue.get("commission_value") or 0.0)
    avg_spend = float(venue.get("avg_spend_eur") or 85.0)
    qty = max(1, int(guests_or_nights or 1))
    if model == "per_cover":
        return round(val * qty, 2)
    if model == "pct_bill":
        return round((avg_spend * qty) * (val / 100.0), 2)
    if model == "per_night":
        return round(val * qty, 2)
    return 0.0


def parse_natural_booking_request(raw_text):
    """Analizza una richiesta incollata da WhatsApp/Email ed estrae ospiti, lingua, slot, ristorante e alloggio."""
    text = (raw_text or "").strip()
    if not text:
        return {}
    lower = text.lower()

    # Rileva lingua
    en_markers = ["hello", "hi ", "would like", "people", "guests", "boat", "tomorrow", "lunch", "sunset", "available", "how much"]
    fr_markers = ["bonjour", "salut", "personnes", "bateau", "demain", "déjeuner", "combien"]
    lang = "it"
    if any(w in lower for w in en_markers):
        lang = "en"
    elif any(w in lower for w in fr_markers):
        lang = "fr"

    # Rileva numero di persone
    guests = 2
    m_guests = re.search(r"\b(\d{1,2})\s*(?:persone|pax|ospiti|people|guests|adults|personnes|invitati|amici)\b", lower)
    if m_guests:
        guests = max(1, min(60, int(m_guests.group(1))))
    elif any(w in lower for w in ["coppia", "couple", "honeymoon", "sposi", "noi due", "2 of us"]):
        guests = 2

    # Rileva fascia oraria
    time_slot = "full_day"
    if any(w in lower for w in ["tramonto", "sunset", "aperitivo", "sera", "evening"]):
        time_slot = "sunset"
    elif any(w in lower for w in ["mattina", "morning", "mezza giornata", "half day"]):
        time_slot = "morning"
    elif any(w in lower for w in ["pomeriggio", "afternoon"]):
        time_slot = "afternoon"

    # Rileva desiderio ristorante
    wants_restaurant = any(
        w in lower
        for w in [
            "ristorante", "pranzo", "cena", "lunch", "dinner", "tavolo", "mangiare",
            "san fruttuoso", "punta chiappa", "food", "restaurant",
        ]
    )

    # Rileva desiderio casa vacanza / alloggio
    wants_stay = any(
        w in lower
        for w in [
            "dormire", "casa", "appartamento", "alloggio", "notte", "notti", "stay",
            "accommodation", "villa", "apartment", "weekend",
        ]
    )
    stay_nights = 2 if wants_stay else 0
    m_nights = re.search(r"\b(\d{1,2})\s*(?:notti|notte|nights|night)\b", lower)
    if m_nights:
        stay_nights = max(1, min(30, int(m_nights.group(1))))
        wants_stay = True

    # Data YYYY-MM-DD se presente
    event_date = ""
    m_date = re.search(r"\b(202\d-\d{2}-\d{2})\b", text)
    if m_date:
        event_date = m_date.group(1)

    return {
        "guests_count": guests,
        "customer_language": lang,
        "time_slot": time_slot,
        "wants_restaurant": wants_restaurant,
        "wants_stay": wants_stay,
        "stay_nights": stay_nights,
        "event_date": event_date,
        "assigned_operator": "gabri" if guests <= 6 else "massi",
    }


def build_multilingual_pitch(
    lang,
    customer_name,
    offer_title,
    operator_name,
    vessel,
    guests_count,
    event_date,
    time_slot_label,
    boat_price_eur,
    restaurant_name="",
    stay_name="",
    stay_nights=0,
    stay_price_eur=0,
    public_url="",
):
    """Genera il messaggio commerciale pronto per WhatsApp/Email nella lingua del cliente."""
    total_eur = int(boat_price_eur or 0) + int(stay_price_eur or 0)
    date_part_it = f" per il {event_date}" if event_date else ""
    date_part_en = f" on {event_date}" if event_date else ""
    date_part_fr = f" le {event_date}" if event_date else ""

    if lang == "en":
        lines = [
            f"Hi {customer_name or 'there'}! ⚓",
            f"We have prepared your tailored experience in Portofino{date_part_en} for {guests_count} guests ({time_slot_label}):",
            f"🛥️ *{offer_title}* with {operator_name} ({vessel}) — €{boat_price_eur}",
        ]
        if restaurant_name:
            lines.append(f"🍽️ *Priority Sea-View Table included*: {restaurant_name} (direct boat landing)")
        if stay_name and stay_nights > 0:
            lines.append(f"🏡 *Accommodation ({stay_nights} nights)*: {stay_name} — €{stay_price_eur}")
        lines.append(f"✨ *Total*: €{total_eur}")
        if public_url:
            lines.append(f"\n👉 View itinerary & confirm in 1 click: {public_url}")
        return "\n".join(lines)

    if lang == "fr":
        lines = [
            f"Bonjour {customer_name or ''}! ⚓",
            f"Voici notre proposition sur mesure à Portofino{date_part_fr} pour {guests_count} personnes ({time_slot_label}) :",
            f"🛥️ *{offer_title}* avec {operator_name} ({vessel}) — {boat_price_eur} €",
        ]
        if restaurant_name:
            lines.append(f"🍽️ *Table prioritaire en bord de mer* : {restaurant_name} (arrivée directe en bateau)")
        if stay_name and stay_nights > 0:
            lines.append(f"🏡 *Séjour ({stay_nights} nuits)* : {stay_name} — {stay_price_eur} €")
        lines.append(f"✨ *Total* : {total_eur} €")
        if public_url:
            lines.append(f"\n👉 Voir les détails et confirmer en 1 clic : {public_url}")
        return "\n".join(lines)

    # Default: Italiano
    lines = [
        f"Ciao {customer_name or ''}! ⚓".replace(" !", "!"),
        f"Ecco la proposta su misura nel Golfo di Portofino{date_part_it} per {guests_count} ospiti ({time_slot_label}):",
        f"🛥️ *{offer_title}* con {operator_name} ({vessel}) — €{boat_price_eur}",
    ]
    if restaurant_name:
        lines.append(f"🍽️ *Tavolo riservato sul mare*: {restaurant_name} (arrivo diretto in barca)")
    if stay_name and stay_nights > 0:
        lines.append(f"🏡 *Soggiorno abbinato ({stay_nights} notti)*: {stay_name} — €{stay_price_eur}")
    lines.append(f"✨ *Totale complessivo*: €{total_eur}")
    if public_url:
        lines.append(f"\n👉 Apri i dettagli dell'esperienza e conferma in 1 click: {public_url}")
    return "\n".join(lines)


TIME_SLOT_LABELS = {
    "morning": "Mattina (09:30–13:00)",
    "afternoon": "Pomeriggio (14:00–17:30)",
    "sunset": "Tramonto & Aperitivo (18:00–20:30)",
    "full_day": "Giornata Intera con Pranzo (10:00–17:00)",
    "multi_day": "Pacchetto Soggiorno + Barca",
}


def dispatch_and_recommend(owner_id, guests_count, time_slot="full_day", event_date="", wants_restaurant=True, wants_stay=False, stay_nights=0):
    """
    Motore di smistamento intelligente:
    - 1..6 ospiti -> Gabri (barca in legno)
    - 7..40 ospiti -> Massi (navetta gruppi)
    - Verifica conflitti di calendario in `bookings_and_quotes`
    - Suggerisce il miglior Ristorante con approdo barca e la Casa Vacanza (Ale per <=6 pax, Cosimo per gruppi).
    """
    guests = max(1, int(guests_count or 2))
    primary_operator = "gabri" if guests <= 6 else "massi"

    conn = _db_conn()
    offers = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM catalog_offers WHERE owner_id=? AND active=1 ORDER BY base_price_eur ASC",
            (owner_id,),
        ).fetchall()
    ]
    venues = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM partner_venues WHERE owner_id=? AND active=1 ORDER BY id ASC",
            (owner_id,),
        ).fetchall()
    ]

    # Controlla se l'operatore primario ha già un giro confermato nella stessa data/fascia
    conflict = None
    if event_date:
        existing = conn.execute(
            """SELECT id, customer_name, time_slot, offer_title FROM bookings_and_quotes
               WHERE owner_id=? AND event_date=? AND assigned_operator=? AND status IN ('confirmed', 'completed')""",
            (owner_id, event_date, primary_operator),
        ).fetchall()
        for row in existing:
            if row["time_slot"] == time_slot or row["time_slot"] == "full_day" or time_slot == "full_day":
                conflict = dict(row)
                break
    conn.close()

    # Scegli l'offerta migliore per capienza e fascia oraria
    matching_offers = [
        o for o in offers if o["operator_slug"] == primary_operator and o["min_guests"] <= guests <= o["max_guests"]
    ]
    if not matching_offers:
        matching_offers = [o for o in offers if o["max_guests"] >= guests] or offers

    selected_offer = matching_offers[0] if matching_offers else None
    for o in matching_offers:
        if time_slot == "sunset" and "sunset" in o["slug"]:
            selected_offer = o
            break
        if time_slot == "full_day" and ("lunch" in o["slug"] or "day" in o["slug"] or "group" in o["slug"]):
            selected_offer = o
            break
        if time_slot == "morning" and "half-day" in o["slug"]:
            selected_offer = o
            break

    # Scegli il miglior ristorante compatibile con la dimensione del gruppo
    restaurants = [
        v for v in venues if v["venue_type"] == "restaurant" and (v.get("max_party_size") or 20) >= guests
    ]
    recommended_restaurant = restaurants[0] if (wants_restaurant and restaurants) else None

    # Scegli la migliore casa vacanza (Ale per <=6 ospiti, Cosimo per gruppi o overflow)
    stays = [v for v in venues if v["venue_type"] == "holiday_home"]
    recommended_stay = None
    if wants_stay or stay_nights > 0:
        target_stay_slug = "case-vacanze-ale" if guests <= 6 else "case-vacanze-cosimo"
        recommended_stay = next((s for s in stays if s["slug"] == target_stay_slug), stays[0] if stays else None)

    alternative_note = ""
    if conflict:
        alt_op = "Massi (Navetta in esclusiva)" if primary_operator == "gabri" else "secondo turno / barca partner"
        alternative_note = (
            f"Attenzione: {primary_operator.capitalize()} ha già '{conflict['offer_title']}' ({conflict['customer_name']}) "
            f"il {event_date}. Puoi proporre la fascia Sunset (18:00) oppure smistare in overflow su {alt_op}."
        )

    return {
        "assigned_operator": primary_operator,
        "operator_label": "Gabri · Barca in legno (max 6 pax)" if primary_operator == "gabri" else "Massi · Navetta Gruppi (fino a 40 pax)",
        "recommended_offer": selected_offer,
        "recommended_restaurant": recommended_restaurant,
        "recommended_stay": recommended_stay,
        "conflict": conflict,
        "alternative_note": alternative_note,
    }


def build_restaurant_whatsapp_link(booking):
    """Costruisce il messaggio e il link WhatsApp per prenotare il tavolo al ristorante partner."""
    phone = re.sub(r"[^\d]", "", booking.get("restaurant_whatsapp") or "")
    op_name = "Gabri (Barca in legno)" if booking.get("assigned_operator") == "gabri" else "Massi (Navetta)"
    covers = booking.get("restaurant_covers") or booking.get("guests_count") or 2
    date_str = booking.get("event_date") or "prossima uscita"
    time_str = booking.get("restaurant_time") or "13:30"
    cust = booking.get("customer_name") or "Ospiti VIP"
    msg = (
        f"Ciao! ⚓ Dalla barca di {op_name} vorremmo prenotare un tavolo per *{covers} persone* "
        f"in data *{date_str}* alle ore *{time_str}* (arrivo via mare) a nome *{cust}*. "
        f"Riuscite a riservarci un bel tavolo? Grazie!"
    )
    url = f"https://wa.me/{phone}?text={quote_plus(msg)}" if phone else f"https://wa.me/?text={quote_plus(msg)}"
    return {"message": msg, "whatsapp_url": url}


# ─── API ROUTES: OVERVIEW, CATALOG, VENUES, QUOTES & PORTALS ──────────────────

def _get_owner():
    from app import get_owner_id
    return get_owner_id()


def _mark_dirty():
    from app import mark_dirty
    mark_dirty()


@commerce_bp.route("/api/commerce/overview")
def api_commerce_overview():
    """Cruscotto completo di Leslie 2.0: vendite barche, coperti mandati ai ristoranti, notti case e portali."""
    owner = _get_owner()
    conn = _db_conn()
    offers = [dict(r) for r in conn.execute("SELECT * FROM catalog_offers WHERE owner_id=? AND active=1 ORDER BY id", (owner,)).fetchall()]
    venues = [dict(r) for r in conn.execute("SELECT * FROM partner_venues WHERE owner_id=? AND active=1 ORDER BY venue_type, id", (owner,)).fetchall()]
    portals = [dict(r) for r in conn.execute("SELECT * FROM partner_portals WHERE owner_id=? AND active=1 ORDER BY id DESC", (owner,)).fetchall()]
    bookings = [dict(r) for r in conn.execute("SELECT * FROM bookings_and_quotes WHERE owner_id=? ORDER BY id DESC LIMIT 100", (owner,)).fetchall()]

    # Arricchisci ogni venue con statistiche reali di coperti/notti e commissioni generate
    venue_stats = {}
    for b in bookings:
        if b["status"] == "cancelled":
            continue
        vid = b.get("restaurant_venue_id")
        if vid:
            st = venue_stats.setdefault(vid, {"referrals": 0, "covers_or_nights": 0, "fee_eur": 0.0, "confirmed_count": 0})
            st["referrals"] += 1
            st["covers_or_nights"] += int(b.get("restaurant_covers") or b.get("guests_count") or 0)
            st["fee_eur"] = round(st["fee_eur"] + float(b.get("restaurant_fee_eur") or 0.0), 2)
            if b.get("restaurant_status") == "confirmed" or b.get("status") in ("confirmed", "completed"):
                st["confirmed_count"] += 1
        sid = b.get("stay_venue_id")
        if sid:
            st = venue_stats.setdefault(sid, {"referrals": 0, "covers_or_nights": 0, "fee_eur": 0.0, "confirmed_count": 0})
            st["referrals"] += 1
            st["covers_or_nights"] += int(b.get("stay_nights") or 1)
            st["fee_eur"] = round(st["fee_eur"] + float(b.get("stay_fee_eur") or 0.0), 2)
            if b.get("status") in ("confirmed", "completed"):
                st["confirmed_count"] += 1

    for v in venues:
        v["stats"] = venue_stats.get(v["id"], {"referrals": 0, "covers_or_nights": 0, "fee_eur": 0.0, "confirmed_count": 0})

    # Calcola KPI aggregati
    active_bookings = [b for b in bookings if b["status"] != "cancelled"]
    confirmed_bookings = [b for b in bookings if b["status"] in ("confirmed", "completed")]
    boat_revenue_confirmed = sum(int(b.get("boat_price_eur") or 0) for b in confirmed_bookings)
    boat_revenue_pipeline = sum(int(b.get("boat_price_eur") or 0) for b in active_bookings)
    total_covers_sent = sum(int(b.get("restaurant_covers") or 0) for b in active_bookings if b.get("restaurant_venue_id"))
    total_stay_nights = sum(int(b.get("stay_nights") or 0) for b in active_bookings if b.get("stay_venue_id"))
    referral_fees_eur = round(
        sum(float(b.get("restaurant_fee_eur") or 0.0) + float(b.get("stay_fee_eur") or 0.0) for b in active_bookings),
        2,
    )

    conn.close()
    return jsonify({
        "kpis": {
            "total_quotes": len(active_bookings),
            "confirmed_boats": len(confirmed_bookings),
            "boat_revenue_confirmed_eur": boat_revenue_confirmed,
            "boat_revenue_pipeline_eur": boat_revenue_pipeline,
            "restaurant_covers_sent": total_covers_sent,
            "stay_nights_sent": total_stay_nights,
            "referral_fees_eur": referral_fees_eur,
        },
        "offers": offers,
        "venues": venues,
        "portals": portals,
        "bookings": bookings,
    })


@commerce_bp.route("/api/commerce/dispatch", methods=["POST"])
def api_commerce_dispatch():
    """Analizza una richiesta (anche testo WhatsApp grezzo) e restituisce la combinazione ideale Barca + Ristorante + Casa."""
    owner = _get_owner()
    payload = request.get_json(silent=True) or {}
    raw_text = payload.get("raw_text", "")
    parsed = parse_natural_booking_request(raw_text) if raw_text else {}

    guests = int(payload.get("guests_count") or parsed.get("guests_count") or 2)
    time_slot = payload.get("time_slot") or parsed.get("time_slot") or "full_day"
    event_date = payload.get("event_date") or parsed.get("event_date") or ""
    wants_restaurant = payload.get("wants_restaurant") if "wants_restaurant" in payload else parsed.get("wants_restaurant", True)
    wants_stay = payload.get("wants_stay") if "wants_stay" in payload else parsed.get("wants_stay", False)
    stay_nights = int(payload.get("stay_nights") if "stay_nights" in payload else (parsed.get("stay_nights") or 0))

    rec = dispatch_and_recommend(
        owner_id=owner,
        guests_count=guests,
        time_slot=time_slot,
        event_date=event_date,
        wants_restaurant=bool(wants_restaurant),
        wants_stay=bool(wants_stay),
        stay_nights=stay_nights,
    )
    return jsonify({
        "parsed": parsed,
        "dispatch": rec,
    })


@commerce_bp.route("/api/commerce/quotes", methods=["GET", "POST"])
def api_commerce_quotes():
    """Lista o crea un preventivo/prenotazione Barca + Ristorante + Casa Vacanza con link pubblico."""
    owner = _get_owner()
    conn = _db_conn()
    if request.method == "GET":
        rows = conn.execute(
            "SELECT * FROM bookings_and_quotes WHERE owner_id=? ORDER BY id DESC LIMIT 100",
            (owner,),
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    d = request.get_json(silent=True) or {}
    customer_name = str(d.get("customer_name") or "Ospite").strip()
    customer_contact = str(d.get("customer_contact") or "").strip()
    lang = str(d.get("customer_language") or "it").strip().lower()
    if lang not in ("it", "en", "fr"):
        lang = "it"
    guests = max(1, min(200, int(d.get("guests_count") or 2)))
    event_date = str(d.get("event_date") or "").strip()
    time_slot = str(d.get("time_slot") or "full_day").strip()

    # Risolvi offerta barca
    offer_id = d.get("offer_id")
    offer_row = None
    if offer_id:
        offer_row = conn.execute("SELECT * FROM catalog_offers WHERE id=? AND owner_id=?", (offer_id, owner)).fetchone()
    if not offer_row:
        op_slug = "gabri" if guests <= 6 else "massi"
        offer_row = conn.execute(
            "SELECT * FROM catalog_offers WHERE owner_id=? AND operator_slug=? ORDER BY id ASC LIMIT 1",
            (owner, op_slug),
        ).fetchone()
    offer = dict(offer_row) if offer_row else {
        "id": None,
        "title": "Tour Privato a Portofino",
        "operator_slug": "gabri" if guests <= 6 else "massi",
        "operator_name": "Gabri" if guests <= 6 else "Massi",
        "vessel_or_property": "Barca in legno" if guests <= 6 else "Navetta 40 pax",
        "base_price_eur": 450 if guests <= 6 else 1250,
        "partner_commission_pct": 12,
    }

    assigned_operator = str(d.get("assigned_operator") or offer["operator_slug"])
    offer_title = str(d.get("offer_title") or offer["title"]).strip()
    boat_price_eur = int(d.get("boat_price_eur") if d.get("boat_price_eur") not in (None, "") else offer["base_price_eur"])

    # Risolvi Ristorante abbinato (opzionale)
    restaurant_venue_id = d.get("restaurant_venue_id")
    restaurant_name = ""
    restaurant_covers = 0
    restaurant_fee_eur = 0.0
    restaurant_status = "none"
    restaurant_time = str(d.get("restaurant_time") or "13:30").strip()
    if restaurant_venue_id:
        v_row = conn.execute("SELECT * FROM partner_venues WHERE id=? AND owner_id=?", (restaurant_venue_id, owner)).fetchone()
        if v_row:
            venue = dict(v_row)
            restaurant_name = venue["name"]
            restaurant_covers = int(d.get("restaurant_covers") or guests)
            restaurant_fee_eur = _calculate_venue_fee(venue, restaurant_covers)
            restaurant_status = "to_book"

    # Risolvi Casa Vacanza abbinata (opzionale)
    stay_venue_id = d.get("stay_venue_id")
    stay_name = ""
    stay_nights = int(d.get("stay_nights") or 0)
    stay_price_eur = int(d.get("stay_price_eur") or 0)
    stay_fee_eur = 0.0
    if stay_venue_id and stay_nights > 0:
        s_row = conn.execute("SELECT * FROM partner_venues WHERE id=? AND owner_id=?", (stay_venue_id, owner)).fetchone()
        if s_row:
            s_venue = dict(s_row)
            stay_name = s_venue["name"]
            if not stay_price_eur:
                stay_price_eur = int((s_venue.get("avg_spend_eur") or 200) * stay_nights)
            stay_fee_eur = round(stay_price_eur * (float(s_venue.get("commission_value") or 10.0) / 100.0), 2)

    # Fonte/Partner per calcolo commissione passiva
    source_portal_slug = str(d.get("source_portal_slug") or "").strip()
    partner_comm_pct = 0
    if source_portal_slug:
        p_row = conn.execute("SELECT commission_pct FROM partner_portals WHERE slug=? AND owner_id=?", (source_portal_slug, owner)).fetchone()
        if p_row:
            partner_comm_pct = int(p_row["commission_pct"] or 10)
    partner_commission_eur = round(boat_price_eur * (partner_comm_pct / 100.0), 2) if partner_comm_pct else 0.0

    total_price_eur = boat_price_eur + stay_price_eur
    net_margin_eur = round(boat_price_eur - partner_commission_eur + restaurant_fee_eur + stay_fee_eur, 2)

    token = f"LSL-{secrets.token_hex(3).upper()}"
    public_url = f"{request.host_url.rstrip('/')}/offer/{token}"
    slot_label = TIME_SLOT_LABELS.get(time_slot, time_slot)

    custom_pitch = str(d.get("custom_pitch") or "").strip()
    if not custom_pitch:
        custom_pitch = build_multilingual_pitch(
            lang=lang,
            customer_name=customer_name,
            offer_title=offer_title,
            operator_name=offer.get("operator_name") or assigned_operator.capitalize(),
            vessel=offer.get("vessel_or_property") or "",
            guests_count=guests,
            event_date=event_date,
            time_slot_label=slot_label,
            boat_price_eur=boat_price_eur,
            restaurant_name=restaurant_name,
            stay_name=stay_name,
            stay_nights=stay_nights,
            stay_price_eur=stay_price_eur,
            public_url=public_url,
        )

    status = str(d.get("status") or "quote_sent")
    if status not in ("inquiry", "quote_sent", "confirmed", "completed", "cancelled"):
        status = "quote_sent"

    cur = conn.execute(
        '''INSERT INTO bookings_and_quotes
           (owner_id, public_token, customer_name, customer_contact, customer_language,
            event_date, time_slot, guests_count, assigned_operator, offer_id, offer_title,
            boat_price_eur, restaurant_venue_id, restaurant_name, restaurant_covers,
            restaurant_time, restaurant_status, restaurant_fee_eur, stay_venue_id,
            stay_name, stay_nights, stay_price_eur, stay_fee_eur, source_portal_slug,
            partner_commission_eur, total_price_eur, net_margin_eur, status, custom_pitch, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            owner, token, customer_name, customer_contact, lang,
            event_date, time_slot, guests, assigned_operator, offer.get("id"), offer_title,
            boat_price_eur, restaurant_venue_id, restaurant_name, restaurant_covers,
            restaurant_time, restaurant_status, restaurant_fee_eur, stay_venue_id,
            stay_name, stay_nights, stay_price_eur, stay_fee_eur, source_portal_slug,
            partner_commission_eur, total_price_eur, net_margin_eur, status, custom_pitch,
            str(d.get("notes") or "").strip(),
        ),
    )
    booking_id = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM bookings_and_quotes WHERE id=?", (booking_id,)).fetchone()
    conn.close()
    _mark_dirty()

    res = dict(row)
    res["public_url"] = public_url
    return jsonify(res), 201


@commerce_bp.route("/api/commerce/quotes/<int:bid>/status", methods=["POST"])
def api_update_quote_status(bid):
    """Aggiorna lo stato di un preventivo/prenotazione o lo stato del tavolo ristorante."""
    owner = _get_owner()
    d = request.get_json(silent=True) or {}
    conn = _db_conn()
    row = conn.execute("SELECT * FROM bookings_and_quotes WHERE id=? AND owner_id=?", (bid, owner)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Prenotazione non trovata"}), 404

    new_status = d.get("status")
    rest_status = d.get("restaurant_status")
    if new_status in ("inquiry", "quote_sent", "confirmed", "completed", "cancelled"):
        if new_status == "confirmed":
            conn.execute(
                "UPDATE bookings_and_quotes SET status=?, confirmed_at=COALESCE(confirmed_at, CURRENT_TIMESTAMP) WHERE id=? AND owner_id=?",
                (new_status, bid, owner),
            )
        else:
            conn.execute(
                "UPDATE bookings_and_quotes SET status=? WHERE id=? AND owner_id=?",
                (new_status, bid, owner),
            )
    if rest_status in ("none", "to_book", "requested", "confirmed"):
        conn.execute(
            "UPDATE bookings_and_quotes SET restaurant_status=? WHERE id=? AND owner_id=?",
            (rest_status, bid, owner),
        )
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM bookings_and_quotes WHERE id=?", (bid,)).fetchone())
    conn.close()
    _mark_dirty()
    return jsonify(updated)


@commerce_bp.route("/api/commerce/quotes/<int:bid>/restaurant-whatsapp")
def api_quote_restaurant_whatsapp(bid):
    """Genera il link WhatsApp diretto per inviare la prenotazione tavolo al ristorante abbinato."""
    owner = _get_owner()
    conn = _db_conn()
    row = conn.execute(
        """SELECT b.*, v.whatsapp_phone AS restaurant_whatsapp, v.contact_name AS restaurant_contact
           FROM bookings_and_quotes b
           LEFT JOIN partner_venues v ON b.restaurant_venue_id = v.id
           WHERE b.id=? AND b.owner_id=?""",
        (bid, owner),
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Prenotazione non trovata"}), 404
    return jsonify(build_restaurant_whatsapp_link(dict(row)))


@commerce_bp.route("/api/commerce/venues", methods=["GET", "POST"])
def api_commerce_venues():
    """Lista o aggiunge un Ristorante Partner o una Casa Vacanza a cui mandare prenotazioni."""
    owner = _get_owner()
    conn = _db_conn()
    if request.method == "GET":
        rows = conn.execute("SELECT * FROM partner_venues WHERE owner_id=? AND active=1 ORDER BY id", (owner,)).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    d = request.get_json(silent=True) or {}
    name = str(d.get("name") or "").strip()
    if not name:
        conn.close()
        return jsonify({"error": "Il nome della struttura/ristorante è obbligatorio"}), 400
    slug = _slugify(d.get("slug") or name)
    venue_type = str(d.get("venue_type") or "restaurant").strip()
    if venue_type not in ("restaurant", "holiday_home", "beach_club", "winery"):
        venue_type = "restaurant"

    conn.execute(
        '''INSERT INTO partner_venues
           (owner_id, slug, name, venue_type, location, boat_access, max_party_size,
            contact_name, whatsapp_phone, email, commission_model, commission_value,
            avg_spend_eur, specialty_note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(owner_id, slug) DO UPDATE SET
            name=excluded.name, location=excluded.location, whatsapp_phone=excluded.whatsapp_phone,
            commission_model=excluded.commission_model, commission_value=excluded.commission_value,
            specialty_note=excluded.specialty_note''',
        (
            owner,
            slug,
            name,
            venue_type,
            str(d.get("location") or "Portofino").strip(),
            1 if d.get("boat_access", True) else 0,
            int(d.get("max_party_size") or 20),
            str(d.get("contact_name") or "").strip(),
            str(d.get("whatsapp_phone") or "").strip(),
            str(d.get("email") or "").strip(),
            str(d.get("commission_model") or "per_cover").strip(),
            float(d.get("commission_value") or 10.0),
            int(d.get("avg_spend_eur") or 85),
            str(d.get("specialty_note") or "").strip(),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM partner_venues WHERE owner_id=? AND slug=?", (owner, slug)).fetchone()
    conn.close()
    _mark_dirty()
    return jsonify(dict(row)), 201


@commerce_bp.route("/api/commerce/portals", methods=["GET", "POST"])
def api_commerce_portals():
    """Gestisce i Portali Partner B2B2C (/p/<slug>) per Case Vacanza, Hotel, B&B e Wedding Planner."""
    owner = _get_owner()
    conn = _db_conn()
    if request.method == "GET":
        rows = conn.execute("SELECT * FROM partner_portals WHERE owner_id=? AND active=1 ORDER BY id DESC", (owner,)).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    d = request.get_json(silent=True) or {}
    partner_name = str(d.get("partner_name") or "").strip()
    if not partner_name:
        conn.close()
        return jsonify({"error": "Nome partner obbligatorio"}), 400
    slug = _slugify(d.get("slug") or partner_name)
    partner_type = str(d.get("partner_type") or "accommodation").strip()
    commission_pct = max(0, min(50, int(d.get("commission_pct") or 12)))
    headline = str(d.get("welcome_headline") or "").strip()
    if not headline:
        headline = f"Benvenuti da {partner_name}! Scopri i tour privati in barca a Portofino e i ristoranti sul mare riservati ai nostri ospiti."

    conn.execute(
        '''INSERT INTO partner_portals
           (owner_id, slug, partner_name, partner_type, lead_id, welcome_headline,
            commission_pct, contact_whatsapp, contact_email)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(owner_id, slug) DO UPDATE SET
            partner_name=excluded.partner_name, welcome_headline=excluded.welcome_headline,
            commission_pct=excluded.commission_pct, contact_whatsapp=excluded.contact_whatsapp,
            contact_email=excluded.contact_email''',
        (
            owner,
            slug,
            partner_name,
            partner_type,
            d.get("lead_id"),
            headline,
            commission_pct,
            str(d.get("contact_whatsapp") or "").strip(),
            str(d.get("contact_email") or "").strip(),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM partner_portals WHERE owner_id=? AND slug=?", (owner, slug)).fetchone()
    conn.close()
    _mark_dirty()
    res = dict(row)
    res["public_url"] = f"{request.host_url.rstrip('/')}/p/{slug}"
    return jsonify(res), 201


# ─── PUBLIC ROUTES: CLIENT OFFER (/offer/<token>) & PARTNER PORTAL (/p/<slug>) ─

@commerce_bp.route("/offer/<token>")
def public_offer_page(token):
    """Pagina pubblica mobile-first del preventivo: mostra barca, rotta, ristorante incluso e conferma 1-click."""
    conn = _db_conn()
    row = conn.execute(
        """SELECT b.*, o.vessel_or_property, o.route_summary, o.includes_text, o.duration_hours, o.operator_name
           FROM bookings_and_quotes b
           LEFT JOIN catalog_offers o ON b.offer_id = o.id
           WHERE b.public_token=?""",
        (token,),
    ).fetchone()
    conn.close()
    if not row:
        return "Offerta non trovata o scaduta.", 404
    booking = dict(row)
    booking["time_slot_label"] = TIME_SLOT_LABELS.get(booking.get("time_slot"), booking.get("time_slot") or "")
    return render_template("offer_public.html", booking=booking)


@commerce_bp.route("/api/public/offer/<token>/confirm", methods=["POST"])
def public_offer_confirm(token):
    """Permette al cliente finale o al concierge di confermare l'offerta in 1 click dalla pagina pubblica."""
    conn = _db_conn()
    row = conn.execute("SELECT * FROM bookings_and_quotes WHERE public_token=?", (token,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Offerta non trovata"}), 404
    conn.execute(
        """UPDATE bookings_and_quotes
           SET status='confirmed',
               confirmed_at=COALESCE(confirmed_at, CURRENT_TIMESTAMP),
               restaurant_status=CASE WHEN restaurant_venue_id IS NOT NULL AND restaurant_status='none' THEN 'to_book' ELSE restaurant_status END
           WHERE public_token=?""",
        (token,),
    )
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM bookings_and_quotes WHERE public_token=?", (token,)).fetchone())
    conn.close()
    _mark_dirty()
    return jsonify({"ok": True, "status": updated["status"], "confirmed_at": updated["confirmed_at"]})


@commerce_bp.route("/p/<slug>")
def public_partner_portal(slug):
    """Portale pubblico / QR Guest Pass per gli ospiti di Case Vacanza (Ale/Cosimo), Hotel e Concierge."""
    conn = _db_conn()
    portal_row = conn.execute("SELECT * FROM partner_portals WHERE slug=? AND active=1", (slug,)).fetchone()
    if not portal_row:
        conn.close()
        return "Portale Partner non trovato.", 404
    portal = dict(portal_row)
    conn.execute("UPDATE partner_portals SET views_count = views_count + 1 WHERE id=?", (portal["id"],))
    conn.commit()
    offers = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM catalog_offers WHERE owner_id=? AND active=1 ORDER BY min_guests ASC, base_price_eur ASC",
            (portal["owner_id"],),
        ).fetchall()
    ]
    restaurants = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM partner_venues WHERE owner_id=? AND venue_type='restaurant' AND active=1 ORDER BY id ASC",
            (portal["owner_id"],),
        ).fetchall()
    ]
    stays = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM partner_venues WHERE owner_id=? AND venue_type='holiday_home' AND active=1 ORDER BY id ASC",
            (portal["owner_id"],),
        ).fetchall()
    ]
    conn.close()
    return render_template(
        "partner_portal.html",
        portal=portal,
        offers=offers,
        restaurants=restaurants,
        stays=stays,
    )


@commerce_bp.route("/api/public/p/<slug>/request", methods=["POST"])
def public_partner_portal_request(slug):
    """Riceve una richiesta di prenotazione dal Portale Partner / QR Code, smista su Gabri o Massi e traccia la commissione."""
    conn = _db_conn()
    portal_row = conn.execute("SELECT * FROM partner_portals WHERE slug=? AND active=1", (slug,)).fetchone()
    if not portal_row:
        conn.close()
        return jsonify({"error": "Portale non trovato"}), 404
    portal = dict(portal_row)
    owner = portal["owner_id"]

    d = request.get_json(silent=True) or {}
    customer_name = str(d.get("customer_name") or "").strip()
    customer_contact = str(d.get("customer_contact") or "").strip()
    if not customer_name or not customer_contact:
        conn.close()
        return jsonify({"error": "Inserisci nome e recapito (WhatsApp o Email)"}), 400

    guests = max(1, min(60, int(d.get("guests_count") or 2)))
    event_date = str(d.get("event_date") or "").strip()
    time_slot = str(d.get("time_slot") or "full_day").strip()
    wants_restaurant = bool(d.get("wants_restaurant", True))
    restaurant_venue_id = d.get("restaurant_venue_id")
    stay_venue_id = d.get("stay_venue_id")
    stay_nights = int(d.get("stay_nights") or 0)

    # Smistamento automatico: <=6 pax -> Gabri, >6 pax -> Massi
    rec = dispatch_and_recommend(
        owner_id=owner,
        guests_count=guests,
        time_slot=time_slot,
        event_date=event_date,
        wants_restaurant=wants_restaurant,
        wants_stay=bool(stay_venue_id and stay_nights > 0),
        stay_nights=stay_nights,
    )
    offer = rec["recommended_offer"] or {
        "id": None,
        "title": "Esperienza in Barca a Portofino",
        "operator_slug": rec["assigned_operator"],
        "operator_name": rec["assigned_operator"].capitalize(),
        "vessel_or_property": "Barca riservata",
        "base_price_eur": 450 if guests <= 6 else 1250,
    }
    if d.get("offer_id"):
        custom_offer = conn.execute(
            "SELECT * FROM catalog_offers WHERE id=? AND owner_id=?", (d["offer_id"], owner)
        ).fetchone()
        if custom_offer:
            offer = dict(custom_offer)

    boat_price_eur = int(offer["base_price_eur"])
    assigned_operator = offer["operator_slug"]

    # Ristorante scelto o consigliato
    rest_venue = None
    if restaurant_venue_id:
        r_row = conn.execute("SELECT * FROM partner_venues WHERE id=? AND owner_id=?", (restaurant_venue_id, owner)).fetchone()
        if r_row:
            rest_venue = dict(r_row)
    elif wants_restaurant and rec["recommended_restaurant"]:
        rest_venue = rec["recommended_restaurant"]

    restaurant_name = rest_venue["name"] if rest_venue else ""
    restaurant_covers = guests if rest_venue else 0
    restaurant_fee_eur = _calculate_venue_fee(rest_venue, guests) if rest_venue else 0.0

    # Casa vacanza scelta (opzionale)
    stay_venue = None
    stay_price_eur = 0
    stay_fee_eur = 0.0
    if stay_venue_id and stay_nights > 0:
        s_row = conn.execute("SELECT * FROM partner_venues WHERE id=? AND owner_id=?", (stay_venue_id, owner)).fetchone()
        if s_row:
            stay_venue = dict(s_row)
            stay_price_eur = int((stay_venue.get("avg_spend_eur") or 200) * stay_nights)
            stay_fee_eur = round(stay_price_eur * (float(stay_venue.get("commission_value") or 10.0) / 100.0), 2)

    comm_pct = int(portal.get("commission_pct") or 10)
    partner_commission_eur = round(boat_price_eur * (comm_pct / 100.0), 2)
    total_price_eur = boat_price_eur + stay_price_eur
    net_margin_eur = round(boat_price_eur - partner_commission_eur + restaurant_fee_eur + stay_fee_eur, 2)

    token = f"LSL-{secrets.token_hex(3).upper()}"
    public_url = f"{request.host_url.rstrip('/')}/offer/{token}"
    custom_pitch = build_multilingual_pitch(
        lang=str(d.get("customer_language") or "it"),
        customer_name=customer_name,
        offer_title=offer["title"],
        operator_name=offer["operator_name"],
        vessel=offer.get("vessel_or_property") or "",
        guests_count=guests,
        event_date=event_date,
        time_slot_label=TIME_SLOT_LABELS.get(time_slot, time_slot),
        boat_price_eur=boat_price_eur,
        restaurant_name=restaurant_name,
        stay_name=stay_venue["name"] if stay_venue else "",
        stay_nights=stay_nights,
        stay_price_eur=stay_price_eur,
        public_url=public_url,
    )

    conn.execute(
        '''INSERT INTO bookings_and_quotes
           (owner_id, public_token, customer_name, customer_contact, customer_language,
            event_date, time_slot, guests_count, assigned_operator, offer_id, offer_title,
            boat_price_eur, restaurant_venue_id, restaurant_name, restaurant_covers,
            restaurant_time, restaurant_status, restaurant_fee_eur, stay_venue_id,
            stay_name, stay_nights, stay_price_eur, stay_fee_eur, source_portal_slug,
            source_lead_id, partner_commission_eur, total_price_eur, net_margin_eur,
            status, custom_pitch, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '13:30', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'inquiry', ?, ?)''',
        (
            owner,
            token,
            customer_name,
            customer_contact,
            str(d.get("customer_language") or "it"),
            event_date,
            time_slot,
            guests,
            assigned_operator,
            offer.get("id"),
            offer["title"],
            boat_price_eur,
            rest_venue["id"] if rest_venue else None,
            restaurant_name,
            restaurant_covers,
            "to_book" if rest_venue else "none",
             restaurant_fee_eur,
            stay_venue["id"] if stay_venue else None,
            stay_venue["name"] if stay_venue else "",
            stay_nights,
            stay_price_eur,
            stay_fee_eur,
            slug,
            portal.get("lead_id"),
            partner_commission_eur,
            total_price_eur,
            net_margin_eur,
            custom_pitch,
            str(d.get("notes") or f"Richiesta da portale partner: {portal['partner_name']}").strip(),
        ),
    )
    conn.execute("UPDATE partner_portals SET requests_count = requests_count + 1 WHERE id=?", (portal["id"],))
    conn.commit()
    conn.close()
    _mark_dirty()

    return jsonify({
        "ok": True,
        "token": token,
        "offer_url": f"/offer/{token}",
        "assigned_operator": assigned_operator,
        "offer_title": offer["title"],
        "restaurant_name": restaurant_name,
        "total_price_eur": total_price_eur,
    }), 201
