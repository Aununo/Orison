"""Shell execution tool."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from orison.tools.base import Tool, object_schema
from orison.tools.common import _contains_internal_url, _extract_absolute_paths, _minimal_env, _truncate

class ExecTool(Tool):
    _DENY_PATTERNS = [
        r"\brm\s+-[rf]{1,2}\b",
        r"\bdel\s+/[fq]\b",
        r"\brmdir\s+/s\b",
        r"\b(mkfs|diskpart)\b",
        r"\bdd\s+if=",
        r">\s*/dev/sd",
        r"\b(shutdown|reboot|poweroff)\b",
        r":\(\)\s*\{.*\};\s*:",
        r">>?\s*\S*(?:history\.jsonl|\.dream_cursor)",
        r"\btee\b[^|;&<>]*(?:history\.jsonl|\.dream_cursor)",
    ]

    def __init__(self, workspace: Path, timeout: int = 60) -> None:
        self.workspace = workspace
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command in the workspace. Prefer read_file/write_file/edit_file "
            "and grep/glob when possible. Output is truncated; timeout defaults to 60s."
        )

    @property
    def exclusive(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "command": {"type": "string", "minLength": 1},
                "working_dir": {"type": "string"},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 600},
            },
            required=["command"],
        )

    async def execute(self, command: str, working_dir: str | None = None, timeout: int | None = None, **_: Any) -> str:
        for pattern in self._DENY_PATTERNS:
            if re.search(pattern, command.lower()):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"
        if _contains_internal_url(command):
            return "Error: Command blocked by safety guard (internal/private URL detected)"
        if "../" in command or "..\\" in command:
            return "Error: Command blocked by safety guard (path traversal detected)"
        cwd = self.workspace if not working_dir else (self.workspace / working_dir).resolve()
        try:
            cwd.relative_to(self.workspace)
        except ValueError:
            return "Error: working_dir is outside the workspace"
        for raw_path in _extract_absolute_paths(command):
            try:
                resolved = Path(os.path.expandvars(raw_path)).expanduser().resolve()
            except Exception:
                continue
            if resolved.is_absolute():
                try:
                    resolved.relative_to(self.workspace)
                except ValueError:
                    return "Error: Command blocked by safety guard (path outside workspace)"
        env = _minimal_env()
        shell = "cmd.exe" if sys.platform == "win32" else (shutil.which("bash") or "/bin/bash")
        args = [shell, "/c", command] if sys.platform == "win32" else [shell, "-l", "-c", command]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=min(timeout or self.timeout, 600))
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"Error: Command timed out after {timeout or self.timeout} seconds"
            parts = []
            if stdout:
                parts.append(stdout.decode("utf-8", "replace"))
            if stderr:
                parts.append("STDERR:\n" + stderr.decode("utf-8", "replace"))
            parts.append(f"\nExit code: {proc.returncode}")
            return _truncate("\n".join(parts), 10_000)
        except FileNotFoundError:
            return f"Error: shell not found: {shell}"
        except Exception as exc:
            return f"Error executing command: {exc}"


