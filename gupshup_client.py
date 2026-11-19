"""Send WhatsApp text via Gupshup session API, or log it in DRY_RUN."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from config import (
    DRY_RUN,
    GUPSHUP_API_KEY,
    GUPSHUP_APP_NAME,
    GUPSHUP_SOURCE,
    OUTBOUND_LOG,
)

log = logging.getLogger("whatsapp-bot.send")

SEND_URL = "https://api.gupshup.io/wa/api/v1/msg"


def _append_log(record: dict[str, Any]) -> None:
    OUTBOUND_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.time(), **record}
    with OUTBOUND_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


async def send_text(destination: str, text: str, *, dry_run: bool | None = None) -> dict[str, Any]:
    """POST a session text message. Never raises to the webhook caller."""
    dest = "".join(ch for ch in str(destination) if ch.isdigit())
    payload = {
        "channel": "whatsapp",
        "source": GUPSHUP_SOURCE,
        "destination": dest,
        "src.name": GUPSHUP_APP_NAME,
        "message": json.dumps({"type": "text", "text": text}, ensure_ascii=False),
    }
    use_dry = DRY_RUN if dry_run is None else dry_run
    if use_dry or not GUPSHUP_API_KEY:
        reason = "DRY_RUN" if use_dry else "missing GUPSHUP_API_KEY"
        record = {
            "ok": True,
            "dry_run": True,
            "reason": reason,
            "destination": dest,
            "text": text,
        }
        _append_log(record)
        log.info("DRY_RUN send to %s (%s): %s", dest, reason, text.replace("\n", " / "))
        return record

    headers = {
        "apikey": GUPSHUP_API_KEY,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(SEND_URL, data=payload, headers=headers)
        body: Any
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}
        record = {
            "ok": 200 <= resp.status_code < 300,
            "dry_run": False,
            "destination": dest,
            "status_code": resp.status_code,
            "response": body,
            "text": text,
        }
        _append_log(record)
        if not record["ok"]:
            log.warning("Gupshup send failed %s: %s", resp.status_code, body)
        else:
            log.info("Gupshup submitted to %s: %s", dest, body)
        return record
    except Exception as exc:
        record = {
            "ok": False,
            "dry_run": False,
            "destination": dest,
            "error": f"{type(exc).__name__}: {exc}",
            "text": text,
        }
        _append_log(record)
        log.exception("Gupshup send error")
        return record
