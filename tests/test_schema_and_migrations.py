"""init_db è idempotente e crea/aggiorna schema con le nuove colonne del funnel."""
import sqlite3


def _leads_columns(app_module):
    conn = sqlite3.connect(app_module.DB_PATH)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()]
    conn.close()
    return cols


def test_init_db_is_idempotent(app_with_db):
    # Chiamarlo una seconda volta non deve sollevare
    app_with_db.init_db()
    app_with_db.init_db()


def test_leads_has_funnel_columns(app_with_db):
    cols = _leads_columns(app_with_db)
    for c in ['contacted_at', 'replied_at', 'meeting_at', 'partner_since',
              'is_generic_email', 'contacts_json', 'draft_email', 'category']:
        assert c in cols, f"colonna funnel mancante: {c}"


def test_lead_statuses_whitelist(app_with_db):
    assert 'saved' in app_with_db.LEAD_STATUSES
    assert 'partner' in app_with_db.LEAD_STATUSES
    assert 'rejected' in app_with_db.LEAD_STATUSES
    assert app_with_db.LEAD_STATUS_TIMESTAMP['contacted'] == 'contacted_at'
    assert app_with_db.LEAD_STATUS_TIMESTAMP['partner'] == 'partner_since'


def test_is_generic_email_helper(app_with_db):
    assert app_with_db.is_generic_email('info@foo.com') is True
    assert app_with_db.is_generic_email('contact@bar.com') is True
    assert app_with_db.is_generic_email('BOOKING@baz.com') is True
    assert app_with_db.is_generic_email('mario.rossi@foo.com') is False
    assert app_with_db.is_generic_email('') is False
    assert app_with_db.is_generic_email('nodomain') is False
