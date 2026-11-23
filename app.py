import atexit
import os
import re
import sqlite3
import threading
import time
import random
from flask import Flask, render_template, request, jsonify, Response, session
import json
import concurrent.futures
from flask_cors import CORS
from dotenv import load_dotenv
import requests
import csv
import io
from urllib.parse import quote_plus
from scraper import scrape_for_contacts
from config import QUERY_POOLS, CATEGORY_WEIGHTS, DB_PATH
import autonomous_scout
from ai_analyzer import generate_json
from logging_setup import get_logger
from commerce_engine import commerce_bp, init_commerce_tables

load_dotenv()
log = get_logger("leslie.app")

app = Flask(__name__)
app.register_blueprint(commerce_bp)
_secret_key_env = os.environ.get("SECRET_KEY")
if _secret_key_env:
    app.secret_key = _secret_key_env
elif os.environ.get("FLASK_ENV") == "development":
    app.secret_key = "leslie-dev-only-do-not-use-in-prod"
    print("[WARN] SECRET_KEY non impostata: uso fallback DEV. Non deployare così.")
else:
    raise RuntimeError(
        "SECRET_KEY mancante. Impostala come secret in Cloud Run "
        "(o esporta FLASK_ENV=development per lavorare in locale)."
    )

# Restrict CORS to specific origins in production
# CORS: same-origin di default. Override via env CORS_ORIGINS (comma-separated).
_cors_env = os.environ.get("CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else [
    "http://localhost:8080", "http://127.0.0.1:8080"
]
CORS(app, origins=_cors_origins, supports_credentials=True)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024  # 256 KB body limit


@app.after_request
def _set_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response

# DB_PATH is now imported from config
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

print(f"[*] Leslie OS Avvio - Bucket: {GCS_BUCKET}")


# ─── GCS PERSISTENCE ────────────────────────────────────────────────────────────

def gcs_download():
    """Scarica il DB da GCS all'avvio (se esiste)."""
    if not GCS_BUCKET:
        return
    try:
        from google.cloud import storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET)
        blob = bucket.blob("agency_db.sqlite")
        if blob.exists():
            blob.download_to_filename(DB_PATH)
            print(f"[GCS] Database ripristinato da gs://{GCS_BUCKET}/agency_db.sqlite")
        else:
            print("[GCS] Nessun database trovato nel bucket, ne verrà creato uno nuovo.")
    except Exception as e:
        log.error("gcs_download_failed", extra={"error": str(e), "bucket": GCS_BUCKET})


_gcs_dirty = threading.Event()
_gcs_upload_lock = threading.Lock()
_GCS_DEBOUNCE_SECONDS = 2.0
_GCS_UPLOAD_RETRIES = 3
_gcs_last_upload_ok = None  # None = mai tentato, True/False = ultimo esito
_gcs_last_upload_ts = None  # epoch del completamento (ok o fail)


def _wal_checkpoint():
    """Forza il checkpoint del WAL nel file principale prima dell'upload."""
    try:
        conn = get_db_conn()
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.close()
    except Exception as e:
        print(f"[GCS] wal_checkpoint fallito: {e}")


def _do_gcs_upload():
    """Upload sincrono del DB su GCS, con retry e backoff."""
    global _gcs_last_upload_ok, _gcs_last_upload_ts
    if not GCS_BUCKET:
        return True
    from google.cloud import storage
    for attempt in range(1, _GCS_UPLOAD_RETRIES + 1):
        try:
            storage_client = storage.Client()
            bucket = storage_client.bucket(GCS_BUCKET)
            blob = bucket.blob("agency_db.sqlite")
            blob.upload_from_filename(DB_PATH)
            log.info("gcs_upload_ok", extra={"attempt": attempt, "bucket": GCS_BUCKET})
            _gcs_last_upload_ok = True
            _gcs_last_upload_ts = time.time()
            return True
        except Exception as e:
            log.warning("gcs_upload_retry", extra={"attempt": attempt, "max_attempts": _GCS_UPLOAD_RETRIES, "error": str(e)})
            if attempt < _GCS_UPLOAD_RETRIES:
                time.sleep(2 ** (attempt - 1))
    _gcs_last_upload_ok = False
    _gcs_last_upload_ts = time.time()
    log.error("gcs_upload_failed_all_retries", extra={"bucket": GCS_BUCKET})
    return False


def gcs_upload():
    """Upload sincrono con checkpoint WAL e lock — usato da atexit/path manuali.

    Per le write delle request HTTP usare mark_dirty(): il worker debounced
    aggrega più write in un singolo upload.
    """
    with _gcs_upload_lock:
        _wal_checkpoint()
        _do_gcs_upload()


def mark_dirty():
    """Segnala una scrittura: il worker debounced farà l'upload."""
    _gcs_dirty.set()


def _gcs_upload_worker():
    """Worker singolo: aspetta segnale dirty, debouncing, poi upload con lock."""
    while True:
        _gcs_dirty.wait()
        time.sleep(_GCS_DEBOUNCE_SECONDS)
        _gcs_dirty.clear()
        with _gcs_upload_lock:
            _wal_checkpoint()
            ok = _do_gcs_upload()
            if not ok:
                _gcs_dirty.set()
                time.sleep(5)


def _start_gcs_worker():
    if GCS_BUCKET:
        t = threading.Thread(target=_gcs_upload_worker, daemon=True, name="gcs-upload-worker")
        t.start()


def _gcs_final_flush():
    """Handler atexit: ultimo upload sincrono prima di terminare con timeout."""
    if not GCS_BUCKET:
        return
    if _gcs_dirty.is_set():
        print("[GCS] Flush finale prima dell'exit...")
        def flush():
            with _gcs_upload_lock:
                _wal_checkpoint()
                _do_gcs_upload()
        t = threading.Thread(target=flush)
        t.start()
        t.join(timeout=12)
        if t.is_alive():
            print("[GCS] Flush finale in timeout, uscita forzata.")


atexit.register(_gcs_final_flush)


def db_write(func):
    """Decorator: esegue la funzione e segnala dirty per upload debounced."""
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        mark_dirty()
        return result
    wrapper.__name__ = func.__name__
    return wrapper


@app.route('/health')
def health():
    return jsonify({"status": "healthy", "timestamp": time.time()})


