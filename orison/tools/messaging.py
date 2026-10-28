"""Conversation-scoped proactive messages and recoverable pending outbox."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import signal
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orison import limits
from orison.tools.base import Tool, object_schema
from orison.tools.common import _strip_think, _validate_url_safe
from orison.tools.onebot import OneBotDeliveryError, send_qq_message


class MessageTool(Tool):
    def __init__(self, *, data_dir: Path, session_id: str, context_metadata: dict[str, Any] | None = None,
                 family: dict[str, Any] | None = None) -> None:
        self.data_dir = data_dir
        self.session_id = session_id
        context = context_metadata or {}
        self.channel = str(context.get("channel") or "local")
        self.chat_id = session_id
        self.message_type = str(context.get("message_type") or "private")
        self.allowed_mentions = {str(context.get("user_id") or "")}
        group = re.fullmatch(r"qq:g:(\d+)", session_id)
        legacy_group = re.fullmatch(r"qq:(\d+):(\d+)", session_id)
        private = re.fullmatch(r"qq:(\d+)", session_id)
        if group or legacy_group:
            self.channel, self.chat_id, self.message_type = "qq", (group or legacy_group)[1], "group"
        elif private:
            self.channel, self.chat_id, self.message_type = "qq", private[1], "private"
        elif self.channel == "qq":
            self.message_type = "group" if context.get("group_id") else "private"
            self.chat_id = str(context.get("group_id") or context.get("user_id") or "")
        if self.message_type == "group" and self.chat_id == (family or {}).get("group_id"):
            self.allowed_mentions.update((family or {}).get("members", {}))

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return (
            "Send a proactive message to the current conversation, including from scheduled tasks. "
            "QQ messages are delivered through OneBot with a confirmed message ID. "
            "Use mentions for known family member IDs when a personal group reminder is requested. "
            "The destination is fixed by Core. A queued or failed result is not successful delivery; "
            "do not automatically retry an unconfirmed send because it may duplicate a message. "
            "Ordinary replies are delivered by the channel and do not need this tool."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "content": {"type": "string", "maxLength": limits.MAX_MESSAGE_BYTES},
                "media": {"type": "array", "maxItems": 4, "items": {"type": "string"},
                          "description": "Public image URLs or image data URIs; local file paths are not allowed."},
                "mentions": {"type": "array", "maxItems": 8, "items": {"type": "string"},
                             "description": "Known family member QQ IDs to mention in this group; never all. "
                                            "Mentions are placed before content automatically; do not repeat @name in content."},
            },
            required=["content"],
        )

    async def execute(self, content: str, media: list[str] | None = None, mentions: list[str] | None = None,
                      channel: str | None = None, chat_id: str | None = None, **_: Any) -> str:
        if channel not in {None, self.channel} or chat_id not in {None, self.chat_id}:
            return "Error: messages may only target the current conversation"
        content = _strip_think(content)
        media, mentions = media or [], list(dict.fromkeys(mentions or []))
        if not content and not media:
            return "Error: a message requires text or an image"
        if len(content.encode()) > limits.MAX_MESSAGE_BYTES or len(media) > 4:
            return "Error: message exceeds the content or image limit"
        if mentions and (self.channel != "qq" or self.message_type != "group" or
                         any(not re.fullmatch(r"[1-9][0-9]{0,19}", member) or member not in self.allowed_mentions for member in mentions)):
            return "Error: only known members of the current group may be mentioned"
        segments = []
        for member in mentions:
            # QQ renders an at segment as "@nickname" without a trailing space.
            segments.append({"type": "at", "data": {"qq": member}})
            segments.append({"type": "text", "data": {"text": " "}})
        if content:
            segments.append({"type": "text", "data": {"text": content}})
        try:
            for item in media:
                image = await asyncio.to_thread(_onebot_image, item)
                segments.append({"type": "image", "data": {"file": image}})
        except ValueError as exc:
            return f"Error: {exc}"
        if self.channel == "qq" and not re.fullmatch(r"[1-9][0-9]{0,19}", self.chat_id):
            return "Error: the current QQ conversation has no valid destination"

        payload = {"session_id": self.session_id, "channel": self.channel, "chat_id": self.chat_id,
                   "message_type": self.message_type, "content": content, "media": media, "mentions": mentions,
                   "status": "pending", "created_at": datetime.now(UTC).isoformat()}
        serialized = json.dumps(payload, ensure_ascii=False)
        limits.ensure_storage_capacity(self.data_dir, len(serialized.encode()) + 4096)
        outbox = self.data_dir / "outbox"
        outbox.mkdir(parents=True, exist_ok=True, mode=0o700)
        outbox_file = outbox / f"{uuid.uuid4().hex}.json"
        with outbox_file.open("x", encoding="utf-8") as record:
            outbox_file.chmod(0o600)
            record.write(serialized)

        command = os.environ.get("ORISON_MESSAGE_COMMAND", "").strip()
        try:
            if command:
                result = await _send_command(command, serialized)
            elif self.channel == "qq":
                message_id = await asyncio.to_thread(send_qq_message, self.message_type, self.chat_id, segments)
                result = f"Sent QQ message (message_id: {message_id})."
            else:
                return f"Message queued at {outbox_file}; no delivery transport is configured."
        except (OneBotDeliveryError, OSError) as exc:
            return f"Error: {exc}; delivery not confirmed. Pending message retained at {outbox_file}."
        try:
            outbox_file.unlink()
        except OSError:
            return result + f" Pending outbox cleanup failed at {outbox_file}; do not resend."
        return result


def _onebot_image(value: str) -> str:
    if value.startswith("data:image/"):
        match = re.fullmatch(r"data:image/(?:png|jpeg|gif|webp);base64,([A-Za-z0-9+/=]+)", value)
        if not match or len(match[1]) > 8 * 1024 * 1024:
            raise ValueError("Invalid or oversized image data URI")
        try:
            decoded = base64.b64decode(match[1], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid image base64") from exc
        if not decoded.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")):
            raise ValueError("Image data does not contain a supported image")
        if decoded.startswith(b"RIFF") and decoded[8:12] != b"WEBP":
            raise ValueError("Image data does not contain a supported image")
        return "base64://" + match[1]
    valid, _ = _validate_url_safe(value)
    if not valid:
        raise ValueError("Media must be a public image URL or image data URI")
    return value


async def _send_command(command: str, serialized: str) -> str:
    proc = await asyncio.create_subprocess_shell(
        command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=os.name == "posix",
    )
    try:
        await asyncio.wait_for(proc.communicate(serialized.encode()), timeout=15)
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        if proc.returncode is None:
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise OneBotDeliveryError("Delivery command timed out; delivery not confirmed") from exc
    if proc.returncode != 0:
        raise OneBotDeliveryError(f"Delivery command exited with status {proc.returncode}")
    return "Message dispatched by the configured delivery command."
