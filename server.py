#!/usr/bin/env python3
"""WhatsApp auto-reply webhook for JC Digital Geneve (Gupshup + Meta v3)."""

from __future__ import annotations

import hmac
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

# Make the project root importable when launched as `uvicorn server:app`.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    BUSINESS_NAME,
    COEXISTENCE_LOG,
    DRY_RUN,
    GUPSHUP_APP_NAME,
    GUPSHUP_SOURCE,
    HOST,
    PORT,
    WEBHOOK_LOG,
    WEBHOOK_TOKEN,
    WEBHOOK_VERIFY_TOKEN,
)
from gupshup_client import send_text  # noqa: E402
from memory import (  # noqa: E402
    already_processed,
    append_message,
    init_db,
    is_handoff_muted,
    mark_processed,
    set_name,
)
from parser import InboundItem, parse_webhook  # noqa: E402
from responder import generate_reply, llm_status  # noqa: E402

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("whatsapp-bot")

app = FastAPI(title=f"{BUSINESS_NAME} WhatsApp bot", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    status = llm_status()
    log.info(
        "Bot ready | app=%s source=%s dry_run=%s llm=%s",
        GUPSHUP_APP_NAME,
        GUPSHUP_SOURCE,
        DRY_RUN,
        status,
    )


def _write_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _log_item(raw: dict[str, Any] | None, item: InboundItem) -> None:
    record = {
        "ts": time.time(),
        "kind": item.kind,
        "field": item.field,
        "message_id": item.message_id,
        "sender": item.sender,
        "message_type": item.message_type,
        "should_reply": item.should_reply,
        "reason": item.reason,
        "text": item.text,
        "extra": item.extra,
    }
    _write_jsonl(WEBHOOK_LOG, record)
    if item.kind in {"echo", "history", "state_sync", "coexistence"} or item.field.startswith("smb_"):
        _write_jsonl(COEXISTENCE_LOG, record)


async def process_item(item: InboundItem, sync_result: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": item.kind,
        "field": item.field,
        "sender": item.sender,
        "message_id": item.message_id,
        "replied": False,
        "silent": False,
        "reason": item.reason,
    }
    if not item.should_reply:
        log.info("Skip reply (%s / %s): %s", item.kind, item.field or "-", item.reason)
        if sync_result is not None:
            sync_result.update(result)
        return result

    if not item.sender:
        log.warning("Inbound message without sender; skip")
        result["reason"] = "missing_sender"
        if sync_result is not None:
            sync_result.update(result)
        return result

    if item.message_id and already_processed(item.message_id):
        result["reason"] = "duplicate"
        result["silent"] = True
        if sync_result is not None:
            sync_result.update(result)
        return result

    if item.message_id:
        mark_processed(item.message_id, item.sender)

    if item.sender_name:
        set_name(item.sender, item.sender_name)

    # Already handed off (e.g. user said "humano" again): stay silent.
    # First-time handoff intent is still answered once inside generate_reply.
    muted = is_handoff_muted(item.sender)
    incoming_text = item.text or ""
    from responder import is_handoff_intent

    will_handoff, _ = is_handoff_intent(incoming_text) if incoming_text else (False, "")
    if muted and not will_handoff:
        log.info("Handoff mute active for %s — no auto-reply", item.sender)
        result["silent"] = True
        result["reason"] = "handoff_mute"
        if item.text:
            append_message(item.sender, "user", item.text)
        if sync_result is not None:
            sync_result.update(result)
        return result

    if item.text:
        # Language is filled after detection; store the user turn first.
        append_message(item.sender, "user", item.text)

    reply = await generate_reply(
        item.sender,
        item.text,
        message_type=item.message_type or "text",
    )
    result["lang"] = reply.get("lang")
    result["source"] = reply.get("source")
    result["handoff"] = reply.get("handoff")
    result["handoff_count"] = reply.get("handoff_count")

    if reply.get("silent") or not reply.get("text"):
        result["silent"] = True
        result["reason"] = reply.get("reason") or "silent"
        log.info("Silent turn for %s (%s)", item.sender, result["reason"])
        if sync_result is not None:
            sync_result.update(result)
        return result

    append_message(item.sender, "assistant", reply["text"], lang=reply.get("lang"))
    send_result = await send_text(item.sender, reply["text"])
    result["replied"] = True
    result["reply"] = reply["text"]
    result["send"] = {k: send_result.get(k) for k in ("ok", "dry_run", "reason", "status_code", "response")}
    if sync_result is not None:
        sync_result.update(result)
    return result


async def _parse_body(request: Request) -> Any:
    content_type = (request.headers.get("content-type") or "").lower()
    raw = await request.body()
    if not raw:
        # Some platforms send form fields
        form = await request.form()
        if form:
            return {k: v for k, v in form.items()}
        return {}
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            data = {k: v for k, v in form.items()}
            # Gupshup sometimes puts JSON in a 'payload' or 'data' field
            for key in ("payload", "data", "body"):
                if isinstance(data.get(key), str):
                    try:
                        data[key] = json.loads(data[key])
                    except json.JSONDecodeError:
                        pass
            return data
        except Exception:
            pass
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"_raw": raw.decode("utf-8", errors="replace")}