@app.route('/api/admin/sync', methods=['POST'])
def force_sync():
    """Forza il sync manuale su GCS."""
    if not GCS_BUCKET:
        return jsonify({"error": "GCS_BUCKET non configurato"}), 400
    try:
        gcs_upload()
        return jsonify({"ok": True, "message": "Database sincronizzato su Cloud Storage."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/status')
def api_status():
    """Stato sync GCS per indicator UI."""
    return jsonify({
        "gcs_enabled": bool(GCS_BUCKET),
        "gcs_dirty": _gcs_dirty.is_set(),
        "last_upload_ok": _gcs_last_upload_ok,
        "last_upload_ts": _gcs_last_upload_ts,
    })


# ─── API USAGE TRACKING ──────────────────────────────────────────────────────────
# Soglie configurabili via env. Default cauti per evitare bill shock.
PLACES_DAILY_LIMIT = int(os.environ.get("PLACES_DAILY_LIMIT", "1000"))
PLACES_MONTHLY_LIMIT = int(os.environ.get("PLACES_MONTHLY_LIMIT", "10000"))
DEEPSEEK_DAILY_LIMIT = int(os.environ.get("DEEPSEEK_DAILY_LIMIT", "200"))
DEEPSEEK_MONTHLY_LIMIT = int(os.environ.get("DEEPSEEK_MONTHLY_LIMIT", "3000"))

# Pricing USD per 1k calls — Google Maps Platform 2025 legacy Places API:
#   textsearch = $32/1000 (SKU: Places API - Text Search)
#   details    = $17/1000 basic + $3/1000 contact fields (usiamo website+phone → +$3)
# Override via env se Google cambia i prezzi.
PLACES_TEXTSEARCH_COST_PER_1K = float(os.environ.get("PLACES_TEXTSEARCH_COST_PER_1K", "32.0"))
PLACES_DETAILS_COST_PER_1K = float(os.environ.get("PLACES_DETAILS_COST_PER_1K", "20.0"))
# Retro-compat: alias del vecchio nome, non più usato per il calcolo ma tenuto per env legacy
PLACES_COST_PER_1K = float(os.environ.get("PLACES_COST_PER_1K", "20.0"))
DEEPSEEK_COST_PER_CALL = float(os.environ.get("DEEPSEEK_COST_PER_CALL", "0.001"))

# Hard cap MENSILE in USD — blocca ensure_quota se il costo stimato del mese
# supera questa soglia. Il vero rimedio alle "bollette sorpresa": non solo
# call-count, ma dollari. Default cauto: $5/mese. Override via env.
PLACES_MONTHLY_BUDGET_USD = float(os.environ.get("PLACES_MONTHLY_BUDGET_USD", "5.0"))
DEEPSEEK_MONTHLY_BUDGET_USD = float(os.environ.get("DEEPSEEK_MONTHLY_BUDGET_USD", "2.0"))

_usage_lock = threading.Lock()

# Rate limit su endpoint costosi (scout). Per-utente, in-memory.
# Ogni scout costa ~$0.4-0.5 fra Places+DeepSeek: senza rate limit un utente
# che clicca 30 volte in 5 min può bruciare il budget mensile in un pomeriggio.
SCOUT_MIN_INTERVAL_SECONDS = float(os.environ.get("SCOUT_MIN_INTERVAL_SECONDS", "20"))
_scout_last_call = {}  # user_id → epoch dell'ultima chiamata
_scout_rate_lock = threading.Lock()


def check_scout_rate_limit(uid):
    """Restituisce (ok: bool, retry_after: int|None).
    Consente uno scout ogni SCOUT_MIN_INTERVAL_SECONDS per utente."""
    now = time.time()
    with _scout_rate_lock:
        last = _scout_last_call.get(uid, 0)
        elapsed = now - last
        if elapsed < SCOUT_MIN_INTERVAL_SECONDS:
            return False, int(SCOUT_MIN_INTERVAL_SECONDS - elapsed) + 1
        _scout_last_call[uid] = now
        return True, None


class UsageLimitExceeded(Exception):
    """Sollevata quando una chiamata API supera la soglia giornaliera."""
    def __init__(self, provider, today, limit):
        self.provider = provider
        self.today = today
        self.limit = limit
        super().__init__(f"{provider} daily limit reached: {today}/{limit}")


def _today_utc():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _calls_today(provider):
    """Quante chiamate per provider oggi (UTC)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT COALESCE(SUM(calls),0) FROM api_usage WHERE day=? AND provider=?",
            (_today_utc(), provider)
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def _cost_this_month(provider):
    """Stima USD spesa nel mese corrente per un provider, con pricing per endpoint."""
    month_prefix = _today_utc()[:7]
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT endpoint, SUM(calls) FROM api_usage WHERE day LIKE ? AND provider=? GROUP BY endpoint",
            (f"{month_prefix}%", provider)
        ).fetchall()
        conn.close()
    except Exception:
        return 0.0
    cost = 0.0
    for endpoint, calls in rows:
        cost += _cost_for(provider, endpoint, calls or 0)
    return cost


def _cost_for(provider, endpoint, calls):
    """Costo USD per un numero di chiamate a un dato endpoint."""
    if provider == "places":
        rate = PLACES_TEXTSEARCH_COST_PER_1K if endpoint == "textsearch" else PLACES_DETAILS_COST_PER_1K
        return calls * rate / 1000.0
    if provider == "deepseek":
        return calls * DEEPSEEK_COST_PER_CALL
    return 0.0


def ensure_quota(provider):
    """Solleva UsageLimitExceeded se la soglia giornaliera O il budget mensile
    è raggiunto. Va chiamata SUBITO prima di emettere una chiamata API a pagamento.

    Emette anche warning strutturati a 80% budget mensile e error strutturati
    al raggiungimento del cap — così Cloud Logging può paginare/alertare."""
    limit = PLACES_DAILY_LIMIT if provider == "places" else DEEPSEEK_DAILY_LIMIT
    today = _calls_today(provider)
    if today >= limit:
        log.error("quota_daily_exceeded", extra={"provider": provider, "today": today, "limit": limit})
        raise UsageLimitExceeded(provider, today, limit)
    # Cap mensile in USD — protezione anti bill-shock
    budget = PLACES_MONTHLY_BUDGET_USD if provider == "places" else DEEPSEEK_MONTHLY_BUDGET_USD
    cost = _cost_this_month(provider)
    pct = 100 * cost / max(budget, 0.01)
    if cost >= budget:
        log.error("quota_monthly_exceeded", extra={"provider": provider, "cost_usd": round(cost, 3), "budget_usd": budget})
        raise UsageLimitExceeded(
            provider,
            f"${cost:.2f}",
            f"${budget:.2f} (budget mensile)",
        )
    if pct >= 80:
        log.warning("quota_close_to_cap", extra={"provider": provider, "pct": round(pct, 1), "cost_usd": round(cost, 3), "budget_usd": budget})


@app.errorhandler(UsageLimitExceeded)
def handle_usage_limit(e):
    """Risposta uniforme 429 quando una route propaga UsageLimitExceeded."""
    return jsonify({
        "error": "limit_exceeded",
        "provider": e.provider,
        "today": e.today,
        "limit": e.limit,
        "message": f"Soglia giornaliera {e.provider} raggiunta ({e.today}/{e.limit})."
    }), 429


def track_api_call(provider, endpoint, n=1):
    """Incrementa contatore chiamate API. Best-effort, non blocca su errore."""
    if n <= 0:
        return
    try:
        with _usage_lock:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO api_usage(day, provider, endpoint, calls) VALUES (?,?,?,?) "
                "ON CONFLICT(day, provider, endpoint) DO UPDATE SET calls = calls + excluded.calls",
                (_today_utc(), provider, endpoint, n)
            )
            conn.commit()
            conn.close()
        mark_dirty()
    except Exception as e:
        log.warning("usage_track_failed", extra={"error": str(e)})


@app.route('/api/reports/monthly')
def api_report_monthly():
    """Riepilogo mensile: costi API + attività funnel + partner nuovi.
    Usato dalla UI per il pannello "Report" e da un cron (documentato in
    HANDOFF.md) per generare la mail di fine mese. Query param `month=YYYY-MM`
    per report retrospettivi (default: mese corrente UTC)."""
    uid = get_user_id()
    month = request.args.get('month', _today_utc()[:7])
    if not re.match(r'^\d{4}-\d{2}$', month):
        return jsonify({"error": "Formato month invalido, usa YYYY-MM"}), 400

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT provider, endpoint, SUM(calls) AS c FROM api_usage WHERE day LIKE ? GROUP BY provider, endpoint",
            (f"{month}%",),
        ).fetchall()
        places_cost = sum(_cost_for('places', r['endpoint'], r['c'] or 0) for r in rows if r['provider'] == 'places')
        deepseek_cost = sum(_cost_for('deepseek', r['endpoint'], r['c'] or 0) for r in rows if r['provider'] == 'deepseek')

        new_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE user_id=? AND strftime('%Y-%m', created_at)=?",
            (uid, month),
        ).fetchone()[0]
        premium = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE user_id=? AND is_premium=1 AND strftime('%Y-%m', created_at)=?",
            (uid, month),
        ).fetchone()[0]
        contacted = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE user_id=? AND strftime('%Y-%m', contacted_at)=?",
            (uid, month),
        ).fetchone()[0]
        new_partners = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE user_id=? AND strftime('%Y-%m', partner_since)=?",
            (uid, month),
        ).fetchone()[0]
        active_partners = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE user_id=? AND status='partner'",
            (uid,),
        ).fetchone()[0]
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    total_cost = round(places_cost + deepseek_cost, 2)
    cost_per_partner = round(total_cost / max(new_partners, 1), 2) if new_partners else None
    return jsonify({
        "user": uid,
        "month": month,
        "costs": {
            "places_usd": round(places_cost, 2),
            "deepseek_usd": round(deepseek_cost, 2),
            "total_usd": total_cost,
        },
        "pipeline": {
            "new_leads": new_leads,
            "premium": premium,
            "contacted": contacted,
            "new_partners": new_partners,
            "active_partners": active_partners,
            "cost_per_new_partner_usd": cost_per_partner,
        },
    })


@app.route('/api/reports/monthly.html')
def api_report_monthly_html():
    """Versione HTML del report mensile — pensata per essere il corpo di una
    mail generata da cron (vedi HANDOFF.md). Include tutti i numeri del JSON."""
    uid = get_user_id()
    month = request.args.get('month', _today_utc()[:7])
    if not re.match(r'^\d{4}-\d{2}$', month):
        return "Formato month invalido", 400
    r = api_report_monthly()
    # api_report_monthly restituisce una Response Flask (jsonify). Ricostruiamo il dict.
    data = json.loads(r.get_data(as_text=True)) if hasattr(r, 'get_data') else {}
    if not data or 'costs' not in data:
        return "Errore generazione report", 500
    p = data['pipeline']
    c = data['costs']
    cpp = p.get('cost_per_new_partner_usd')
    html = f"""<!doctype html><html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif; background:#f8f9fa; padding:24px;">
    <div style="max-width:600px; margin:0 auto; background:#fff; border-radius:16px; overflow:hidden; box-shadow:0 4px 16px rgba(0,0,0,0.06);">
      <div style="background:#0a192f; color:#c5a059; padding:20px 24px;">
        <div style="font-size:12px; letter-spacing:2px; text-transform:uppercase;">Leslie · CIBO Marine</div>
        <div style="font-size:22px; font-weight:700; margin-top:4px;">Report mensile — {month}</div>
        <div style="font-size:12px; color:#8892b0; margin-top:2px;">Utente: {uid}</div>
      </div>
      <div style="padding:24px;">
        <h3 style="margin:0 0 12px; font-size:15px;">Pipeline</h3>
        <table cellspacing="0" cellpadding="8" style="width:100%; font-size:13px; border-collapse:collapse;">
          <tr><td>Nuovi lead nel mese</td><td align="right"><strong>{p['new_leads']}</strong></td></tr>
          <tr style="background:#fafafa;"><td>Di cui premium</td><td align="right"><strong>{p['premium']}</strong></td></tr>
          <tr><td>Contattati nel mese</td><td align="right"><strong>{p['contacted']}</strong></td></tr>
          <tr style="background:#fafafa;"><td>Partner nuovi</td><td align="right"><strong style="color:#22a559;">{p['new_partners']}</strong></td></tr>
          <tr><td>Partner attivi totali</td><td align="right"><strong>{p['active_partners']}</strong></td></tr>
        </table>
        <h3 style="margin:20px 0 12px; font-size:15px;">Costi</h3>
        <table cellspacing="0" cellpadding="8" style="width:100%; font-size:13px; border-collapse:collapse;">
          <tr><td>Google Places</td><td align="right">${c['places_usd']}</td></tr>
          <tr style="background:#fafafa;"><td>DeepSeek AI</td><td align="right">${c['deepseek_usd']}</td></tr>
          <tr><td><strong>Totale</strong></td><td align="right"><strong>${c['total_usd']}</strong></td></tr>
          {f'<tr style="background:#fafafa;"><td>Costo per partner nuovo</td><td align="right"><strong>${cpp}</strong></td></tr>' if cpp is not None else ''}
        </table>
        <p style="margin-top:20px; font-size:11px; color:#8892b0;">
          Report generato automaticamente. La metrica che conta per CIBO Marine è "Partner attivi": ogni partner porta clienti UHNWI a bordo.
        </p>
      </div>
    </div></body></html>"""
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/api/usage/timeseries')
def api_usage_timeseries():
    """Serie giornaliera di costo/chiamate per identificare picchi.
    Query param: days (default 30, max 90)."""
    days = min(90, max(1, request.args.get('days', 30, type=int)))
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT day, provider, endpoint, SUM(calls) as calls
            FROM api_usage
            WHERE day >= date('now', '-{days} days')
            GROUP BY day, provider, endpoint
            ORDER BY day ASC
            """
        ).fetchall()
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Aggreghiamo per (day) → { places_cost, deepseek_cost, places_calls, ... }
    daily = {}
    for r in rows:
        d = daily.setdefault(r["day"], {
            "day": r["day"],
            "places_textsearch": 0, "places_details": 0,
            "deepseek_chat": 0,
            "places_cost_usd": 0.0, "deepseek_cost_usd": 0.0, "total_cost_usd": 0.0,
        })
        calls = r["calls"] or 0
        cost = _cost_for(r["provider"], r["endpoint"], calls)
        if r["provider"] == "places":
            key = f"places_{r['endpoint']}"
            d[key] = d.get(key, 0) + calls
            d["places_cost_usd"] = round(d["places_cost_usd"] + cost, 4)
        elif r["provider"] == "deepseek":
            d["deepseek_chat"] += calls
            d["deepseek_cost_usd"] = round(d["deepseek_cost_usd"] + cost, 4)
        d["total_cost_usd"] = round(d["places_cost_usd"] + d["deepseek_cost_usd"], 4)
    return jsonify(sorted(daily.values(), key=lambda x: x["day"]))


@app.route('/api/usage')
def api_usage():
    """Conteggi chiamate API e soglie per il pannello frontend."""
    today = _today_utc()
    month_prefix = today[:7]  # YYYY-MM
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        today_rows = conn.execute(
            "SELECT provider, endpoint, calls FROM api_usage WHERE day = ?",
            (today,)
        ).fetchall()
        month_rows = conn.execute(
            "SELECT provider, SUM(calls) as calls FROM api_usage WHERE day LIKE ? GROUP BY provider",
            (f"{month_prefix}%",)
        ).fetchall()
        conn.close()
    except Exception as e:
        print(f"[USAGE] read failed: {e}")
        today_rows, month_rows = [], []

    def by_provider(rows, provider):
        return sum(r["calls"] for r in rows if r["provider"] == provider)

    places_today = by_provider(today_rows, "places")
    deepseek_today = by_provider(today_rows, "deepseek")
    places_month = next((r["calls"] for r in month_rows if r["provider"] == "places"), 0)
    deepseek_month = next((r["calls"] for r in month_rows if r["provider"] == "deepseek"), 0)

    # Breakdown per endpoint (places: textsearch + details)
    places_breakdown = {r["endpoint"]: r["calls"] for r in today_rows if r["provider"] == "places"}

    # Statistiche ricerche del mese
    try:
        search_conn = sqlite3.connect(DB_PATH)
        total_searches = search_conn.execute(
            "SELECT COUNT(*) FROM search_history WHERE last_run LIKE ?", (f"{month_prefix}%",)
        ).fetchone()[0]
        search_conn.close()
    except Exception:
        total_searches = 0

    # Costo places: split textsearch (più caro) vs details, con i prezzi reali 2025
    places_month_breakdown = {}
    conn_bd = sqlite3.connect(DB_PATH)
    for endpoint, s in conn_bd.execute(
        "SELECT endpoint, SUM(calls) FROM api_usage WHERE provider='places' AND day LIKE ? GROUP BY endpoint",
        (f"{month_prefix}%",)
    ):
        places_month_breakdown[endpoint] = s or 0
    conn_bd.close()

    places_cost = round(sum(_cost_for("places", ep, n) for ep, n in places_month_breakdown.items()), 2)
    deepseek_cost = round(_cost_for("deepseek", "chat", deepseek_month), 2)
    total_cost = round(places_cost + deepseek_cost, 2)
    avg_cost = round(total_cost / max(total_searches, 1), 3)

    return jsonify({
        "today": today,
        "places": {
            "today": places_today,
            "month": places_month,
            "daily_limit": PLACES_DAILY_LIMIT,
            "monthly_limit": PLACES_MONTHLY_LIMIT,
            "monthly_budget_usd": PLACES_MONTHLY_BUDGET_USD,
            "breakdown": places_breakdown,
            "monthly_breakdown": places_month_breakdown,
            "estimated_cost_usd": places_cost,
            "budget_used_pct": round(100 * places_cost / max(PLACES_MONTHLY_BUDGET_USD, 0.01), 1),
            "pricing": {
                "textsearch_per_1k_usd": PLACES_TEXTSEARCH_COST_PER_1K,
                "details_per_1k_usd": PLACES_DETAILS_COST_PER_1K,
            },
        },
        "deepseek": {
            "today": deepseek_today,
            "month": deepseek_month,
            "daily_limit": DEEPSEEK_DAILY_LIMIT,
            "monthly_limit": DEEPSEEK_MONTHLY_LIMIT,
            "monthly_budget_usd": DEEPSEEK_MONTHLY_BUDGET_USD,
            "estimated_cost_usd": deepseek_cost,
            "budget_used_pct": round(100 * deepseek_cost / max(DEEPSEEK_MONTHLY_BUDGET_USD, 0.01), 1),
        },
        "total_cost_usd": total_cost,
        "total_searches": total_searches,
        "avg_cost_per_search": avg_cost,
    })


# ─── DATABASE ────────────────────────────────────────────────────────────────────

def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA synchronous=NORMAL;")
    c.execute("PRAGMA foreign_keys=ON;")
    c.execute('''CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL DEFAULT 'dany',
        company_name TEXT NOT NULL,
        website TEXT,
        phone TEXT,
        email TEXT,
        address TEXT,
        source_query TEXT,
        status TEXT DEFAULT 'saved',
        notes TEXT DEFAULT '',
        ai_audit TEXT DEFAULT '',
        user_notes TEXT DEFAULT '',
        selling_angle TEXT DEFAULT '',
        is_premium BOOLEAN DEFAULT 0,
        seo_data TEXT DEFAULT '{}',
        contacted_at TIMESTAMP,
        replied_at TIMESTAMP,
        meeting_at TIMESTAMP,
        partner_since TIMESTAMP,
        is_generic_email BOOLEAN DEFAULT 0,
        contacts_json TEXT DEFAULT '[]',
        draft_email TEXT DEFAULT '',
        category TEXT DEFAULT '',
        fit_score INTEGER,
        fit_reason TEXT DEFAULT '',
        fit_dimensions_json TEXT DEFAULT '{}',
        fit_confidence TEXT DEFAULT '',
        fit_evidence_json TEXT DEFAULT '[]',
        analyzed_at TIMESTAMP,
        analysis_version TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS search_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL DEFAULT 'dany',
        query TEXT NOT NULL,
        results_count INTEGER DEFAULT 0,
        new_count INTEGER DEFAULT 0,
        is_exhausted BOOLEAN DEFAULT 0,
        last_run TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, query)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS learned_intelligence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        niche_key TEXT NOT NULL,
        description TEXT DEFAULT '',
        market_saturation INTEGER DEFAULT 0,
        suggested_queries TEXT, -- JSON list
        confidence INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, niche_key)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS user_settings (
        user_id TEXT PRIMARY KEY,
        weights TEXT DEFAULT '{}'
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS client_workspaces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id TEXT NOT NULL,
        slug TEXT NOT NULL,
        name TEXT NOT NULL,
        sector TEXT DEFAULT '',
        geography TEXT DEFAULT '',
        ideal_partner TEXT DEFAULT '',
        exclusions TEXT DEFAULT '',
        offer_name TEXT DEFAULT '',
        boat_type TEXT DEFAULT '',
        max_guests INTEGER,
        experience_style TEXT DEFAULT '',
        target_customer TEXT DEFAULT '',
        business_type TEXT DEFAULT 'nautical',
        inventory_count INTEGER DEFAULT 1,
        price_positioning TEXT DEFAULT '',
        seasonality TEXT DEFAULT '',
        primary_goal TEXT DEFAULT '',
        differentiators TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        archived BOOLEAN DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(owner_id, slug)
    )''')

    for col, coltype in [
        ("offer_name", "TEXT DEFAULT ''"),
        ("boat_type", "TEXT DEFAULT ''"),
        ("max_guests", "INTEGER"),
        ("experience_style", "TEXT DEFAULT ''"),
        ("target_customer", "TEXT DEFAULT ''"),
        ("business_type", "TEXT DEFAULT 'nautical'"),
        ("inventory_count", "INTEGER DEFAULT 1"),
        ("price_positioning", "TEXT DEFAULT ''"),
        ("seasonality", "TEXT DEFAULT ''"),
        ("primary_goal", "TEXT DEFAULT ''"),
        ("differentiators", "TEXT DEFAULT ''"),
    ]:
        try:
            c.execute(f"ALTER TABLE client_workspaces ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass

    c.execute('''CREATE TABLE IF NOT EXISTS api_usage (
        day TEXT NOT NULL,            -- ISO date UTC: YYYY-MM-DD
        provider TEXT NOT NULL,       -- 'places' | 'deepseek'
        endpoint TEXT NOT NULL,       -- 'textsearch' | 'details' | 'chat'
        calls INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(day, provider, endpoint)
    )''')
    
    # Migrazione: Aggiungi user_id se manca (per DB esistenti)
    try:
        c.execute("ALTER TABLE leads ADD COLUMN user_id TEXT NOT NULL DEFAULT 'dany'")
    except sqlite3.OperationalError: pass  # colonna/indice già presente
    try:
        c.execute("ALTER TABLE search_history ADD COLUMN user_id TEXT NOT NULL DEFAULT 'dany'")
    except sqlite3.OperationalError: pass  # colonna/indice già presente
    try:
        c.execute("ALTER TABLE learned_intelligence ADD COLUMN description TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass  # colonna/indice già presente
    try:
        c.execute("ALTER TABLE learned_intelligence ADD COLUMN market_saturation INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass  # colonna/indice già presente
    try:
        c.execute("ALTER TABLE leads ADD COLUMN seo_data TEXT DEFAULT '{}'")
    except sqlite3.OperationalError: pass  # colonna/indice già presente
    try:
        c.execute("ALTER TABLE leads ADD COLUMN selling_angle TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass  # colonna/indice già presente
    try:
        c.execute("ALTER TABLE leads ADD COLUMN is_premium BOOLEAN DEFAULT 0")
    except sqlite3.OperationalError: pass  # colonna/indice già presente
    try:
        c.execute("ALTER TABLE user_settings ADD COLUMN settings TEXT DEFAULT '{}'")
    except sqlite3.OperationalError: pass  # colonna/indice già presente

    # Migrazione funnel outreach (Fase 2 delivery): stati aggiuntivi + timestamp
    for col, coltype in [
        ("contacted_at", "TIMESTAMP"),
        ("replied_at", "TIMESTAMP"),
        ("meeting_at", "TIMESTAMP"),
        ("partner_since", "TIMESTAMP"),
        ("is_generic_email", "BOOLEAN DEFAULT 0"),
        ("contacts_json", "TEXT DEFAULT '[]'"),
        ("draft_email", "TEXT DEFAULT ''"),
        ("category", "TEXT DEFAULT ''"),
        ("ai_audit", "TEXT DEFAULT ''"),
        ("user_notes", "TEXT DEFAULT ''"),
        ("fit_score", "INTEGER"),
        ("fit_reason", "TEXT DEFAULT ''"),
        ("fit_dimensions_json", "TEXT DEFAULT '{}'"),
        ("fit_confidence", "TEXT DEFAULT ''"),
        ("fit_evidence_json", "TEXT DEFAULT '[]'"),
        ("analyzed_at", "TIMESTAMP"),
        ("analysis_version", "TEXT DEFAULT ''"),
    ]:
        try:
            c.execute(f"ALTER TABLE leads ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass

    # Migrazione semantica: il vecchio campo `notes` conteneva l'audit AI.
    c.execute("UPDATE leads SET ai_audit=notes WHERE COALESCE(ai_audit,'')='' AND COALESCE(notes,'')!=''")
    
    # Forzatura indici UNICI necessari per ON CONFLICT
    try:
        c.execute("DROP INDEX IF EXISTS idx_history_user_query")
        c.execute("CREATE UNIQUE INDEX idx_history_user_query ON search_history(user_id, query)")
    except sqlite3.OperationalError: pass  # colonna/indice già presente

    try:
        # Lo stesso partner può essere rilevante per clienti diversi, ma non va
        # duplicato all'interno dello stesso spazio cliente.
        c.execute("DROP INDEX IF EXISTS idx_leads_website")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_user_website ON leads(user_id, website)")
    except sqlite3.OperationalError: pass  # DB legacy con vincolo UNIQUE(website)
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_intel_user_niche ON learned_intelligence(user_id, niche_key)")
    except sqlite3.OperationalError: pass  # colonna/indice già presente
    
    # Performance Indices
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_leads_user ON leads(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_leads_user_status ON leads(user_id, status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_leads_user_created ON leads(user_id, created_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_leads_user_fit ON leads(user_id, fit_score DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_history_user_last ON search_history(user_id, last_run)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_intel_user_conf ON learned_intelligence(user_id, confidence)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_usage_day ON api_usage(day)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_client_workspaces_owner_archived ON client_workspaces(owner_id, archived)")
    except sqlite3.OperationalError: pass  # colonna/indice già presente

    # Inizializza tabelle e catalogo Leslie 2.0 (Barche, Ristoranti, Case, Portali)
    init_commerce_tables(conn)

    c.execute("PRAGMA optimize")

    conn.commit()
    conn.close()


# ─── QUERY GENERATOR ────────────────────────────────────────────────────────────

# ─── QUERY POOLS STRATEGICI ──────────────────────────────────────────────────
# Chi CONCRETAMENTE manderebbe clienti a noleggiare barche di lusso a Portofino?

def generate_queries(n=20):
    """Estrae query pesate + query 'imparate' + espansione AI se necessario."""
    uid = get_user_id()
    all_categories = list(QUERY_POOLS.keys())
    result = []

    # Prima vengono le query costruite sul profilo commerciale del cliente.
    workspace = get_active_workspace()
    if workspace:
        geography = workspace.get('geography') or 'Portofino'
        partner_terms = [
            term.strip() for term in re.split(r'[,;\n]+', workspace.get('ideal_partner') or '') if term.strip()
        ]
        for term in partner_terms[:8]:
            result.append({
                "query": f"{term} {geography}",
                "category": f"🎯 {workspace['name']}",
            })
    
    # 1. Filtra query già 'esaurite' dal database
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    exhausted = {r['query'] for r in conn.execute("SELECT query FROM search_history WHERE user_id=? AND is_exhausted=1", (uid,)).fetchall()}
    conn.close()

    # 2. Recupera query 'imparate' (se presenti)
    try:
        conn = get_db_conn()
        conn.row_factory = sqlite3.Row
        learned = conn.execute("SELECT * FROM learned_intelligence WHERE user_id=? ORDER BY confidence DESC LIMIT 5", (uid,)).fetchall()
        conn.close()
        for row in learned:
            qs = json.loads(row['suggested_queries'])
            for q in qs:
                if q not in exhausted:
                    result.append({"query": q, "category": f"🎯 Nicchia: {row['niche_key']}"})
    except sqlite3.OperationalError: pass  # colonna/indice già presente

    # 3. Query standard pesate (evitando le esaurite) — usa pesi SALVATI dall'utente
    conn3 = get_db_conn()
    _w_row = conn3.execute("SELECT weights FROM user_settings WHERE user_id=?", (uid,)).fetchone()
    conn3.close()
    user_weights = json.loads(_w_row[0]) if _w_row and _w_row[0] else CATEGORY_WEIGHTS

    total_weight = sum(user_weights.get(cat, 1) for cat in all_categories)
    if total_weight == 0: total_weight = len(all_categories)

    target_std = n - len(result)
    if target_std < 10: target_std = 10

    custom_queries = _load_custom_queries(uid)

    for cat in all_categories:
        if workspace and workspace.get('business_type') == 'hospitality' and (cat.startswith('🏨') or cat.startswith('⛵')):
            continue
        weight = user_weights.get(cat, 1)
        if weight == 0: continue

        # Pool: query base + query custom, evitando le esaurite
        base_queries = list(QUERY_POOLS[cat]) + list(custom_queries.get(cat, []))
        pool = [q for q in base_queries if q not in exhausted]
        
        if weight >= 80 and len(pool) < 5:
            # Espansione AI: le query si persistono nei custom_queries dell'UTENTE,
            # NON mutando QUERY_POOLS (era globale → leak cross-user + perdita al restart).
            print(f"[AI] Espansione nicchia per: {cat} (user={uid})")
            new_qs = expand_niche_queries(cat)
            if new_qs:
                cat_custom = custom_queries.setdefault(cat, [])
                base_set = set(QUERY_POOLS[cat]) | set(cat_custom)
                added = [q for q in new_qs if q not in base_set]
                if added:
                    cat_custom.extend(added)
                    _save_custom_queries(uid, custom_queries)
                pool.extend(q for q in new_qs if q not in exhausted and q not in pool)

        count = max(1, round((weight / total_weight) * target_std))
        num_to_pick = min(count, len(pool))
        if num_to_pick > 0:
            picks = random.sample(pool, num_to_pick)
            for q in picks:
                result.append({"query": q, "category": cat})
                
    # Le query costruite sul profilo e quelle apprese restano davanti; soltanto
    # il repertorio generale viene variato fra una sessione e l'altra.
    priority = [item for item in result if item['category'].startswith('🎯')]
    general = [item for item in result if not item['category'].startswith('🎯')]
    random.shuffle(general)
    return (priority + general)[:n]

def expand_niche_queries(category_name):
    """Usa l'AI per generare 10 nuove query di ricerca per una nicchia specifica."""
    prompt = f"Genera 10 query di ricerca per Google Maps (in inglese) per trovare aziende nella categoria '{category_name}'. Rispondi solo con una lista JSON di stringhe."
    try:
        result = generate_json(prompt)
    except Exception as e:
        print(f"[expand_niche_queries] {e}")
        return []
    # DeepSeek può restituire dict invece di list (es. {"queries": [...]}) — sanifichiamo
    if isinstance(result, dict):
        for v in result.values():
            if isinstance(v, list):
                result = v
                break
    if not isinstance(result, list):
        print(f"[expand_niche_queries] output non-list ignorato: {type(result).__name__}")
        return []
    return [q for q in result if isinstance(q, str) and q.strip()]

@app.route('/api/settings/weights', methods=['GET', 'POST'])
def api_weights():
    uid = get_user_id()
    conn = get_db_conn()
    if request.method == 'POST':
        new_weights = request.json
        conn.execute("INSERT INTO user_settings (user_id, weights) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET weights=excluded.weights", 
                     (uid, json.dumps(new_weights)))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    
    row = conn.execute("SELECT weights FROM user_settings WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    if row:
        return jsonify(json.loads(row[0]))
    return jsonify(CATEGORY_WEIGHTS)


@app.route('/api/settings/scout', methods=['GET', 'POST'])
def api_scout_settings():
    """Parametri di scouting configurabili dall'utente."""
    uid = get_user_id()
    conn = get_db_conn()

    defaults = {
        "max_results": 10,
        "min_score": 0,
        "premium_threshold": 7,
        "max_scrape_chars": 5000,
        "max_ai_chars": 4000,
        "min_text_for_analysis": 200,
        "show_all_leads": True,
        "extra_pages": ["contact", "contatti", "chi-siamo", "about", "about-us"],
        "junk_email_patterns": ["noreply", "no-reply", "wordpress", "example.com", "sentry",
                                "wixpress", "test@", "email@", "your@", "name@", "user@",
                                "privacy@", "abuse@", "postmaster@", "webmaster@"]
    }

    if request.method == 'POST':
        new_settings = request.json
        # Merge con defaults per non perdere nulla
        row = conn.execute("SELECT settings FROM user_settings WHERE user_id=?", (uid,)).fetchone()
        current = json.loads(row[0]) if row and row[0] else {}
        current.update(new_settings)
        conn.execute("INSERT INTO user_settings (user_id, settings) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET settings=excluded.settings",
                     (uid, json.dumps(current)))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    row = conn.execute("SELECT settings FROM user_settings WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    saved = json.loads(row[0]) if row and row[0] else {}
    # Merge defaults con saved
    result = {**defaults, **saved}
    return jsonify(result)


@app.route('/api/queries/pool')
def api_queries_pool():
    """Restituisce il pool completo di query con stato (new/used/exhausted) per categoria."""
    uid = get_user_id()
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    history = conn.execute("SELECT query, is_exhausted FROM search_history WHERE user_id=?", (uid,)).fetchall()
    conn.close()
    exhausted = {r['query'] for r in history if r['is_exhausted']}
    used = {r['query'] for r in history if not r['is_exhausted']}

    # Carica query custom dall'utente
    custom_queries = _load_custom_queries(uid)

    pool = {}
    for cat, queries in QUERY_POOLS.items():
        all_q = list(queries)
        # Aggiungi query custom per questa categoria
        for cq in custom_queries.get(cat, []):
            if cq not in all_q:
                all_q.append(cq)
        pool[cat] = [{
            "query": q,
            "status": "exhausted" if q in exhausted else ("used" if q in used else "new"),
            "custom": q not in queries
        } for q in all_q]

    total = sum(len(v) for v in pool.values())
    return jsonify({
        "pool": pool,
        "stats": {
            "total": total,
            "used": len(used),
            "exhausted": len(exhausted),
            "available": total - len(exhausted)
        }
    })


@app.route('/api/queries/stats')
def api_queries_stats():
    """Statistiche sintetiche per categoria (per il tab Settori)."""
    uid = get_user_id()
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    history = conn.execute("SELECT query, is_exhausted FROM search_history WHERE user_id=?", (uid,)).fetchall()
    conn.close()
    exhausted = {r['query'] for r in history if r['is_exhausted']}
    used = {r['query'] for r in history if not r['is_exhausted']}
    custom_queries = _load_custom_queries(uid)

    stats = {}
    for cat, queries in QUERY_POOLS.items():
        all_q = set(queries) | set(custom_queries.get(cat, []))
        cat_exhausted = len([q for q in all_q if q in exhausted])
        cat_used = len([q for q in all_q if q in used])
        stats[cat] = {
            "total": len(all_q),
            "used": cat_used,
            "exhausted": cat_exhausted,
            "available": len(all_q) - cat_exhausted
        }
    return jsonify(stats)


@app.route('/api/queries/custom', methods=['POST'])
def api_add_custom_query():
    """Aggiunge una query personalizzata al pool di una categoria."""
    uid = get_user_id()
    data = request.json
    category = data.get('category', '')
    query = data.get('query', '').strip()
    if not query or category not in QUERY_POOLS:
        return jsonify({"error": "Categoria o query non valida"}), 400

    custom_queries = _load_custom_queries(uid)
    if category not in custom_queries:
        custom_queries[category] = []
    if query not in custom_queries[category] and query not in QUERY_POOLS[category]:
        custom_queries[category].append(query)
        _save_custom_queries(uid, custom_queries)
    return jsonify({"ok": True, "pool_size": len(QUERY_POOLS[category]) + len(custom_queries.get(category, []))})


@app.route('/api/queries/custom', methods=['DELETE'])
def api_delete_custom_query():
    """Rimuove una query personalizzata dal pool."""
    uid = get_user_id()
    data = request.json
    category = data.get('category', '')
    query = data.get('query', '').strip()
    custom_queries = _load_custom_queries(uid)
    if category in custom_queries and query in custom_queries[category]:
        custom_queries[category].remove(query)
        _save_custom_queries(uid, custom_queries)
    return jsonify({"ok": True})


def _load_custom_queries(uid):
    """Carica query personalizzate dal campo settings di user_settings."""
    conn = get_db_conn()
    row = conn.execute("SELECT settings FROM user_settings WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    if row and row[0]:
        try:
            return json.loads(row[0]).get('custom_queries', {})
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _save_custom_queries(uid, custom_queries):
    """Salva query personalizzate nel campo settings di user_settings."""
    conn = get_db_conn()
    row = conn.execute("SELECT settings FROM user_settings WHERE user_id=?", (uid,)).fetchone()
    settings = {}
    if row and row[0]:
        try:
            settings = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            pass
    settings['custom_queries'] = custom_queries
    conn.execute(
        "INSERT INTO user_settings (user_id, weights, settings) VALUES (?, '{}', ?) ON CONFLICT(user_id) DO UPDATE SET settings=excluded.settings",
        (uid, json.dumps(settings))
    )
    conn.commit()
    conn.close()
    mark_dirty()


# ─── UTILS ──────────────────────────────────────────────────────────────────────

VALID_USER_IDS = {'dany', 'alessio'}
CLIENT_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,47}$')
VALID_BUSINESS_TYPES = {'nautical', 'hospitality', 'experience', 'other'}

# Funnel outreach — ogni transizione corrisponde a un timestamp che viene
# popolato automaticamente in update_lead_status.
LEAD_STATUSES = {'saved', 'contacted', 'replied', 'meeting', 'partner', 'rejected'}
LEAD_STATUS_TIMESTAMP = {
    'contacted': 'contacted_at',
    'replied': 'replied_at',
    'meeting': 'meeting_at',
    'partner': 'partner_since',
}
GENERIC_EMAIL_PREFIXES = {
    'info', 'contact', 'contatti', 'hello', 'ciao', 'booking', 'reservation',
    'reservations', 'prenotazioni', 'admin', 'staff', 'office', 'mail',
    'sales', 'commerciale', 'support', 'help', 'assistenza', 'segreteria',
}


def is_generic_email(addr):
    """True se l'email è generica (info@, contact@, ecc.) e quindi difficilmente
    raggiunge un decisore. Usata per marcare i lead in UI e per il conteggio
    dei contatti 'personali' scoperti."""
    if not addr or '@' not in addr:
        return False
    prefix = addr.split('@', 1)[0].lower().strip()
    prefix = prefix.split('+', 1)[0]
    return prefix in GENERIC_EMAIL_PREFIXES


def get_owner_id():
    """
    Recupera l'ID utente con whitelist hard.
    Priorità: sessione HMAC -> header X-User-ID -> query param.
    Fallback su 'dany' solo se NESSUNA fonte ha valore valido (dev mode).
    """
    uid = session.get('user_id') or request.headers.get('X-User-ID') or request.args.get('user_id')
    if uid not in VALID_USER_IDS:
        uid = 'dany'  # fallback dev: log per visibilità
        if request.path.startswith('/api/'):
            print(f"[AUTH] Fallback uid='dany' su {request.path} (header={request.headers.get('X-User-ID')!r})")
    return uid


def get_client_slug():
    """Spazio cliente attivo. `default` mantiene compatibili i dati storici."""
    slug = (request.headers.get('X-Client-ID') or request.args.get('client_id') or 'default').strip().lower()
    return slug if CLIENT_SLUG_RE.fullmatch(slug) else 'default'


def get_user_id():
    """Chiave di isolamento usata dal motore e dalle query esistenti."""
    owner = get_owner_id()
    client_slug = get_client_slug()
    return owner if client_slug == 'default' else f'{owner}::{client_slug}'


def get_active_workspace():
    """Profilo commerciale selezionato, oppure None per i dati storici."""
    slug = get_client_slug()
    if slug == 'default':
        return None
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM client_workspaces WHERE owner_id=? AND slug=? AND archived=0",
        (get_owner_id(), slug),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def active_workspace_required():
    """I test legacy esercitano il tenant storico; nell'app reale serve un cliente."""
    return app.config.get('TESTING', False) or bool(get_active_workspace())


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    uid = request.json.get('user_id')
    if uid in ['dany', 'alessio']:
        session['user_id'] = uid
        return jsonify({"ok": True, "user": uid})
    return jsonify({"error": "Utente non autorizzato"}), 401

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.pop('user_id', None)
    return jsonify({"ok": True})


def _workspace_to_dict(row):
    return {key: row[key] for key in row.keys()}


def _profile_completeness(workspace):
    weighted_fields = {
        'name': 5, 'business_type': 5, 'sector': 10, 'geography': 10,
        'offer_name': 15, 'boat_type': 5, 'target_customer': 15,
        'ideal_partner': 15, 'exclusions': 5, 'primary_goal': 10,
        'differentiators': 5,
    }
    earned = sum(weight for field, weight in weighted_fields.items() if workspace.get(field))
    return min(100, earned)


def _client_playbook(workspace):
    kind = workspace.get('business_type') or 'other'
    geography = workspace.get('geography') or 'Portofino'
    if kind == 'hospitality':
        partners = ['DMC e tour operator', 'wedding planner', 'travel designer', 'aziende e retreat', 'concierge', 'operatori di esperienze']
        focus = 'Prenotazioni qualificate, soggiorni coordinati e canali di vendita ricorrenti.'
    elif kind == 'nautical':
        partners = ['hotel e resort', 'concierge', 'wedding planner', 'DMC', 'travel designer', 'agenzie eventi']
        focus = 'Compatibilità fra dimensione del gruppo, stile dell’esperienza e capacità del partner di proporla.'
    else:
        partners = ['hotel', 'concierge', 'DMC', 'agenzie eventi', 'travel designer', 'operatori complementari']
        focus = 'Sovrapposizione del pubblico, territorio e valore concreto della collaborazione.'
    query_terms = [term.strip() for term in re.split(r'[,;\n]+', workspace.get('ideal_partner') or '') if term.strip()]
    if not query_terms:
        query_terms = partners
    return {
        'partner_categories': partners,
        'recommended_queries': [f'{term} {geography}' for term in query_terms[:6]],
        'scoring_focus': focus,
    }


def _client_next_action(workspace, stats):
    completeness = _profile_completeness(workspace)
    if completeness < 70:
        return {'kind': 'profile', 'label': 'Completa il profilo', 'detail': 'Più contesto rende ricerca e messaggi molto più precisi.'}
    if stats['leads'] == 0:
        return {'kind': 'search', 'label': 'Avvia la prima ricerca', 'detail': 'Leslie ha già preparato le categorie più adatte.'}
    if stats['followups'] > 0:
        return {'kind': 'followup', 'label': f"Gestisci {stats['followups']} follow-up", 'detail': 'Riprendi le conversazioni che aspettano una risposta.'}
    if stats['saved'] > 0:
        return {'kind': 'outreach', 'label': f"Contatta {stats['saved']} opportunità", 'detail': 'Apri i contatti, esegui l’audit e prepara le email.'}
    return {'kind': 'expand', 'label': 'Espandi la ricerca', 'detail': 'Esplora una nuova categoria di partner.'}


@app.route('/api/clients', methods=['GET', 'POST'])
def api_clients():
    owner = get_owner_id()
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    if request.method == 'GET':
        rows = conn.execute(
            "SELECT * FROM client_workspaces WHERE owner_id=? AND archived=0 ORDER BY name COLLATE NOCASE",
            (owner,),
        ).fetchall()
        conn.close()
        return jsonify([_workspace_to_dict(row) for row in rows])

    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name', '')).strip()
    raw_slug = str(payload.get('slug') or name).strip().lower()
    slug = re.sub(r'[^a-z0-9]+', '-', raw_slug).strip('-')[:48]
    if not name or not slug or not CLIENT_SLUG_RE.fullmatch(slug):
        conn.close()
        return jsonify({"error": "Nome cliente non valido"}), 400
    business_type = str(payload.get('business_type', 'other')).strip()
    if business_type not in VALID_BUSINESS_TYPES:
        conn.close()
        return jsonify({"error": "Tipo di attività non valido"}), 400
    try:
        conn.execute(
            '''INSERT INTO client_workspaces
               (owner_id, slug, name, sector, geography, ideal_partner, exclusions,
                offer_name, boat_type, max_guests, experience_style, target_customer,
                business_type, inventory_count, price_positioning, seasonality,
                primary_goal, differentiators, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                owner, slug, name, str(payload.get('sector', '')).strip(),
                str(payload.get('geography', '')).strip(),
                str(payload.get('ideal_partner', '')).strip(),
                str(payload.get('exclusions', '')).strip(),
                str(payload.get('offer_name', '')).strip(),
                str(payload.get('boat_type', '')).strip(),
                max(1, min(500, int(payload['max_guests']))) if str(payload.get('max_guests', '')).isdigit() else None,
                str(payload.get('experience_style', '')).strip(),
                str(payload.get('target_customer', '')).strip(),
                business_type,
                max(1, min(500, int(payload['inventory_count']))) if str(payload.get('inventory_count', '')).isdigit() else 1,
                str(payload.get('price_positioning', '')).strip(),
                str(payload.get('seasonality', '')).strip(),
                str(payload.get('primary_goal', '')).strip(),
                str(payload.get('differentiators', '')).strip(),
                str(payload.get('notes', '')).strip(),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM client_workspaces WHERE owner_id=? AND slug=?", (owner, slug)
        ).fetchone()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Esiste già un cliente con questo nome"}), 409
    conn.close()
    mark_dirty()
    return jsonify(_workspace_to_dict(row)), 201


@app.route('/api/clients/<slug>', methods=['GET', 'PATCH', 'DELETE'])
def api_client_detail(slug):
    owner = get_owner_id()
    if not CLIENT_SLUG_RE.fullmatch(slug):
        return jsonify({"error": "Cliente non valido"}), 400
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM client_workspaces WHERE owner_id=? AND slug=?", (owner, slug)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Cliente non trovato"}), 404
    if request.method == 'GET':
        conn.close()
        return jsonify(_workspace_to_dict(row))
    if request.method == 'DELETE':
        conn.execute(
            "UPDATE client_workspaces SET archived=1, updated_at=CURRENT_TIMESTAMP WHERE owner_id=? AND slug=?",
            (owner, slug),
        )
    else:
        payload = request.get_json(silent=True) or {}
        fields = ('name', 'sector', 'geography', 'ideal_partner', 'exclusions', 'offer_name',
                  'boat_type', 'experience_style', 'target_customer', 'business_type',
                  'price_positioning', 'seasonality', 'primary_goal', 'differentiators', 'notes')
        updates = {field: str(payload[field]).strip() for field in fields if field in payload}
        if 'business_type' in updates and updates['business_type'] not in VALID_BUSINESS_TYPES:
            conn.close()
            return jsonify({"error": "Tipo di attività non valido"}), 400
        if 'max_guests' in payload:
            raw_capacity = str(payload.get('max_guests', '')).strip()
            updates['max_guests'] = max(1, min(500, int(raw_capacity))) if raw_capacity.isdigit() else None
        if 'inventory_count' in payload:
            raw_inventory = str(payload.get('inventory_count', '')).strip()
            updates['inventory_count'] = max(1, min(500, int(raw_inventory))) if raw_inventory.isdigit() else 1
        if 'name' in updates and not updates['name']:
            conn.close()
            return jsonify({"error": "Il nome cliente è obbligatorio"}), 400
        if updates:
            clause = ', '.join(f'{field}=?' for field in updates)
            conn.execute(
                f"UPDATE client_workspaces SET {clause}, updated_at=CURRENT_TIMESTAMP WHERE owner_id=? AND slug=?",
                (*updates.values(), owner, slug),
            )
            # Una bozza generata con il vecchio profilo non deve essere riusata.
            conn.execute("UPDATE leads SET draft_email='' WHERE user_id=?", (f"{owner}::{slug}",))
    conn.commit()
    updated = conn.execute(
        "SELECT * FROM client_workspaces WHERE owner_id=? AND slug=?", (owner, slug)
    ).fetchone()
    conn.close()
    mark_dirty()
    return jsonify({"ok": True} if request.method == 'DELETE' else _workspace_to_dict(updated))


@app.route('/api/clients/overview')
def api_clients_overview():
    owner = get_owner_id()
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM client_workspaces WHERE owner_id=? AND archived=0 ORDER BY name COLLATE NOCASE",
        (owner,),
    ).fetchall()
    result = []
    for row in rows:
        workspace = dict(row)
        scope = f"{owner}::{workspace['slug']}"
        counts = {status: 0 for status in LEAD_STATUSES}
        for stat in conn.execute(
            "SELECT status, COUNT(*) AS count FROM leads WHERE user_id=? GROUP BY status", (scope,)
        ).fetchall():
            counts[stat['status']] = stat['count']
        stats = {
            'leads': sum(counts.values()),
            'premium': conn.execute("SELECT COUNT(*) FROM leads WHERE user_id=? AND is_premium=1", (scope,)).fetchone()[0],
            'saved': counts['saved'],
            'contacted': counts['contacted'] + counts['replied'] + counts['meeting'] + counts['partner'],
            'partners': counts['partner'],
            'followups': conn.execute(
                "SELECT COUNT(*) FROM leads WHERE user_id=? AND status='contacted' AND contacted_at <= datetime('now','-7 days')",
                (scope,),
            ).fetchone()[0],
        }
        workspace['stats'] = stats
        workspace['profile_completeness'] = _profile_completeness(workspace)
        workspace['playbook'] = _client_playbook(workspace)
        workspace['next_action'] = _client_next_action(workspace, stats)
        result.append(workspace)
    conn.close()
    return jsonify(result)


@app.route('/api/opportunities/network')
def api_opportunity_network():
    owner = get_owner_id()
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    clients = [dict(row) for row in conn.execute(
        "SELECT * FROM client_workspaces WHERE owner_id=? AND archived=0 ORDER BY name", (owner,)
    ).fetchall()]
    conn.close()
    opportunities = []
    for index, first in enumerate(clients):
        for second in clients[index + 1:]:
            kinds = {first.get('business_type'), second.get('business_type')}
            if kinds == {'hospitality', 'nautical'}:
                stay = first if first.get('business_type') == 'hospitality' else second
                boat = second if stay is first else first
                score = 92
                kind = 'stay_experience_bundle'
                suggested_offer = 'Soggiorno + esperienza in mare'
                next_action = 'Concorda disponibilità, modalità di referral e commissione.'
                detail = f"Gli ospiti di {stay['name']} possono prenotare {boat.get('offer_name') or 'esperienze in mare'}; {boat['name']} può consigliare le strutture di {stay['name']}."
            elif first.get('business_type') == second.get('business_type') == 'hospitality':
                score = 76
                kind = 'overflow_referral'
                suggested_offer = 'Scambio richieste e overflow'
                next_action = 'Definisci quando passare una richiesta e quali dati condividere.'
                detail = f"{first['name']} e {second['name']} possono scambiarsi richieste quando una struttura è piena o la dimensione del gruppo non è adatta."
            elif first.get('business_type') == second.get('business_type') == 'nautical':
                score = 82
                kind = 'capacity_handoff'
                suggested_offer = 'Instradamento per dimensione gruppo'
                next_action = 'Definisci soglie di capienza e regole di referral.'
                small, large = sorted((first, second), key=lambda c: c.get('max_guests') or 0)
                detail = f"{small['name']} può gestire le richieste più intime; {large['name']} i gruppi più numerosi. Insieme evitano di perdere richieste fuori capienza."
            else:
                continue
            opportunities.append({
                'from': first['slug'], 'from_name': first['name'],
                'to': second['slug'], 'to_name': second['name'],
                'title': f"{first['name']} + {second['name']}",
                'detail': detail,
                'score': score,
                'kind': kind,
                'suggested_offer': suggested_offer,
                'next_action': next_action,
            })
    opportunities.sort(key=lambda item: item['score'], reverse=True)
    return jsonify(opportunities)


# ─── ROUTES: PAGE + DISCOVERY ────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/queries/generate')
def api_generate():
    if not active_workspace_required():
        return jsonify({"error": "client_required", "message": "Seleziona un cliente prima di preparare le ricerche."}), 400
    return jsonify(generate_queries(12))


@app.route('/api/clients/active')
def api_active_client():
    workspace = get_active_workspace()
    return jsonify(workspace or {"slug": "default", "name": "Portafoglio personale"})


@app.route('/api/queries/history')
def api_history():
    uid = get_user_id()
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM search_history WHERE user_id=? ORDER BY last_run DESC", (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


def search_places(query):
    """Esegue una singola ricerca su Google Places (usata anche dal batch)."""
    if not GOOGLE_API_KEY:
        return []

    ensure_quota("places")
    url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={quote_plus(query)}&key={GOOGLE_API_KEY}"
    try:
        places = requests.get(url, timeout=10).json().get('results', [])
        track_api_call("places", "textsearch")
    except Exception as e:
        print(f"[Places] textsearch error: {e}")
        return []

    results = []
    for p in places:
        try:
            det_url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={p['place_id']}&fields=website,formatted_phone_number,url&key={GOOGLE_API_KEY}"
            det = requests.get(det_url, timeout=10).json().get('result', {})
            track_api_call("places", "details")
            website = det.get('website', '')
            if not website or any(s in website for s in ['facebook.com', 'instagram.com', 'twitter.com', 'tiktok.com']):
                continue

            results.append({
                'name': p.get('name', ''),
                'website': website,
                'phone': det.get('formatted_phone_number', ''),
                'address': p.get('formatted_address', ''),
                'maps_url': det.get('url', ''),
                'query_source': query
            })
        except Exception as e:
            print(f"[search_places] details error: {e}")
            continue
    return results


@app.route('/api/search')
def live_search():
    if not active_workspace_required():
        return jsonify({"error": "client_required", "message": "Seleziona un cliente prima di cercare partner."}), 400
    query = request.args.get('q', '')
    if not query:
        return jsonify([])
    if not GOOGLE_API_KEY:
        return jsonify({"error": "GOOGLE_MAPS_API_KEY mancante"}), 500

    # Siti già noti (Isolamento multi-user)
    uid = get_user_id()
    conn = get_db_conn()
    known = {r[0] for r in conn.execute("SELECT website FROM leads WHERE user_id=?", (uid,)).fetchall()}
    conn.close()

    # Google Places
    try:
        ensure_quota("places")
    except UsageLimitExceeded as e:
        return jsonify({"error": "limit_exceeded", "provider": "places", "today": e.today, "limit": e.limit}), 429
    url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={quote_plus(query)}&key={GOOGLE_API_KEY}"
    try:
        places = requests.get(url, timeout=10).json().get('results', [])
        track_api_call("places", "textsearch")
    except Exception as e:
        print(f"Search error: {e}")
        return jsonify([])

    results = []
    new_count = 0
    for p in places:
        det = requests.get(
            f"https://maps.googleapis.com/maps/api/place/details/json"
            f"?place_id={p['place_id']}&fields=website,formatted_phone_number,url&key={GOOGLE_API_KEY}",
            timeout=10
        ).json().get('result', {})
        track_api_call("places", "details")
        website = det.get('website', '')
        if not website or any(s in website for s in ['facebook.com', 'instagram.com', 'twitter.com', 'tiktok.com']):
            continue

        is_known = website in known
        if not is_known:
            new_count += 1
        results.append({
            'name': p.get('name', ''),
            'website': website,
            'phone': det.get('formatted_phone_number', ''),
            'address': p.get('formatted_address', ''),
            'maps_url': det.get('url', ''),
            'is_known': is_known,
            'emails': [],
            'email_status': 'pending'
        })

    # Email enrichment (solo nuovi, max 10 in parallelo)
    def enrich(item):
        if not item['is_known']:
            try:
                item['emails'] = scrape_for_contacts(item['website'])
                item['email_status'] = 'done'
            except Exception as e:
                print(f"[enrich] {item.get('website')}: {e}")
                item['email_status'] = 'error'
        else:
            item['email_status'] = 'skip'

    threads = [threading.Thread(target=enrich, args=(r,)) for r in results[:10]]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=12)

    # Salva nella cronologia
    uid = get_user_id()
    # Una query è 'esaurita' se non produce risultati nuovi (non presenti nel DB leads)
    is_exhausted = 1 if (len(results) > 0 and new_count == 0) else 0
    
    conn = get_db_conn()
    conn.execute('''INSERT INTO search_history (user_id, query, results_count, new_count, is_exhausted, last_run)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, query) DO UPDATE SET
            results_count=excluded.results_count,
            new_count=excluded.new_count,
            is_exhausted=excluded.is_exhausted,
            last_run=excluded.last_run
    ''', (uid, query, len(results), new_count, is_exhausted))
    conn.commit()
    conn.close()
    mark_dirty()

    return jsonify(results)

@app.route('/api/search/batch', methods=['POST'])
def api_search_batch():
    if not active_workspace_required():
        return jsonify({"error": "client_required", "message": "Seleziona un cliente prima di cercare partner."}), 400
    queries = request.json.get('queries', [])
    if not queries:
        return jsonify([])
    
    all_results = []
    seen_websites = set()
    
    # Ottieni lead già salvati
    uid = get_user_id()
    conn = get_db_conn()
    saved = conn.execute("SELECT website FROM leads WHERE user_id=?", (uid,)).fetchall()
    known_sites = {s[0] for s in saved}
    conn.close()

    # Parallelizziamo le ricerche (max 5 query contemporaneamente)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_query = {executor.submit(search_places, q): q for q in queries[:10]}
        for future in concurrent.futures.as_completed(future_to_query):
            try:
                results = future.result()
                for r in results:
                    if r['website'] not in seen_websites:
                        r['is_known'] = r['website'] in known_sites
                        all_results.append(r)
                        seen_websites.add(r['website'])
            except Exception as e:
                print(f"Batch query error: {e}")
                
    # Salva nella cronologia
    conn = get_db_conn()
    batch_q = f"Batch: {', '.join(queries[:3])}..."
    batch_new = sum(1 for r in all_results if not r.get('is_known'))
    batch_exhausted = 1 if (len(all_results) > 0 and batch_new == 0) else 0
    conn.execute('''INSERT INTO search_history (user_id, query, results_count, new_count, is_exhausted, last_run)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, query) DO UPDATE SET
                        results_count=excluded.results_count,
                        new_count=excluded.new_count,
                        is_exhausted=excluded.is_exhausted,
                        last_run=excluded.last_run''',
                 (uid, batch_q, len(all_results), batch_new, batch_exhausted))
    conn.commit()
    conn.close()
    mark_dirty()

    return jsonify(all_results)


@app.route('/api/leads/followups')
def api_leads_followups():
    """Lead in stato 'contacted' senza reply dopo N giorni (default 7).
    Il frontend usa questa lista per popolare il badge follow-up in header
    e la sezione dedicata nella pagina leads."""
    uid = get_user_id()
    days = max(1, min(90, request.args.get('days', 7, type=int)))
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, company_name, website, email, phone, contacted_at, category, selling_angle
        FROM leads
        WHERE user_id=?
          AND status='contacted'
          AND contacted_at IS NOT NULL
          AND replied_at IS NULL
          AND contacted_at <= datetime('now', ?)
        ORDER BY contacted_at ASC
        """,
        (uid, f'-{days} days'),
    ).fetchall()
    conn.close()
    return jsonify({
        "days": days,
        "count": len(rows),
        "items": [dict(r) for r in rows],
    })


@app.route('/api/dashboard/conversion')
def api_dashboard_conversion():
    """Metriche di funnel per CIBO Marine: quanti lead a ogni stato, breakdown
    per categoria e per mese (contacted_at). Serve alla dashboard nella view
    Panoramica per capire se lo scouting sta generando pipeline reale."""
    uid = get_user_id()
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row

    funnel_counts = {s: 0 for s in ['saved', 'contacted', 'replied', 'meeting', 'partner', 'rejected']}
    for r in conn.execute(
        "SELECT status, COUNT(*) as c FROM leads WHERE user_id=? GROUP BY status",
        (uid,),
    ).fetchall():
        if r['status'] in funnel_counts:
            funnel_counts[r['status']] = r['c']

    # Breakdown per categoria — quali nicchie convertono meglio
    by_category = {}
    for r in conn.execute(
        """
        SELECT COALESCE(NULLIF(category, ''), source_query, 'sconosciuta') AS cat,
               status, COUNT(*) AS c
        FROM leads
        WHERE user_id=?
        GROUP BY cat, status
        """,
        (uid,),
    ).fetchall():
        cat = r['cat'] or 'sconosciuta'
        d = by_category.setdefault(cat, {s: 0 for s in funnel_counts})
        if r['status'] in d:
            d[r['status']] = r['c']

    # Serie mensile — quanti contatti e partner per mese negli ultimi 12
    monthly = {}
    for r in conn.execute(
        """
        SELECT strftime('%Y-%m', contacted_at) AS month, COUNT(*) AS c
        FROM leads
        WHERE user_id=? AND contacted_at IS NOT NULL
          AND contacted_at >= date('now', '-12 months')
        GROUP BY month
        """,
        (uid,),
    ).fetchall():
        m = r['month']
        monthly.setdefault(m, {'contacted': 0, 'partner': 0})['contacted'] = r['c']
    for r in conn.execute(
        """
        SELECT strftime('%Y-%m', partner_since) AS month, COUNT(*) AS c
        FROM leads
        WHERE user_id=? AND partner_since IS NOT NULL
          AND partner_since >= date('now', '-12 months')
        GROUP BY month
        """,
        (uid,),
    ).fetchall():
        m = r['month']
        monthly.setdefault(m, {'contacted': 0, 'partner': 0})['partner'] = r['c']
    conn.close()

    total = sum(funnel_counts.values())
    contacted = funnel_counts['contacted'] + funnel_counts['replied'] + funnel_counts['meeting'] + funnel_counts['partner']
    partners = funnel_counts['partner']
    return jsonify({
        "funnel": funnel_counts,
        "totals": {
            "leads": total,
            "contacted_or_beyond": contacted,
            "partners": partners,
            "conversion_pct": round(100 * partners / max(contacted, 1), 1),
        },
        "by_category": by_category,
        "monthly": [
            {"month": m, **v} for m, v in sorted(monthly.items())
        ],
    })


@app.route('/api/analytics/pulse')
def api_pulse():
    uid = get_user_id()
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    # Lead premium trovati negli ultimi 14 giorni
    rows = conn.execute("""
        SELECT date(created_at) as day, COUNT(*) as count 
        FROM leads 
        WHERE user_id=? AND created_at > date('now', '-14 days')
        GROUP BY day 
        ORDER BY day ASC
    """, (uid,)).fetchall()
    conn.close()
    return jsonify([{"day": r['day'], "count": r['count']} for r in rows])


# ─── ROUTES: LEADS ────────────────────────────────────────────────────────────────

@app.route('/api/leads')
def api_leads():
    uid = get_user_id()
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM leads WHERE user_id=? ORDER BY (fit_score IS NULL), fit_score DESC, id DESC",
        (uid,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/intelligence')
def api_intelligence():
    uid = get_user_id()
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM learned_intelligence WHERE user_id=? ORDER BY confidence DESC", (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/leads/save', methods=['POST'])
@db_write
def save_lead():
    if not active_workspace_required():
        return jsonify({"error": "client_required", "message": "Seleziona un cliente prima di salvare un contatto."}), 400
    uid = get_user_id()
    d = request.get_json(silent=True)
    if not isinstance(d, dict) or not (d.get('name') or '').strip():
        return jsonify({"error": "invalid_payload", "message": "Payload JSON con campo 'name' obbligatorio."}), 400
    conn = get_db_conn()
    try:
        email_val = d.get('email') or ''
        # Se save arriva da /api/search il payload ha `emails` (lista); teniamo il primo
        if not email_val and isinstance(d.get('emails'), list) and d['emails']:
            email_val = d['emails'][0]
        first_email = email_val.split(',')[0].strip() if email_val else ''
        generic_flag = 1 if is_generic_email(first_email) else 0
        conn.execute('''INSERT INTO leads (user_id, company_name, website, phone, email, address, source_query, status, is_generic_email, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'saved', ?, ?)
            ON CONFLICT(user_id, website) DO UPDATE SET status='saved', is_generic_email=excluded.is_generic_email
        ''', (uid, str(d.get('name', ''))[:255], d.get('website'), d.get('phone'),
              email_val, d.get('address'), d.get('query'), generic_flag, d.get('category', '')))
        conn.commit()
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"error": "save_failed"}), 400
    finally:
        conn.close()


@app.route('/api/leads/<int:lid>/status', methods=['POST'])
@db_write
def update_lead_status(lid):
    uid = get_user_id()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_payload"}), 400
    new_status = payload.get('status', 'saved')
    if new_status not in LEAD_STATUSES:
        return jsonify({"error": "invalid_status", "allowed": sorted(LEAD_STATUSES)}), 400
    conn = get_db_conn()
    ts_col = LEAD_STATUS_TIMESTAMP.get(new_status)
    if ts_col:
        # Popola il timestamp SOLO se non già settato: la prima transizione
        # è quella che conta per le metriche di funnel. Torni indietro e
        # avanti su 'contacted' → 'replied' non deve resettare contacted_at.
        conn.execute(
            f"UPDATE leads SET status=?, {ts_col}=COALESCE({ts_col}, CURRENT_TIMESTAMP) WHERE id=? AND user_id=?",
            (new_status, lid, uid),
        )
    else:
        conn.execute(
            "UPDATE leads SET status=? WHERE id=? AND user_id=?",
            (new_status, lid, uid),
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "status": new_status})


@app.route('/api/leads/<int:lid>/notes', methods=['POST'])
@db_write
def update_lead_notes(lid):
    uid = get_user_id()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_payload"}), 400
    notes = str(payload.get('notes', ''))[:4000]
    conn = get_db_conn()
    conn.execute("UPDATE leads SET user_notes=? WHERE id=? AND user_id=?", (notes, lid, uid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route('/api/leads/audit/<int:lead_id>', methods=['POST'])
@db_write
def audit_lead(lead_id):
    if not active_workspace_required():
        return jsonify({"error": "client_required", "message": "Seleziona un cliente prima di analizzare un contatto."}), 400
    uid = get_user_id()
    workspace = get_active_workspace() or {}
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    lead = conn.execute("SELECT * FROM leads WHERE id=? AND user_id=?", (lead_id, uid)).fetchone()
    if not lead:
        conn.close()
        return jsonify({"error": "Lead not found"}), 404

    # Rescrape per avere dati freschi
    from scraper import scrape_lead_site
    scrape_data = scrape_lead_site(lead['website'])
    text = scrape_data['content'] if scrape_data and scrape_data.get('content') else lead['company_name']
    seo_meta = scrape_data['metadata'] if scrape_data and scrape_data.get('metadata') else {}

    prompt = f"""
    Analizza '{lead['company_name']}'.
    1. Profilo strategico commerciale.
    2. Descrizione nicchia (max 15 parole).
    3. 3 query simili per Google Maps.
    4. Mini-Audit SEO (Titolo: {seo_meta.get('title')}, H1: {seo_meta.get('h1')}).
    5. Un suggerimento su come approcciare questa azienda per proporre l'offerta specifica del cliente (max 25 parole).
    6. Valuta compatibilità 0-2 per pubblico, territorio, capacità, posizionamento e canale commerciale.
    7. Cita fino a 3 evidenze concrete trovate nel sito e indica confidence alta/media/bassa.

    PROFILO CLIENTE: {json.dumps(workspace, ensure_ascii=False)}

    Rispondi SOLO JSON:
    {{
      "audit": "...profilo strategico...",
      "niche": "...",
      "description": "...",
      "queries": ["..."],
      "seo_insight": "breve commento SEO",
      "selling_angle": "suggerimento approccio commerciale"
      ,"fit_dimensions": {{"audience": 0, "geography": 0, "capacity": 0, "positioning": 0, "channel": 0}}
      ,"fit_reason": "motivazione concreta max 35 parole"
      ,"confidence": "alta|media|bassa"
      ,"evidence": ["prova dal sito"]
    }}
    Testo: {text[:4000]}
    """
    try:
        data = generate_json(prompt)
        audit_text = data.get('audit', '')
        if data.get('seo_insight'):
            audit_text += f"\n\n🔍 SEO INSIGHT: {data['seo_insight']}"

        niche = data.get('niche', 'Generica')
        desc = data.get('description', 'Analisi nicchia strategica completata.')
        queries = data.get('queries', [])
        selling_angle = data.get('selling_angle', '')
        fit_dimensions = data.get('fit_dimensions') if isinstance(data.get('fit_dimensions'), dict) else {}
        fit_score = sum(max(0, min(2, int(fit_dimensions.get(key, 0) or 0))) for key in ('audience', 'geography', 'capacity', 'positioning', 'channel')) if fit_dimensions else max(0, min(10, int(data.get('score', 0) or 0)))
        fit_reason = str(data.get('fit_reason') or data.get('reason') or '').strip()
        fit_confidence = data.get('confidence') if data.get('confidence') in {'alta', 'media', 'bassa'} else 'bassa'
        fit_evidence = data.get('evidence') if isinstance(data.get('evidence'), list) else []
        saturation = random.randint(10, 45)

        conn.execute('''INSERT INTO learned_intelligence (user_id, niche_key, description, market_saturation, suggested_queries)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(user_id, niche_key) DO UPDATE SET
                            confidence = confidence + 1,
                            description = excluded.description
        ''', (uid, niche, desc, saturation, json.dumps(queries)))

        people = scrape_data.get('people') if scrape_data else []
        conn.execute(
            """UPDATE leads SET ai_audit=?, seo_data=?, selling_angle=?, contacts_json=?,
               fit_score=?, fit_reason=?, fit_dimensions_json=?, fit_confidence=?,
               fit_evidence_json=?, analyzed_at=CURRENT_TIMESTAMP, analysis_version='fit-v2'
               WHERE id=? AND user_id=?""",
            (audit_text, json.dumps(seo_meta), selling_angle, json.dumps(people or []),
             fit_score, fit_reason, json.dumps(fit_dimensions), fit_confidence,
             json.dumps(fit_evidence), lead_id, uid),
        )

        conn.commit()
        conn.close()
        mark_dirty()
        return jsonify({"audit": audit_text, "seo": seo_meta, "selling_angle": selling_angle,
                        "fit_score": fit_score, "fit_reason": fit_reason,
                        "fit_dimensions": fit_dimensions, "confidence": fit_confidence,
                        "evidence": fit_evidence})
    except Exception as e:
        if 'conn' in locals(): conn.close()
        return jsonify({"error": str(e)}), 500


@app.route('/api/leads/<int:lid>/draft_email', methods=['POST'])
@db_write
def draft_lead_email(lid):
    """Genera (o rigenera) una mail B2B di primo contatto da inviare al lead.
    Prompt calibrato su tono CIBO Marine (charter yacht di lusso a Portofino).
    Il risultato viene persistito in `draft_email` così le rigenerazioni non
    sono forzate a ogni apertura del lead."""
    uid = get_user_id()
    workspace = get_active_workspace() or {}
    if not workspace and not app.config.get('TESTING', False):
        return jsonify({"error": "client_required", "message": "Seleziona un cliente prima di preparare un’email."}), 400
    force = bool(request.json.get('force')) if request.is_json else False
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    lead = conn.execute(
        "SELECT * FROM leads WHERE id=? AND user_id=?", (lid, uid)
    ).fetchone()
    if not lead:
        conn.close()
        return jsonify({"error": "Lead not found"}), 404

    if lead['draft_email'] and not force:
        conn.close()
        return jsonify({"email": lead['draft_email'], "cached": True})

    company = lead['company_name'] or 'il vostro team'
    angle = lead['selling_angle'] or ''
    category = lead['category'] or ''
    website = lead['website'] or ''
    seo = {}
    try:
        seo = json.loads(lead['seo_data'] or '{}')
    except (json.JSONDecodeError, TypeError):
        pass
    site_title = seo.get('title') or ''

    operator_name = workspace.get('name') or 'Attività turistica del territorio'
    offer = workspace.get('offer_name') or workspace.get('sector') or 'esperienze nel territorio di Portofino'
    asset = workspace.get('boat_type') or 'servizio'
    capacity = workspace.get('max_guests') or 'un numero selezionato di'
    prompt = f"""
Sei l'assistente commerciale di {operator_name}.
Scrivi una PRIMA email B2B in italiano rivolta a '{company}'.
Stai proponendo una partnership per offrire ai loro clienti: {offer}.
L’offerta si basa su: {asset}. Quantità disponibile: {workspace.get('inventory_count') or 1}. Capienza massima: {capacity} ospiti.
Stile: {workspace.get('experience_style', '')}. Pubblico ideale: {workspace.get('target_customer', '')}.
Elementi distintivi: {workspace.get('differentiators', '')}.

Regole:
- Tono professionale, elegante, mai commerciale-aggressivo.
- Massimo 140 parole totali, corpo email tra 4 e 7 righe.
- Personalizza il primo capoverso citando ciò che sai dell'azienda (categoria "{category or 'partner premium'}", angolo "{angle or 'servizi high-end'}", titolo sito "{site_title[:120]}").
- Chiudi con una call-to-action leggera: 15 minuti di call per esplorare la collaborazione.
- Firma: "{operator_name} — Portofino".

Rispondi SOLO in JSON con questa forma esatta:
{{
  "subject": "oggetto max 8 parole",
  "body": "testo completo dell'email con paragrafi separati da \\n\\n"
}}
""".strip()

    try:
        data = generate_json(prompt)
    except UsageLimitExceeded:
        conn.close()
        raise
    except Exception as e:
        conn.close()
        return jsonify({"error": f"AI error: {e}"}), 500

    subject = str(data.get('subject', '') or '').strip()
    body = str(data.get('body', '') or '').strip()
    if not subject or not body:
        conn.close()
        return jsonify({"error": "AI non ha restituito subject/body validi", "raw": data}), 502

    email_str = f"Oggetto: {subject}\n\n{body}"
    conn.execute(
        "UPDATE leads SET draft_email=? WHERE id=? AND user_id=?",
        (email_str, lid, uid),
    )
    conn.commit()
    conn.close()
    return jsonify({"email": email_str, "subject": subject, "body": body, "cached": False})


@app.route('/api/leads/<int:lid>', methods=['DELETE'])
@db_write
def delete_lead(lid):
    uid = get_user_id()
    conn = get_db_conn()
    conn.execute("DELETE FROM leads WHERE id=? AND user_id=?", (lid, uid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route('/api/export')
def export_csv():
    uid = get_user_id()
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM leads WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    conn.close()
    
    def sanitize(val):
        """Previene CSV Injection (Formula Injection)."""
        if isinstance(val, str) and val.startswith(('=', '+', '-', '@')):
            return f"'{val}"
        return val

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Nome", "Sito Web", "Email", "Telefono", "Indirizzo", "Ricerca origine", "Compatibilità", "Motivazione", "Stato", "Analisi AI", "Note personali", "Data"])
    for r in rows:
        writer.writerow([
            sanitize(r['company_name']), 
            sanitize(r['website']), 
            sanitize(r['email']), 
            sanitize(r['phone']),
            sanitize(r['address']), 
            sanitize(r['source_query']), 
            r['fit_score'] if r['fit_score'] is not None else '',
            sanitize(r['fit_reason']),
            sanitize(r['status']), 
            sanitize(r['ai_audit']),
            sanitize(r['user_notes']),
            r['created_at']
        ])
    output.seek(0)
    return output.getvalue(), 200, {
        'Content-Type': 'text/csv; charset=utf-8',
        'Content-Disposition': f'attachment; filename=leslie_{get_client_slug()}_opportunita.csv'
    }


# ─── ROUTES: DISCOVERY FEED + SCOUT ──────────────────────────────────────────────

@app.route('/api/discovery/feed')
def api_discovery_feed():
    uid = get_user_id()
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    # Prendi i 12 lead più recenti che hanno un'analisi AI
    rows = conn.execute("""
        SELECT * FROM leads 
        WHERE user_id=? AND (COALESCE(ai_audit,'') != '' OR seo_data IS NOT NULL)
        ORDER BY created_at DESC LIMIT 12
    """, (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/scout/auto', methods=['POST'])
@db_write
def api_scout_auto():
    if not active_workspace_required():
        return jsonify({"error": "client_required", "message": "Seleziona un cliente prima di avviare la ricerca."}), 400
    uid = get_user_id()
    ok, retry_after = check_scout_rate_limit(uid)
    if not ok:
        return jsonify({
            "error": "rate_limited",
            "message": f"Aspetta {retry_after}s prima del prossimo scout (protezione costi).",
            "retry_after": retry_after,
        }), 429
    result = autonomous_scout.run_scout_cycle(uid)
    return jsonify({"ok": True, "result": result, "message": "Scouting completato."})


@app.route('/api/scout/stream')
def api_scout_stream():
    if not active_workspace_required():
        return jsonify({"error": "client_required", "message": "Seleziona un cliente prima di avviare la ricerca."}), 400
    uid = get_user_id()
    ok, retry_after = check_scout_rate_limit(uid)
    if not ok:
        return jsonify({
            "error": "rate_limited",
            "message": f"Aspetta {retry_after}s prima del prossimo scout (protezione costi).",
            "retry_after": retry_after,
        }), 429
    max_results = request.args.get('max_results', 10, type=int)
    min_score = request.args.get('min_score', 0, type=int)
    max_results = max(1, min(20, max_results))
    min_score = max(0, min(10, min_score))

    def generate():
        gen = autonomous_scout.stream_scout_cycle(uid, max_results=max_results, min_score=min_score)
        for update in gen:
            yield f"data: {json.dumps(update)}\n\n"
        mark_dirty()

    return Response(generate(), mimetype='text/event-stream')


# ─── STARTUP ─────────────────────────────────────────────────────────────────────

def safe_startup():
    print("[*] Inizio sequenza di startup...")
    try:
        gcs_download()
    except Exception as e:
        print(f"[ERROR] gcs_download fallito: {e}")
        
    try:
        init_db()
        print("[*] Database inizializzato correttamente.")
    except Exception as e:
        print(f"[ERROR] init_db fallito: {e}")

    try:
        _start_gcs_worker()
        print("[*] GCS upload worker avviato.")
    except Exception as e:
        print(f"[ERROR] avvio worker GCS fallito: {e}")

# Esegui inizializzazione all'import
safe_startup()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f"[*] Avvio server su porta {port}...")
    # Disabilita debug in produzione per evitare info leakage
    is_debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host='0.0.0.0', port=port, debug=is_debug)
