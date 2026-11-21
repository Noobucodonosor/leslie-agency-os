"""Structured JSON logging per Cloud Run.

Cloud Run cattura stdout riga per riga e lo indicizza. Se ogni riga è un
JSON con `severity`, `message`, e i campi utili (uid, provider, cost_usd,
lead_id, ecc.), Cloud Logging popola i filtri e le dashboard automaticamente.

Uso tipico:
    from logging_setup import get_logger
    log = get_logger(__name__)
    log.info("scout_start", extra={"uid": uid, "query": q})
    log.warning("quota_close", extra={"provider": "places", "pct": 82.4})
    log.error("upload_failed", extra={"attempt": 3, "error": str(e)})

Le chiavi in `extra` vengono serializzate insieme al messaggio nel JSON.
"""
import json
import logging
import os
import sys
import time


_RESERVED = {
    'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename',
    'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName',
    'created', 'msecs', 'relativeCreated', 'thread', 'threadName',
    'processName', 'process', 'message', 'asctime', 'taskName',
}


class JsonFormatter(logging.Formatter):
    """Formatter minimalista: emette un dict JSON per riga con severity
    Cloud-Run friendly (INFO/WARNING/ERROR/CRITICAL). I campi passati via
    `extra` diventano top-level nel JSON."""

    def format(self, record):
        payload = {
            "severity": record.levelname,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in _RESERVED or k.startswith("_"):
                continue
            try:
                json.dumps(v)  # solo se serializzabile
                payload[k] = v
            except TypeError:
                payload[k] = repr(v)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_configured = False


def configure_root(level=None):
    """Configura il root logger una sola volta. Idempotente.
    Il livello si legge da LESLIE_LOG_LEVEL (default INFO)."""
    global _configured
    if _configured:
        return
    lvl = level or os.environ.get("LESLIE_LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, lvl, logging.INFO))
    _configured = True


def get_logger(name):
    """Wrapper per configurare-e-restituire un logger."""
    configure_root()
    return logging.getLogger(name)
