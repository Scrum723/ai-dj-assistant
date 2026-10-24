"""Alpha ops helpers: JSON logs, IP rate limits, sqlite backup, adapter-down watch."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# IP token bucket: ~30 / 60s so bursty room use still works; auth is tighter via path.
_IP_CAPACITY = 30.0
_IP_REFILL_PER_SEC = 30.0 / 60.0
_AUTH_CAPACITY = 20.0
_AUTH_REFILL_PER_SEC = 20.0 / 60.0

_lock = threading.Lock()
_buckets: dict[str, list[float]] = {}  # key -> [tokens, last_ts]
_adapter_down_since: dict[str, float] = {}
_adapter_alerted: set[str] = set()
ADAPTER_DOWN_ALERT_SEC = 120.0


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", None)
        if rid:
            payload["request_id"] = rid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_json_logging() -> None:
    """Replace root handlers with JSON lines when LOG_FORMAT=json (default on Railway)."""
    want = (os.getenv("LOG_FORMAT") or "").strip().lower()
    if not want:
        if os.getenv("RAILWAY_ENVIRONMENT"):
            want = "json"
        else:
            return
    if want not in {"json", "1", "true"}:
        return
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def init_sentry() -> None:
    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(dsn=dsn, traces_sample_rate=0.0)
        logger.info("Sentry initialized")
    except Exception:
        logger.exception("Sentry init failed (optional)")


def trusted_proxy() -> bool:
    """X-Forwarded-For is honored only behind a known proxy (Railway or TRUSTED_PROXY)."""
    if os.getenv("RAILWAY_ENVIRONMENT"):
        return True
    return os.getenv("TRUSTED_PROXY", "").lower() in {"1", "true", "yes"}


def cookie_secure() -> bool:
    if os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}:
        return True
    return bool(os.getenv("RAILWAY_ENVIRONMENT"))


def cors_origins() -> list[str]:
    raw = (os.getenv("CORS_ORIGINS") or "").strip()
    defaults = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "https://djbotbotty.com",
        "https://www.djbotbotty.com",
        "https://halo-dj-production.up.railway.app",
    ]
    if not raw:
        return defaults
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or defaults


def data_dir() -> Path:
    raw = os.getenv("DATA_DIR")
    if raw:
        p = Path(raw)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(__file__).resolve().parent


def _take(key: str, capacity: float, refill: float) -> bool:
    now = time.monotonic()
    with _lock:
        tokens, last = _buckets.get(key, [capacity, now])
        tokens = min(capacity, tokens + max(0.0, now - last) * refill)
        if tokens < 1.0:
            _buckets[key] = [tokens, now]
            return False
        _buckets[key] = [tokens - 1.0, now]
        return True


def allow_ip(ip: str, *, path: str) -> bool:
    """Return False when the IP should be rate-limited for this path class."""
    ip = (ip or "unknown").strip() or "unknown"
    authish = path.startswith("/auth")
    cap = _AUTH_CAPACITY if authish else _IP_CAPACITY
    refill = _AUTH_REFILL_PER_SEC if authish else _IP_REFILL_PER_SEC
    kind = "auth" if authish else "pub"
    return _take(f"{kind}:{ip}", cap, refill)


def reset_ip_limits() -> None:
    with _lock:
        _buckets.clear()


def sqlite_paths() -> list[Path]:
    base = data_dir()
    names = (
        "halo.db",
        "community.db",
        "users.db",
        "identity.db",
        "library.db",
        "halo_training.jsonl",
        "dj_actions.jsonl",
        "sentinel_incidents.jsonl",
    )
    return [base / n for n in names]


def maybe_daily_backup() -> Optional[Path]:
    """Copy sqlite/jsonl under DATA_DIR/backups/YYYY-MM-DD once per UTC day."""
    base = data_dir()
    day = time.strftime("%Y-%m-%d", time.gmtime())
    dest = base / "backups" / day
    stamp = dest / ".done"
    if stamp.exists():
        return None
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in sqlite_paths():
        if not src.is_file():
            continue
        try:
            shutil.copy2(src, dest / src.name)
            copied += 1
        except Exception:
            logger.exception("backup failed for %s", src)
    try:
        stamp.write_text(f"copied={copied}\n", encoding="utf-8")
    except Exception:
        logger.exception("backup stamp write failed")
    logger.info("daily sqlite backup dest=%s copied=%s", dest, copied)
    return dest


def check_adapter_down_alerts(
    adapters: dict[str, Any],
    *,
    on_alert: Optional[Callable[[dict[str, Any]], None]] = None,
    now: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Alert when an enabled adapter stays disconnected/dead > 2 minutes."""
    t = time.time() if now is None else float(now)
    fired: list[dict[str, Any]] = []
    for name, st in (adapters or {}).items():
        if not isinstance(st, dict):
            continue
        if "enabled" in st and not st.get("enabled"):
            _adapter_down_since.pop(name, None)
            _adapter_alerted.discard(name)
            continue
        health = str(st.get("health") or "")
        connected = bool(st.get("connected"))
        running = bool(st.get("running"))
        down = running and (not connected or health in {"dead", "degraded"})
        if not down:
            _adapter_down_since.pop(name, None)
            _adapter_alerted.discard(name)
            continue
        since = _adapter_down_since.setdefault(name, t)
        if t - since < ADAPTER_DOWN_ALERT_SEC:
            continue
        if name in _adapter_alerted:
            continue
        _adapter_alerted.add(name)
        payload = {
            "type": "operator_alert",
            "adapter": name,
            "reason": "adapter_down",
            "message": f"Adapter '{name}' down >{int(ADAPTER_DOWN_ALERT_SEC)}s (health={health})",
            "ts": t,
        }
        fired.append(payload)
        if on_alert:
            try:
                on_alert(payload)
            except Exception:
                logger.exception("adapter down alert failed for %s", name)
    return fired


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]
