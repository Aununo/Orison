from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Thread

from orison.config import load_config
from orison.core import SessionManager
from orison.providers import ProviderEvent, ProviderHealth, ProviderRegistry, ProviderRequest, ProviderRunResult
from orison.server import build_server


class StreamingProvider:
    name = "fake"

    def start_session(self, request: ProviderRequest) -> ProviderRunResult:
        if request.metadata.get("orison_internal") == "skill_selection":
            return ProviderRunResult(content='{"skills":[]}')
        return ProviderRunResult(content="sync reply", provider_session_id="provider-thread-1")

    def resume_session(self, provider_session_id: str, request: ProviderRequest) -> ProviderRunResult:
        return ProviderRunResult(content="sync reply", provider_session_id=provider_session_id)

    async def stream(self, request: ProviderRequest):
        yield ProviderEvent(type="session_started", provider_session_id="provider-thread-1")
        yield ProviderEvent(type="text_delta", content="stream ", provider_session_id="provider-thread-1")
        yield ProviderEvent(type="text_delta", content="reply", provider_session_id="provider-thread-1")
        yield ProviderEvent(type="final", content="stream reply", provider_session_id="provider-thread-1")

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(ok=True, message="ok")


@unittest.skipUnless(shutil.which("go"), "go is required for the Go channel integration test")
class GoChannelIntegrationTest(unittest.TestCase):
    def test_go_local_channel_talks_to_python_core(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(__file__).resolve().parents[1]
            registry = ProviderRegistry(default_name="fake", providers={"fake": StreamingProvider()})
            config = replace(load_config(root / "config.toml"), data_dir=str(Path(tempdir) / "data"))
            manager = SessionManager(
                data_dir=config.data_dir,
                registry=registry,
                memory_dir=Path(tempdir) / "memory",
                skills_dir=Path(tempdir) / "skills",
            )
            server = build_server(config, registry=registry, manager=manager, host="127.0.0.1", port=0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                go_config = Path(tempdir) / "config.toml"
                go_config.write_text(
                    textwrap.dedent(
                        f"""
                        [provider]
                        default = "openai-codex"

                        [providers.openai-codex]
                        model = "gpt-5.5"
                        reasoning_effort = "high"
                        sandbox = "workspace-write"
                        workdir = "."

                        [orison]
                        data_dir = "{Path(tempdir) / "go-data"}"

                        [channels]
                        default = "local"
                        core_url = "http://{host}:{port}"
                        http_addr = "127.0.0.1:0"
                        """
                    ).strip()
                    + "\n",
                    encoding="utf-8",
                )

                completed = subprocess.run(
                    ["go", "run", "./cmd/orison-channel"],
                    cwd=root,
                    input="hello\n/quit\n",
                    text=True,
                    capture_output=True,
                    timeout=30,
                    env={
                        **os.environ,
                        "ORISON_CONFIG": str(go_config),
                        "ORISON_CHANNEL": "local",
                        "ORISON_SESSION_ID": "local:integration",
                        "ORISON_USER_ID": "user:integration",
                    },
                )
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Orison local channel ready.", completed.stdout)
            self.assertEqual(completed.stdout.count("stream reply"), 1)

            session = manager.get_session("local:integration")
            self.assertIsNotNone(session)
            self.assertEqual(session.provider_session_id, "provider-thread-1")
            runs = manager.list_run_records("local:integration")
            self.assertEqual(runs[0]["user_message"], "hello")
            self.assertEqual(runs[0]["final_response"], "stream reply")


if __name__ == "__main__":
    unittest.main()
