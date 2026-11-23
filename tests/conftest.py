"""Test scaffolding: DB temporaneo isolato + client Flask con env safe.
Ogni test riparte con schema pulito via monkeypatch di DB_PATH.
"""
import os
import sys
import types
import tempfile
import pytest

# Evita blocchi di macOS SystemConfiguration (_scproxy) in ambienti sandbox/CI
if sys.platform == "darwin" and "_scproxy" not in sys.modules:
    _sc = types.ModuleType("_scproxy")
    _sc._get_proxy_settings = lambda: {}
    _sc._get_proxies = lambda: {}
    sys.modules["_scproxy"] = _sc

# I test devono trovare i moduli del progetto quando li lanci con "pytest tests/"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app_with_db(monkeypatch, tmp_path):
    """Reload di app con DB_PATH temporaneo. Idempotente: crea schema fresco."""
    db_file = tmp_path / "leslie-test.sqlite"
    monkeypatch.setenv("SECRET_KEY", "test-secret-do-not-use")
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "fake-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setenv("GCS_BUCKET", "")
    monkeypatch.setenv("PLACES_MONTHLY_BUDGET_USD", "5")
    monkeypatch.setenv("DEEPSEEK_MONTHLY_BUDGET_USD", "2")
    monkeypatch.setenv("SCOUT_MIN_INTERVAL_SECONDS", "0")

    # Ricarichiamo tutti i moduli progetto perché DB_PATH viene letto a import time
    import importlib
    for mod_name in ["config", "ai_analyzer", "scraper", "autonomous_scout", "commerce_engine", "app"]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import config
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    import app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", str(db_file))
    app_module.init_db()
    app_module.app.config['TESTING'] = True
    return app_module


@pytest.fixture
def client(app_with_db):
    with app_with_db.app.test_client() as c:
        yield c


@pytest.fixture
def as_owner(client):
    """Client con header X-User-ID=owner."""
    class Wrapped:
        def get(self, url, **kw):
            headers = kw.pop('headers', {}) or {}
            headers.setdefault('X-User-ID', 'owner')
            return client.get(url, headers=headers, **kw)
        def post(self, url, **kw):
            headers = kw.pop('headers', {}) or {}
            headers.setdefault('X-User-ID', 'owner')
            return client.post(url, headers=headers, **kw)
        def delete(self, url, **kw):
            headers = kw.pop('headers', {}) or {}
            headers.setdefault('X-User-ID', 'owner')
            return client.delete(url, headers=headers, **kw)
    return Wrapped()
