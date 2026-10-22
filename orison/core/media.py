"""Small, session-scoped image cache for immediate QQ follow-up questions."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Any

from orison.providers.base import ProviderMessage


class RecentImages:
    MAX_BYTES = 16 * 1024 * 1024
    MAX_IMAGES = 32
    TTL_SECONDS = 15 * 60

    def __init__(self) -> None:
        self._items: OrderedDict[tuple[str, str], tuple[float, str]] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def _prune(self) -> None:
        now = time.monotonic()
        for key, (expires, data) in list(self._items.items()):
            if expires <= now:
                self._size -= len(data)
                del self._items[key]
        while self._size > self.MAX_BYTES or len(self._items) > self.MAX_IMAGES:
            _, (_, data) = self._items.popitem(last=False)
            self._size -= len(data)

    def remember(self, scope: str, images: Any) -> list[dict[str, Any]]:
        if not isinstance(images, list):
            return []
        result = []
        with self._lock:
            self._prune()
            for image in images[:8]:
                if not isinstance(image, dict):
                    continue
                data = image.get("data")
                item = dict(image)
                if isinstance(data, str) and data.startswith("data:image/") and data.isascii() and len(data) <= self.MAX_BYTES:
                    reference = hashlib.sha256(data.encode()).hexdigest()
                    key = (scope, reference)
                    if key in self._items:
                        self._size -= len(self._items.pop(key)[1])
                    self._items[key] = (time.monotonic() + self.TTL_SECONDS, data)
                    self._size += len(data)
                    item["image_ref"] = reference
                result.append(item)
            self._prune()
        return result

    def resolve(self, scope: str, messages: list[ProviderMessage]) -> list[ProviderMessage]:
        result = []
        remaining = 8
        with self._lock:
            self._prune()
            for message in reversed(messages):
                metadata = dict(message.metadata)
                images = metadata.get("images")
                if not isinstance(images, list) or message.role != "user":
                    result.append(message)
                    continue
                ready, missing = [], False
                for image in images:
                    if not isinstance(image, dict):
                        continue
                    if remaining == 0:
                        missing = True
                        continue
                    reference = image.get("image_ref")
                    if not isinstance(reference, str):
                        ready.append(image)
                        remaining -= 1
                        continue
                    cached = self._items.get((scope, reference))
                    if cached is None:
                        missing = True
                        continue
                    ready.append({**image, "data": cached[1]})
                    remaining -= 1
                metadata["images"] = ready
                content = message.content
                if missing:
                    content += "\n[Some previously shared images are unavailable or outside the image budget; ask for a resend instead of inferring their contents.]"
                result.append(replace(message, content=content, metadata=metadata))
        return list(reversed(result))
