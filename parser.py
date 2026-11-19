"""Parse Gupshup v2 and Meta/Gupshup v3 webhook payloads, including coexistence events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from typing import Any

from config import GUPSHUP_SOURCE, OWN_NUMBERS

# Meta/Gupshup coexistence + non-message fields we must accept and never auto-reply to.
COEXISTENCE_FIELDS = {
    "history",
    "smb_message_echoes",
    "smb_app_state_sync",
    "message_echoes",
}

STATUS_FIELDS = {
    "message_template_status_update",
    "message_template_quality_update",
    "message_template_components_update",
    "template_category_update",
    "phone_number_quality_update",
    "phone_number_name_update",
    "account_update",
    "account_alerts",
    "account_review_update",
    "business_capability_update",
    "security",
    "user_preferences",
}

# Gupshup v2 event types that are not inbound user messages.
V2_NON_MESSAGE_TYPES = {
    "message-event",
    "user-event",
    "billing-event",
    "template-event",
    "account-event",
    "system-event",
}

PHONE_RE = re.compile(r"\D+")


def normalize_phone(value: Any) -> str:
    if value is None:
        return ""
    digits = PHONE_RE.sub("", str(value))
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def is_own_number(phone: str) -> bool:
    n = normalize_phone(phone)
    if not n:
        return False
    if n in OWN_NUMBERS or n == normalize_phone(GUPSHUP_SOURCE):
        return True
    # Compare last 9–12 digits to be resilient to missing country codes.
    for own in list(OWN_NUMBERS) + [normalize_phone(GUPSHUP_SOURCE)]:
        if own and (n.endswith(own[-9:]) or own.endswith(n[-9:])):
            return True
    return False


@dataclass
class InboundItem:
    kind: str  # message | status | echo | history | state_sync | coexistence | other
    field: str = ""
    message_id: str = ""
    sender: str = ""
    sender_name: str = ""
    text: str | None = None
    message_type: str = ""
    should_reply: bool = False
    reason: str = ""
    extra: dict[str, Any] = dc_field(default_factory=dict)


def _as_dict(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        return data
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8")
        except Exception:
            return None
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def parse_webhook(data: Any) -> list[InboundItem]:
    body = _as_dict(data)
    if not body:
        return [
            InboundItem(kind="other", reason="unparseable or empty body", should_reply=False)
        ]

    # Meta / Gupshup v3 (primary): object + entry[].changes[]
    if "entry" in body and isinstance(body.get("entry"), list):
        return _parse_meta_v3(body)

    # Some proxies wrap the Meta payload.
    for key in ("payload", "body", "data"):
        inner = body.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("entry"), list):
            return _parse_meta_v3(inner)

    # Gupshup v2
    if body.get("version") == 2 or body.get("type") in (
        {"message"} | V2_NON_MESSAGE_TYPES
    ):
        return _parse_gupshup_v2(body)

    # Loose v2: type=message with payload.source
    payload = body.get("payload")
    if body.get("type") == "message" or (
        isinstance(payload, dict) and ("source" in payload or "payload" in payload)
    ):
        return _parse_gupshup_v2(body)

    return [
        InboundItem(
            kind="other",
            reason="unrecognised payload shape",
            extra={"keys": list(body.keys())},
            should_reply=False,
        )
    ]


def _parse_meta_v3(body: dict[str, Any]) -> list[InboundItem]:
    items: list[InboundItem] = []
    for entry in body.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            field = str(change.get("field") or "")
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            items.extend(_parse_meta_change(field, value))
    return items or [
        InboundItem(kind="other", field="entry", reason="empty entry/changes", should_reply=False)
    ]


def _contacts_name(value: dict[str, Any], phone: str) -> str:
    for contact in value.get("contacts") or []:
        if not isinstance(contact, dict):
            continue
        wa_id = normalize_phone(contact.get("wa_id") or contact.get("id") or "")
        if wa_id and phone and (wa_id == phone or wa_id.endswith(phone[-9:]) or phone.endswith(wa_id[-9:])):
            profile = contact.get("profile") if isinstance(contact.get("profile"), dict) else {}
            return str(profile.get("name") or "")
    contacts = value.get("contacts") or []
    if len(contacts) == 1 and isinstance(contacts[0], dict):
        profile = contacts[0].get("profile") if isinstance(contacts[0].get("profile"), dict) else {}
        return str(profile.get("name") or "")
    return ""


def _extract_text_and_type(msg: dict[str, Any]) -> tuple[str, str | None]:
    mtype = str(msg.get("type") or "text")
    text: str | None = None
    if mtype == "text":
        t = msg.get("text")
        if isinstance(t, dict):
            text = t.get("body") or t.get("text")
        elif isinstance(t, str):
            text = t
    elif mtype == "button":
        btn = msg.get("button") if isinstance(msg.get("button"), dict) else {}
        text = btn.get("text") or btn.get("payload")
    elif mtype in {"interactive", "button_reply", "list_reply"}:
        interactive = msg.get("interactive") if isinstance(msg.get("interactive"), dict) else msg
        for key in ("button_reply", "list_reply"):
            node = interactive.get(key) if isinstance(interactive.get(key), dict) else {}
            if node:
                text = node.get("title") or node.get("id") or node.get("description")
                mtype = key
                break
    elif mtype == "image":
        img = msg.get("image") if isinstance(msg.get("image"), dict) else {}
        caption = img.get("caption")
        text = caption if caption else None
    # Other media: text stays None so the bot can ask for text.
    if isinstance(text, str):
        text = text.strip()
        if not text:
            text = None
    return mtype, text


def _parse_meta_change(field: str, value: dict[str, Any]) -> list[InboundItem]:
    items: list[InboundItem] = []

    if field in COEXISTENCE_FIELDS or field.startswith("smb_"):
        kind = {
            "history": "history",
            "smb_message_echoes": "echo",
            "message_echoes": "echo",
            "smb_app_state_sync": "state_sync",
        }.get(field, "coexistence")
        # Log every echo / history message, never reply.
        echoes = value.get("message_echoes") or value.get("messages") or value.get("history") or []
        if field == "history":
            # history.value.history[] or history.value.messages / threads
            history = value.get("history") or []
            if isinstance(history, list) and history:
                for chunk in history:
                    if not isinstance(chunk, dict):
                        continue
                    threads = chunk.get("threads") or chunk.get("messages") or [chunk]
                    if isinstance(threads, list):
                        for thread in threads:
                            if isinstance(thread, dict):
                                inner_msgs = thread.get("messages") or [thread]
                                for msg in inner_msgs if isinstance(inner_msgs, list) else []:
                                    if isinstance(msg, dict):
                                        items.append(_history_item(field, msg, chunk))
                    items.append(
                        InboundItem(
                            kind="history",
                            field=field,
                            reason="history chunk",
                            extra={"keys": list(chunk.keys())},
                            should_reply=False,
                        )
                    )
            else:
                items.append(
                    InboundItem(
                        kind="history",
                        field=field,
                        reason="history event",
                        extra={"value_keys": list(value.keys())},
                        should_reply=False,
                    )
                )
            return items or [
                InboundItem(kind="history", field=field, reason="history event", should_reply=False)
            ]

        if field == "smb_app_state_sync":
            items.append(
                InboundItem(
                    kind="state_sync",
                    field=field,
                    reason="contact/app state sync",
                    extra={
                        "count": len(value.get("state_sync") or []),
                        "value_keys": list(value.keys()),
                    },
                    should_reply=False,
                )
            )
            return items

        # echoes
        if isinstance(echoes, list) and echoes:
            for echo in echoes:
                if not isinstance(echo, dict):
                    continue
                mtype, text = _extract_text_and_type(echo)
                items.append(
                    InboundItem(
                        kind="echo",
                        field=field,
                        message_id=str(echo.get("id") or ""),
                        sender=normalize_phone(echo.get("from") or echo.get("to") or ""),
                        text=text,
                        message_type=mtype,
                        reason="business app echo — do not auto-reply",
                        should_reply=False,
                        extra={"to": normalize_phone(echo.get("to") or "")},
                    )
                )
            return items
        items.append(
            InboundItem(
                kind=kind,
                field=field,
                reason="coexistence event",
                extra={"value_keys": list(value.keys())},
                should_reply=False,
            )
        )
        return items

    # Status / delivery updates inside messages field or dedicated fields
    statuses = value.get("statuses") or []
    if field in STATUS_FIELDS or (statuses and not value.get("messages")):
        for st in statuses if isinstance(statuses, list) else [None]:
            st = st if isinstance(st, dict) else {}
            items.append(
                InboundItem(
                    kind="status",
                    field=field or "statuses",
                    message_id=str(st.get("id") or ""),
                    sender=normalize_phone(st.get("recipient_id") or ""),
                    reason=str(st.get("status") or "status event"),
                    should_reply=False,
                )
            )
        if not items:
            items.append(
                InboundItem(kind="status", field=field, reason="account/status event", should_reply=False)
            )
        return items

    # Live inbound messages — the only path that may auto-reply
    messages = value.get("messages") or []
    if field in {"messages", ""} or messages:
        # statuses piggy-backed on the same change
        for st in statuses if isinstance(statuses, list) else []:
            if isinstance(st, dict):
                items.append(
                    InboundItem(
                        kind="status",
                        field="messages",
                        message_id=str(st.get("id") or ""),
                        sender=normalize_phone(st.get("recipient_id") or ""),
                        reason=str(st.get("status") or "status"),
                        should_reply=False,
                    )
                )
        for msg in messages if isinstance(messages, list) else []:
            if not isinstance(msg, dict):
                continue
            # Edits/revokes/system: log only
            mtype, text = _extract_text_and_type(msg)
            sender = normalize_phone(msg.get("from") or "")
            mid = str(msg.get("id") or "")
            if mtype in {"system", "reaction", "edit", "revoke", "unsupported"}:
                items.append(
                    InboundItem(
                        kind="other",
                        field=field or "messages",
                        message_id=mid,
                        sender=sender,
                        message_type=mtype,
                        reason=f"ignore {mtype}",
                        should_reply=False,
                    )
                )
                continue
            if is_own_number(sender):
                items.append(
                    InboundItem(
                        kind="echo",
                        field=field or "messages",
                        message_id=mid,
                        sender=sender,
                        text=text,
                        message_type=mtype,
                        reason="sender is the business number — do not auto-reply",
                        should_reply=False,
                    )
                )
                continue
            items.append(
                InboundItem(
                    kind="message",
                    field=field or "messages",
                    message_id=mid,
                    sender=sender,
                    sender_name=_contacts_name(value, sender),
                    text=text,
                    message_type=mtype or "text",
                    should_reply=True,
                    reason="inbound user message",
                )
            )
        return items

    items.append(
        InboundItem(
            kind="other",
            field=field,
            reason="unhandled meta field",
            extra={"value_keys": list(value.keys())},
            should_reply=False,
        )
    )
    return items


def _history_item(field: str, msg: dict[str, Any], chunk: dict[str, Any]) -> InboundItem:
    mtype, text = _extract_text_and_type(msg)
    return InboundItem(
        kind="history",
        field=field,
        message_id=str(msg.get("id") or ""),
        sender=normalize_phone(msg.get("from") or ""),
        text=text,
        message_type=mtype,
        reason="historical message — do not auto-reply",
        should_reply=False,
        extra={"chunk_keys": list(chunk.keys())},
    )


def _parse_gupshup_v2(body: dict[str, Any]) -> list[InboundItem]:
    ev_type = str(body.get("type") or "")
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}

    if ev_type in V2_NON_MESSAGE_TYPES or ev_type.endswith("-event"):
        return [
            InboundItem(
                kind="status" if "message-event" in ev_type else "other",
                field=ev_type,
                message_id=str(payload.get("id") or payload.get("gsId") or ""),
                sender=normalize_phone(payload.get("destination") or payload.get("phone") or payload.get("source") or ""),
                reason=str(payload.get("type") or ev_type),
                should_reply=False,
            )
        ]

    if ev_type and ev_type != "message":
        # Unknown v2 type: log, don't reply (covers future coexistence wrappers)
        lowered = ev_type.lower()
        if any(k in lowered for k in ("history", "echo", "coexist", "smb", "state_sync")):
            return [
                InboundItem(
                    kind="coexistence",
                    field=ev_type,
                    reason="v2 coexistence-like event",
                    extra={"payload_keys": list(payload.keys())},
                    should_reply=False,
                )
            ]
        return [
            InboundItem(kind="other", field=ev_type, reason=f"v2 type {ev_type}", should_reply=False)
        ]

    sender = normalize_phone(payload.get("source") or payload.get("sender", {}).get("phone") if isinstance(payload.get("sender"), dict) else payload.get("source"))
    sender_name = ""
    if isinstance(payload.get("sender"), dict):
        sender_name = str(payload["sender"].get("name") or "")
        if not sender:
            sender = normalize_phone(payload["sender"].get("phone"))

    inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    mtype = str(payload.get("type") or "text")
    text: str | None = None
    if mtype == "text":
        text = inner.get("text") if isinstance(inner.get("text"), str) else (inner.get("text") if inner else None)
        if text is None and isinstance(payload.get("text"), str):
            text = payload.get("text")
    elif mtype in {"button_reply", "list_reply"}:
        text = inner.get("title") or inner.get("postbackText") or inner.get("id")
        if isinstance(text, str):
            text = text.strip()
    elif mtype == "image":
        cap = inner.get("caption")
        text = cap if isinstance(cap, str) and cap.strip() else None

    if isinstance(text, str):
        text = text.strip() or None

    mid = str(payload.get("id") or body.get("messageId") or "")
    if is_own_number(sender):
        return [
            InboundItem(
                kind="echo",
                field="message",
                message_id=mid,
                sender=sender,
                sender_name=sender_name,
                text=text,
                message_type=mtype,
                reason="sender is the business number — do not auto-reply",
                should_reply=False,
            )
        ]

    return [
        InboundItem(
            kind="message",
            field="message",
            message_id=mid,
            sender=sender,
            sender_name=sender_name,
            text=text,
            message_type=mtype or "text",
            should_reply=True,
            reason="inbound user message (gupshup v2)",
        )
    ]
