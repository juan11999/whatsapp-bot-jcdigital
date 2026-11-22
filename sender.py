"""Route outbound WhatsApp text to Meta or Gupshup based on PROVIDER."""

from __future__ import annotations

from typing import Any

from config import PROVIDER


async def send_text(destination: str, text: str, *, dry_run: bool | None = None) -> dict[str, Any]:
    if PROVIDER == "gupshup":
        from gupshup_client import send_text as _send
    else:
        from meta_client import send_text as _send
    return await _send(destination, text, dry_run=dry_run)
