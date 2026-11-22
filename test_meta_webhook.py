#!/usr/bin/env python3
"""Local simulation of Meta Cloud API webhook verification, signatures, and replies."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

# Must be set before importing config/server.
os.environ["PROVIDER"] = "meta"
os.environ["DRY_RUN"] = "true"
os.environ["META_VERIFY_TOKEN"] = "unit-test-verify-token"
os.environ["META_APP_SECRET"] = "unit-test-app-secret"
os.environ["WEBHOOK_TOKEN"] = ""
os.environ["META_ACCESS_TOKEN"] = ""
os.environ["META_PHONE_NUMBER_ID"] = ""
os.environ["GUPSHUP_API_KEY"] = ""

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from server import app, meta_signature_ok, meta_verify_ok  # noqa: E402

SECRET = "unit-test-app-secret"
VERIFY = "unit-test-verify-token"


def sign(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


SAMPLE_MESSAGE = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "3666860806796820",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15550001111",
                            "phone_number_id": "1234567890",
                        },
                        "contacts": [{"profile": {"name": "Ana"}, "wa_id": "34600111111"}],
                        "messages": [
                            {
                                "from": "34600111111",
                                "id": "wamid.test.meta.sim.1",
                                "timestamp": "1731960001",
                                "type": "text",
                                "text": {"body": "Hola"},
                            }
                        ],
                    },
                }
            ],
        }
    ],
}

SAMPLE_STATUS = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "3666860806796820",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "statuses": [
                            {
                                "id": "wamid.test.out.1",
                                "status": "delivered",
                                "timestamp": "1731960007",
                                "recipient_id": "34600111111",
                            }
                        ],
                    },
                }
            ],
        }
    ],
}


def main() -> int:
    failed = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal failed
        if cond:
            print(f"PASS  {name}")
        else:
            failed += 1
            print(f"FAIL  {name}  {detail}")

    # Unit checks for helpers
    body = json.dumps(SAMPLE_MESSAGE, separators=(",", ":")).encode()
    header = sign(body)
    check("signature accepts valid hmac", meta_signature_ok(body, header, SECRET))
    check("signature rejects wrong secret", not meta_signature_ok(body, header, "other-secret"))
    check("signature rejects missing header", not meta_signature_ok(body, "", SECRET))
    check("signature skipped when secret empty", meta_signature_ok(body, "nope", ""))
    check(
        "verify accepts subscribe + token + challenge",
        meta_verify_ok("subscribe", VERIFY, "CHALLENGE-42", VERIFY),
    )
    check("verify rejects wrong token", not meta_verify_ok("subscribe", "nope", "CHALLENGE-42", VERIFY))
    check("verify rejects missing expected token", not meta_verify_ok("subscribe", VERIFY, "x", ""))
    check("verify rejects other mode", not meta_verify_ok("unsubscribe", VERIFY, "x", VERIFY))

    client = TestClient(app)

    r = client.get("/health")
    check("GET /health 200", r.status_code == 200 and r.json().get("ok") is True, str(r.status_code))
    check("GET /health provider=meta", r.json().get("provider") == "meta", str(r.json()))
    check("GET /health dry_run true", r.json().get("dry_run") is True, str(r.json()))

    r = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "CHALLENGE-42"},
    )
    check("GET /webhook correct token returns challenge", r.status_code == 200 and r.text == "CHALLENGE-42", f"{r.status_code} {r.text!r}")

    r = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong-token", "hub.challenge": "CHALLENGE-42"},
    )
    check("GET /webhook wrong token is 403", r.status_code == 403, str(r.status_code))

    r = client.get("/webhook")
    check("GET /webhook without params is 403", r.status_code == 403, str(r.status_code))

    raw = json.dumps(SAMPLE_MESSAGE).encode("utf-8")
    r = client.post(
        "/webhook?sync=1",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sign(raw)},
    )
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check("POST signed inbound message is 200", r.status_code == 200 and data.get("ok") is True, f"{r.status_code} {data}")
    check(
        "POST signed inbound produces a Spanish greeting",
        bool(data.get("reply")) and "JC Digital Geneve" in data.get("reply", "") and data.get("lang") == "es",
        str(data.get("reply")),
    )
    check("POST signed inbound dry-run send", bool((data.get("results") or [{}])[0].get("send", {}).get("dry_run")), str(data.get("results")))

    r = client.post(
        "/webhook?sync=1",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sign(raw, "bad-secret")},
    )
    check("POST with bad signature is 401", r.status_code == 401, str(r.status_code))

    r = client.post("/webhook?sync=1", content=raw, headers={"Content-Type": "application/json"})
    check("POST with missing signature is 401", r.status_code == 401, str(r.status_code))

    raw_status = json.dumps(SAMPLE_STATUS).encode("utf-8")
    r = client.post(
        "/webhook?sync=1",
        content=raw_status,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sign(raw_status)},
    )
    data = r.json() if r.status_code == 200 else {}
    check("POST status event is 200 and does not reply", r.status_code == 200 and not data.get("reply"), f"{r.status_code} {data}")
    kinds = [item.get("kind") for item in data.get("results") or []]
    check("POST status event parsed as status", "status" in kinds or data.get("accepted") >= 1, str(data))

    if failed:
        print(f"\n{failed} test(s) failed")
        return 1
    print("\nAll Meta webhook simulations passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
