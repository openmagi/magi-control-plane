"""Append-only hash-chain ledger.

Each entry: {prev, body, token, ts, h} where h = sha256(prev || token).
Tampering with any body or token breaks the chain at that point.

Note: a real production ledger should also chain over a content hash that
includes `body` and use a Merkle tree for log proofs. v0 keeps it simple —
prev-link over token is enough to detect any single-line edit.
"""
from __future__ import annotations
import hashlib
import json
import os
import time


def _canonical(body: dict) -> str:
    return json.dumps(body, sort_keys=True, ensure_ascii=False)


def _chain_hash(prev: str, body: dict, token: str) -> str:
    """Chain commits to *both* body and token, so tampering with either is
    detected. Without `body`, an attacker could swap a body's fields without
    changing the token and the chain would still verify.
    """
    return hashlib.sha256(
        (prev + "|" + _canonical(body) + "|" + token).encode("utf-8")
    ).hexdigest()


class Ledger:
    def __init__(self, path: str):
        self.path = path

    def _read_last_hash(self) -> str:
        if not os.path.exists(self.path):
            return ""
        with open(self.path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return ""
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8").splitlines()
        return json.loads(tail[-1]).get("h", "") if tail else ""

    def append(self, body: dict, token: str) -> dict:
        prev = self._read_last_hash()
        entry = {
            "prev": prev,
            "body": body,
            "token": token,
            "ts": int(time.time()),
            "h": _chain_hash(prev, body, token),
        }
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def entries(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def verify_chain(self) -> bool:
        prev = ""
        for entry in self.entries():
            if entry["prev"] != prev:
                return False
            if entry["h"] != _chain_hash(entry["prev"], entry["body"], entry["token"]):
                return False
            prev = entry["h"]
        return True
