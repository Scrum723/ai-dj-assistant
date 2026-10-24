"""Supporter thank-you templates and burst grouping helpers."""

from __future__ import annotations

from typing import Any, Optional

BURST_WINDOW_SEC = 10.0  # max gap between gifts to stay in one burst
BURST_QUIET_SEC = 1.5  # debounce after last gift before thank-you (keeps ≤5s SLA)
LLM_THANKS_TIMEOUT_SEC = 3.0

DEFAULT_THANKS = {
    "youtube": "Thank you {name} on YouTube{amount_bit} — the booth feels that!",
    "twitch": "Huge thanks {name} from Twitch{amount_bit} — appreciate the support!",
    "kick": "Thanks {name}!",
    "tiktok": "Thanks {name}!",
    "default": "Thank you {name} for the support!",
}


def format_amount_bit(
    support: Optional[dict[str, Any]],
    *,
    say_amounts: bool,
) -> str:
    if not say_amounts or not support:
        return ""
    amount = support.get("amount")
    currency = support.get("currency") or ""
    if amount is None:
        return ""
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        return ""
    cur = str(currency).strip().lower()
    if cur in {"bits", "diamonds"}:
        label = f"{int(amt)} {cur}"
    elif cur:
        label = f"{amt:g} {currency}"
    else:
        label = f"{amt:g}"
    return f" for {label}"


def thank_template(
    platform: str,
    name: str,
    support: Optional[dict[str, Any]] = None,
    *,
    settings_thanks: Optional[dict[str, Any]] = None,
    say_amounts: bool = False,
    count: Optional[int] = None,
) -> str:
    thanks = dict(DEFAULT_THANKS)
    if settings_thanks:
        thanks.update({str(k): str(v) for k, v in settings_thanks.items() if v})
    plat = (platform or "").strip().lower() or "default"
    tmpl = thanks.get(plat) or thanks.get("default") or DEFAULT_THANKS["default"]
    amount_bit = format_amount_bit(support, say_amounts=say_amounts)
    n = count
    if n is None and support:
        try:
            n = int(support.get("count") or 1)
        except (TypeError, ValueError):
            n = 1
    n = max(1, int(n or 1))
    line = tmpl.format(name=name, amount_bit=amount_bit, count=n)
    if n > 1 and "x{count}" not in tmpl and "{count}" not in tmpl:
        kind = (support or {}).get("kind") if support else None
        # Prefer a compact group suffix for gift bursts
        if plat in {"kick", "tiktok"}:
            line = f"Thanks {name} for the {n} gifts!"
        else:
            line = f"{line.rstrip('!')} — {n} in a row!"
    return line.strip()


def merge_support(
    base: Optional[dict[str, Any]],
    incoming: Optional[dict[str, Any]],
) -> dict[str, Any]:
    out = dict(base or {})
    inc = dict(incoming or {})
    try:
        base_count = int(out.get("count") or 0)
    except (TypeError, ValueError):
        base_count = 0
    try:
        inc_count = int(inc.get("count") or 1)
    except (TypeError, ValueError):
        inc_count = 1
    out["count"] = max(1, base_count) + max(1, inc_count) if base_count else max(1, inc_count)
    if out.get("amount") is None and inc.get("amount") is not None:
        out["amount"] = inc.get("amount")
        out["currency"] = inc.get("currency")
    elif out.get("amount") is not None and inc.get("amount") is not None:
        if (out.get("currency") or "") == (inc.get("currency") or ""):
            try:
                out["amount"] = float(out["amount"]) + float(inc["amount"])
            except (TypeError, ValueError):
                pass
    if not out.get("tier") and inc.get("tier"):
        out["tier"] = inc.get("tier")
    if not out.get("currency") and inc.get("currency"):
        out["currency"] = inc.get("currency")
    return out
