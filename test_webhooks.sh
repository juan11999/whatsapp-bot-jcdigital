#!/usr/bin/env bash
# Local DRY_RUN webhook simulations. Usage: ./test_webhooks.sh [BASE_URL]
set -euo pipefail
cd "$(dirname "$0")"
BASE="${1:-http://127.0.0.1:${PORT:-8080}}"
OUT="logs/test_results.jsonl"
mkdir -p logs
: > "$OUT"

post() {
  local name="$1"
  local body="$2"
  echo "======= $name ======="
  local resp
  resp="$(curl -sS -X POST "$BASE/webhook?sync=1" \
    -H 'Content-Type: application/json' \
    -H 'X-Sync-Reply: 1' \
    -d "$body")"
  echo "$resp" | /workspace/whatsapp-bot/.venv/bin/python -m json.tool
  OUT="$OUT" NAME="$name" RESP="$resp" /workspace/whatsapp-bot/.venv/bin/python -c 'import json,os; open(os.environ["OUT"],"a",encoding="utf-8").write(json.dumps({"name":os.environ["NAME"],"response":json.loads(os.environ["RESP"])},ensure_ascii=False)+"\n")'
  echo
}

# 1) Spanish greeting — Meta v3
post "ES greeting (Meta v3)" "$(cat <<'JSON'
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "3666860806796820",
    "changes": [{
      "field": "messages",
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "34637274292", "phone_number_id": "1378713898668233"},
        "contacts": [{"profile": {"name": "Ana"}, "wa_id": "34600111111"}],
        "messages": [{
          "from": "34600111111",
          "id": "wamid.test.greet.es.1",
          "timestamp": "1731960001",
          "type": "text",
          "text": {"body": "Hola"}
        }]
      }
    }]
  }]
}
JSON
)"

# 2) French website price — Meta v3
post "FR website price (Meta v3)" "$(cat <<'JSON'
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "3666860806796820",
    "changes": [{
      "field": "messages",
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "34637274292", "phone_number_id": "1378713898668233"},
        "contacts": [{"profile": {"name": "Claire"}, "wa_id": "33600111112"}],
        "messages": [{
          "from": "33600111112",
          "id": "wamid.test.web.fr.1",
          "timestamp": "1731960002",
          "type": "text",
          "text": {"body": "Bonjour, combien coûte un site web ?"}
        }]
      }
    }]
  }]
}
JSON
)"

# 3) Aesthetics templates — Gupshup v2, Spanish
post "ES aesthetics templates (Gupshup v2)" "$(cat <<'JSON'
{
  "app": "JuanCamiloTestBot",
  "timestamp": 1731960003000,
  "version": 2,
  "type": "message",
  "payload": {
    "id": "ABEGk-test-templates-es-1",
    "source": "34600111113",
    "type": "text",
    "payload": {"text": "¿Tienen plantillas para centros de estética? Recordatorios de cita y reseñas."},
    "sender": {"phone": "34600111113", "name": "Lucía", "country_code": "34", "dial_code": "600111113"}
  }
}
JSON
)"

# 4) Human request — Meta v3 Spanish
post "ES request for a human (Meta v3)" "$(cat <<'JSON'
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "3666860806796820",
    "changes": [{
      "field": "messages",
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "34637274292", "phone_number_id": "1378713898668233"},
        "contacts": [{"profile": {"name": "Pedro"}, "wa_id": "34600111114"}],
        "messages": [{
          "from": "34600111114",
          "id": "wamid.test.human.es.1",
          "timestamp": "1731960004",
          "type": "text",
          "text": {"body": "Quiero hablar con un humano, por favor"}
        }]
      }
    }]
  }]
}
JSON
)"

# 5) Same user says humano again — should be silent
post "ES humano twice (should be silent)" "$(cat <<'JSON'
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "3666860806796820",
    "changes": [{
      "field": "messages",
      "value": {
        "messaging_product": "whatsapp",
        "messages": [{
          "from": "34600111114",
          "id": "wamid.test.human.es.2",
          "timestamp": "1731960005",
          "type": "text",
          "text": {"body": "humano"}
        }]
      }
    }]
  }]
}
JSON
)"

# 6) Non-text image — should ask for text
post "Non-text image (ask for text)" "$(cat <<'JSON'
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "3666860806796820",
    "changes": [{
      "field": "messages",
      "value": {
        "messaging_product": "whatsapp",
        "messages": [{
          "from": "34600111115",
          "id": "wamid.test.image.1",
          "timestamp": "1731960006",
          "type": "image",
          "image": {"id": "media-1", "mime_type": "image/jpeg"}
        }]
      }
    }]
  }]
}
JSON
)"

# 7) Delivery status — no reply
post "Delivery status (no reply)" "$(cat <<'JSON'
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "3666860806796820",
    "changes": [{
      "field": "messages",
      "value": {
        "messaging_product": "whatsapp",
        "statuses": [{
          "id": "wamid.test.out.1",
          "status": "delivered",
          "timestamp": "1731960007",
          "recipient_id": "34600111111"
        }]
      }
    }]
  }]
}
JSON
)"

# 8) smb_message_echoes — no reply
post "Coexistence echo (no reply)" "$(cat <<'JSON'
{
  "object": "whatsapp_business_account",
  "gs_app_id": "009b780e-e61b-4396-9cb2-d3faf84297eb",
  "entry": [{
    "id": "3666860806796820",
    "changes": [{
      "field": "smb_message_echoes",
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "34637274292", "phone_number_id": "1378713898668233"},
        "message_echoes": [{
          "from": "34637274292",
          "to": "34600111111",
          "id": "wamid.test.echo.1",
          "timestamp": "1731960008",
          "type": "text",
          "text": {"body": "Te contesto yo desde el móvil"}
        }]
      }
    }]
  }]
}
JSON
)"

# 9) history event — no reply
post "Coexistence history (no reply)" "$(cat <<'JSON'
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "3666860806796820",
    "changes": [{
      "field": "history",
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "34637274292", "phone_number_id": "1378713898668233"},
        "history": [{
          "metadata": {"phase": 0, "chunk_order": 1, "progress": 50},
          "threads": [{
            "id": "34600111999",
            "messages": [{
              "from": "34600111999",
              "id": "wamid.test.hist.1",
              "timestamp": "1730000000",
              "type": "text",
              "text": {"body": "Hola, esto es historial, no deberías responder"}
            }]
          }]
        }]
      }
    }]
  }]
}
JSON
)"

# 10) smb_app_state_sync — no reply
post "Coexistence state sync (no reply)" "$(cat <<'JSON'
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "3666860806796820",
    "changes": [{
      "field": "smb_app_state_sync",
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "34637274292", "phone_number_id": "1378713898668233"},
        "state_sync": [{"type": "contact", "action": "add", "contact": {"phone_number": "34600111111"}}]
      }
    }]
  }]
}
JSON
)"

echo "Tests written to $OUT"
