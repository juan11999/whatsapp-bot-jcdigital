"""Send WhatsApp text via the Meta Cloud API, or log it in DRY_RUN."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from config import (
    DRY_RUN,
    META_ACCESS_TOKEN,
    META_GRAPH_VERSION,
    META_PHONE_NUMBER_ID,
    OUTBOUND_LOG,
)

log = logging.getLogger("whatsapp-bot.meta")


def _append_log(record: dict[str, Any]) -> None:
    OUTBOUND_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.time(), **record}
    with OUTBOUND_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _send_url() -> str:
    version = META_GRAPH_VERSION.strip().lstrip("/") or "v21.0"
    phone_id = META_PHONE_NUMBER_ID
    return f"https://graph.facebook.com/{version}/{phone_id}/messages"


async def send_text(destination: str, text: str, *, dry_run: bool | None = None) -> dict[str, Any]:
    """POST a Cloud API text message. Never raises to the webhook caller."""
    dest = "".join(ch for ch in str(destination) if ch.isdigit())
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": dest,
        "type": "text",
        "text": {"body": text, "preview_url": False},
    }
    use_dry = DRY_RUN if dry_run is None else dry_run
    if use_dry or not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID:
        if use_dry:
            reason = "DRY_RUN"
        elif not META_ACCESS_TOKEN:
            reason = "missing META_ACCESS_TOKEN"
        else:
            reason = "missing META_PHONE_NUMBER_ID"
        record = {
            "ok": True,
            "dry_run": True,
            "provider": "meta",
            "reason": reason,
            "destination": dest,
            "text": text,
        }
        _append_log(record)
        log.info("DRY_RUN Meta send to %s (%s): %s", dest, reason, text.replace("\n", " / "))
        return record

    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(_send_url(), json=payload, headers=headers)
        body: Any
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}
        record = {
            "ok": 200 <= resp.status_code < 300,
            "dry_run": False,
            "provider": "meta",
            "destination": dest,
            "status_code": resp.status_code,
            "response": body,
            "text": text,
        }
        _append_log(record)
        if not record["ok"]:
            log.warning("Meta send failed %s: %s", resp.status_code, body)
        else:
            log.info("Meta submitted to %s", dest)
        return record
    except Exception as exc:
        record = {
            "ok": False,
            "dry_run": False,
            "provider": "meta",
            "destination": dest,
            "error": f"{type(exc).__name__}: {exc}",
            "text": text,
        }
        _append_log(record)
        log.exception("Meta send error")
        return record
