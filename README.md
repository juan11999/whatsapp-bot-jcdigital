# JC Digital Geneve — bot de WhatsApp (prueba)

Servidor webhook que recibe mensajes de WhatsApp a través de Gupshup (formato **Meta v3** como principal; también acepta Gupshup v2) y responde como el asistente de **JC Digital Geneve**.

Número conectado: **+34 637 27 42 92** (app `JuanCamiloTestBot`, App ID `009b780e-e61b-4396-9cb2-d3faf84297eb`, modo coexistencia).

Mientras no haya clave de API ni saldo en Gupshup, el bot corre en `DRY_RUN`: registra las respuestas en `logs/outbound.jsonl` y no llama a la API de envío.

## Cómo arrancar

```bash
cd /workspace/whatsapp-bot
./run.sh
```

Esto levanta el servidor en el puerto 8080 y un túnel HTTPS privado por Serveo (SSH inverso a través del proxy de salida del box, con rearranque automático). La URL actual queda en `PUBLIC_URL.txt`. Cambia si se cae la sesión SSH y el loop la vuelve a abrir.

Para parar: `./stop.sh`.

Variables en `.env` (plantilla en `.env.example`). Cuando tengas la clave de Gupshup:

```
GUPSHUP_API_KEY=...   # pegarla aquí, nunca en el chat
DRY_RUN=false         # solo cuando haya clave, verificación y saldo
```

## URL pública del webhook

El túnel por defecto es **serveo.net** (HTTPS, sin visor público de payloads). `./run.sh` escribe la URL completa del callback en `PUBLIC_URL.txt` (ruta `/webhook`).

En Gupshup → pestaña Webhooks:

1. Formato: **Meta format (v3)**.
2. Callback URL: el contenido de `PUBLIC_URL.txt` (ahora `https://957f9b416c5a7599-54-148-83-46.serveousercontent.com/webhook`).
3. Suscribe también el tipo de evento **Coexistence** (`history`, `smb_message_echoes`, `smb_app_state_sync`). Hay que hacerlo **antes del 19 nov 2026, 22:20 Europe/Zurich**.

El endpoint acepta esos eventos, los registra en `logs/coexistence.jsonl` y **no responde** a ecos del móvil de Juan Camilo ni a mensajes de historial.

**Estabilidad de la URL:** en el plan gratuito de Serveo la URL cambia cada vez que se reinicia la sesión SSH. El script `bin/tunnel-loop.sh` la mantiene viva y, si se cae, reconecta y actualiza `PUBLIC_URL.txt`. Hay que volver a pegar la URL nueva en Gupshup si eso ocurre. Para reservar un subdominio estable (gratis, con cuenta de Google o GitHub) se puede registrar la clave `data/tunnel_ed25519.pub` en https://console.serveo.net y definir `TUNNEL_SUBDOMAIN` en `.env`; no se ha hecho aquí.

**No usar smee.io** para el webhook real: el canal es visible para quien tenga el enlace y reenvía/repite payloads recientes (incluido un posible historial de WhatsApp). Solo está disponible con `USE_SMEE=1` para pruebas locales.

`GET /webhook` en el servidor local atiende la verificación de Meta (`hub.mode=subscribe`, token `WEBHOOK_VERIFY_TOKEN`).

Un túnel de Cloudflare quick no llega a establecerse en este box (el protocolo hacia el edge está bloqueado). `bin/cloudflared` queda instalado por si más adelante se puede usar.

## Cómo se responde

- Si hay una clave LLM en el entorno (`LLM_PROVIDER` + `LLM_API_KEY`, o `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`, `GROQ_API_KEY`, etc.), se usa ese proveedor.
- Si no hay ninguna (caso actual), entra un respondedor de reglas/FAQ basado en `knowledge.md`.
- Recuerda las últimas ~10 intervenciones por usuario en `data/memory.sqlite`.
- Detecta español, francés, inglés o portugués y contesta en el mismo idioma.
- Si piden un humano, una reunión o un presupuesto formal, responde una vez que Juan Camilo les contactará, lo anota en `handoffs.log` y deja de auto-responder a esa persona durante 24 h (un segundo «humano» no genera otra respuesta).

Edita `knowledge.md` para cambiar servicios, tono o precios. El bot no inventa precios fuera de los que estén ahí.

## Pruebas locales

```bash
./test_webhooks.sh http://127.0.0.1:8080
```

Usa `?sync=1` para que el JSON de respuesta incluya el texto que habría enviado el bot.

## Envío (Gupshup)

Documentación actual (session text): `POST https://api.gupshup.io/wa/api/v1/msg`  
Cabecera `apikey`, cuerpo `application/x-www-form-urlencoded`: `channel=whatsapp`, `source=34637274292`, `destination`, `src.name=JuanCamiloTestBot`, `message={"type":"text","text":"..."}`.
