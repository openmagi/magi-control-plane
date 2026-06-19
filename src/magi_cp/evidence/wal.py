"""Local Write-Ahead-Log = cache of cloud-signed evidence tokens.

The gate reads this on every PreToolUse. Entries are *not* a source of truth —
signatures (Ed25519, public-key verified) are. WAL just lives close to the
process so the gate stays fast and works briefly offline.
"""
from __future__ import annotations
import json
import os


class Wal:
    def __init__(self, path: str):
        self.path = path

    def append(self, entry: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def entries(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def clear(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)