def _webhook_token_ok(request: Request) -> bool:
    """If WEBHOOK_TOKEN is set, require a matching ?token= query param."""
    expected = WEBHOOK_TOKEN
    if not expected:
        return True
    provided = request.query_params.get("token") or ""
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))

@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "app": GUPSHUP_APP_NAME,
        "business": BUSINESS_NAME,
        "source": GUPSHUP_SOURCE,
        "dry_run": DRY_RUN,
        "llm": llm_status(),
    }


@app.get("/webhook")
async def webhook_verify(request: Request) -> Response:
    """Meta-style verification (hub.mode / hub.challenge / hub.verify_token)."""
    params = request.query_params
    mode = params.get("hub.mode") or params.get("mode")
    token = params.get("hub.verify_token") or params.get("verify_token")
    challenge = params.get("hub.challenge") or params.get("challenge")
    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN and challenge is not None:
        return PlainTextResponse(challenge)
    # Friendly GET so a pasted callback URL is not a 405
    return JSONResponse(
        {
            "ok": True,
            "service": f"{BUSINESS_NAME} WhatsApp webhook",
            "hint": "POST inbound events here. For Meta verification, send hub.mode=subscribe.",
        }
    )


@app.post("/")
@app.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks) -> JSONResponse:
    """Accept Gupshup v2 and Meta v3 payloads. Always return 200 quickly."""
    if not _webhook_token_ok(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    t0 = time.time()
    try:
        body = await _parse_body(request)
    except Exception:
        log.exception("Failed to read webhook body")
        body = {}

    try:
        items = parse_webhook(body)
    except Exception:
        log.exception("Failed to parse webhook")
        items = []

    _write_jsonl(
        WEBHOOK_LOG,
        {
            "ts": time.time(),
            "event": "received",
            "path": str(request.url.path),
            "item_count": len(items),
            "kinds": [i.kind for i in items],
            "fields": [i.field for i in items],
        },
    )

    sync = request.query_params.get("sync") in {"1", "true", "yes"} or request.headers.get(
        "x-sync-reply", ""
    ).lower() in {"1", "true", "yes"}

    results: list[dict[str, Any]] = []
    for item in items:
        try:
            _log_item(body if isinstance(body, dict) else None, item)
        except Exception:
            log.exception("Failed to log item")
        if sync:
            holder: dict[str, Any] = {}
            try:
                await process_item(item, holder)
            except Exception:
                log.exception("Failed to process item in sync mode")
                holder = {"error": "process_failed", "kind": item.kind}
            results.append(holder)
        else:
            if item.should_reply or item.kind in {"echo", "history", "state_sync", "coexistence", "message"}:
                background.add_task(_safe_process, item)

    payload = {
        "ok": True,
        "accepted": len(items),
        "ms": int((time.time() - t0) * 1000),
    }
    if sync:
        payload["results"] = results
        # Convenience: surface the first reply for curl tests
        for r in results:
            if r.get("reply"):
                payload["reply"] = r["reply"]
                payload["lang"] = r.get("lang")
                payload["source"] = r.get("source")
                break
            if r.get("silent"):
                payload["silent"] = True
                payload["reason"] = r.get("reason")
    return JSONResponse(payload, status_code=200)


async def _safe_process(item: InboundItem) -> None:
    try:
        await process_item(item)
    except Exception:
        log.exception("Background process failed for %s", item.message_id or item.sender)


@app.get("/")
async def root() -> dict[str, Any]:
    return await health()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host=HOST, port=PORT, reload=False)
