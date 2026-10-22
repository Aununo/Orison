"""OneBot HTTP delivery with explicit send acknowledgements."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx


class OneBotDeliveryError(RuntimeError):
    """A message was not confirmed by OneBot; it must not be reported as sent."""


def send_qq_message(message_type: str, chat_id: str, message: list[dict[str, Any]]) -> str:
    if message_type not in {"group", "private"} or not re.fullmatch(r"[1-9][0-9]{0,19}", chat_id):
        raise OneBotDeliveryError("Invalid QQ destination")
    raw_url = os.environ.get("ORISON_ONEBOT_HTTP_URL", "http://127.0.0.1:15150").strip().rstrip("/")
    try:
        url = httpx.URL(raw_url)
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.query or url.fragment:
            raise ValueError
    except (ValueError, httpx.InvalidURL) as exc:
        raise OneBotDeliveryError("Invalid OneBot HTTP URL") from exc
    token = os.environ.get("ORISON_ONEBOT_ACCESS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    action = "send_group_msg" if message_type == "group" else "send_private_msg"
    target = "group_id" if message_type == "group" else "user_id"
    try:
        with httpx.Client(timeout=15.0, follow_redirects=False, trust_env=False) as client:
            response = client.post(f"{raw_url}/{action}", headers=headers, json={target: int(chat_id), "message": message})
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise OneBotDeliveryError(f"OneBot HTTP {exc.response.status_code}; delivery not confirmed") from exc
    except httpx.HTTPError as exc:
        raise OneBotDeliveryError("OneBot connection failed or timed out; delivery not confirmed") from exc
    except ValueError as exc:
        raise OneBotDeliveryError("Invalid OneBot response; delivery not confirmed") from exc
    if not isinstance(payload, dict) or payload.get("status") != "ok" or type(payload.get("retcode")) is not int or payload["retcode"] != 0:
        raise OneBotDeliveryError("OneBot did not confirm successful delivery")
    data = payload.get("data")
    message_id = data.get("message_id") if isinstance(data, dict) else None
    if isinstance(message_id, bool) or not re.fullmatch(r"-?[0-9]+", str(message_id)):
        raise OneBotDeliveryError("OneBot response has no message ID; delivery not confirmed")
    return str(message_id)
