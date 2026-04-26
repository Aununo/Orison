"""Message delivery/outbox tool."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orison.tools.base import Tool, object_schema
from orison.tools.common import _strip_think


class MessageTool(Tool):
    def __init__(self, *, data_dir: Path, session_id: str) -> None:
        self.data_dir = data_dir
        self.session_id = session_id

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return (
            "Send or queue a message for delivery through Orison. In Core-only mode this writes "
            "an outbox record under data_dir/outbox; if ORISON_MESSAGE_COMMAND is set, it is "
            "also executed with the JSON payload on stdin."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "content": {"type": "string"},
                "channel": {"type": "string"},
                "chat_id": {"type": "string"},
                "media": {"type": "array", "items": {"type": "string"}},
            },
            required=["content"],
        )

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        media: list[str] | None = None,
        **_: Any,
    ) -> str:
        payload = {
            "session_id": self.session_id,
            "channel": channel or "",
            "chat_id": chat_id or "",
            "content": _strip_think(content),
            "media": media or [],
            "created_at": datetime.now(UTC).isoformat(),
        }
        outbox = self.data_dir / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        outbox_file = outbox / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}.json"
        outbox_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        command = os.environ.get("ORISON_MESSAGE_COMMAND", "").strip()
        if command:
            try:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                    timeout=15,
                )
                if proc.returncode != 0:
                    detail = stderr.decode("utf-8", "replace").strip() or stdout.decode("utf-8", "replace").strip()
                    return f"Error sending message command failed: {detail}; queued at {outbox_file}"
            except Exception as exc:
                return f"Error sending message command: {exc}; queued at {outbox_file}"
        return f"Message queued at {outbox_file}"
