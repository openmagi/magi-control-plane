"""`magi-cp share <run>`: locate transcript -> build+redact view -> upload."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi_cp.cli import share


def _write_transcript(projects_dir: Path, cwd_slug: str, session_id: str, events: list[dict]) -> Path:
    d = projects_dir / cwd_slug
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{session_id}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def _events(goal: str = "do the thing") -> list[dict]:
    return [
        {"type": "user", "sessionId": "sess-1", "message": {"role": "user", "content": goal}},
        {"type": "assistant", "sessionId": "sess-1",
         "message": {"role": "assistant", "model": "claude-opus-4-8",
                     "content": [{"type": "text", "text": "done"}],
                     "usage": {"input_tokens": 5, "output_tokens": 2}}},
    ]


def test_find_transcript_by_session_id(tmp_path: Path) -> None:
    _write_transcript(tmp_path, "proj-a", "sess-1", _events())
    found = share.find_transcript("sess-1", projects_dir=tmp_path)
    assert found is not None and found.name == "sess-1.jsonl"


def test_find_transcript_direct_path(tmp_path: Path) -> None:
    path = _write_transcript(tmp_path, "proj-a", "sess-1", _events())
    assert share.find_transcript(str(path), projects_dir=tmp_path) == path


def test_find_transcript_missing(tmp_path: Path) -> None:
    assert share.find_transcript("nope", projects_dir=tmp_path) is None


def test_build_redacted_view_scrubs_secret(tmp_path: Path) -> None:
    token = "ghp_" + "A" * 36
    _write_transcript(tmp_path, "proj-a", "sess-1", _events(goal=f"deploy {token}"))
    view = share.build_redacted_view("sess-1", projects_dir=tmp_path)
    assert view["schemaVersion"] == "openmagi.runView.v1"
    assert token not in view["summary"]["goal"]
    assert view["summary"]["model"] == "claude-opus-4-8"


def test_build_redacted_view_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        share.build_redacted_view("nope", projects_dir=tmp_path)


def test_upload_posts_view_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"token": "t", "url": "https://cloud/r/t"}).encode()

    def fake_urlopen(req, timeout=15):
        captured["url"] = req.full_url
        captured["headers"] = req.headers
        captured["body"] = json.loads(req.data)
        captured["method"] = req.get_method()
        return _Resp()

    monkeypatch.setattr(share.urllib.request, "urlopen", fake_urlopen)
    out = share.upload({"schemaVersion": "openmagi.runView.v1"}, cloud_url="https://cloud", api_key="k")
    assert out == {"token": "t", "url": "https://cloud/r/t"}
    assert captured["url"] == "https://cloud/v1/runs/share"
    assert captured["method"] == "POST"
    assert captured["headers"]["X-api-key"] == "k"  # urllib title-cases header keys
    assert captured["body"]["view"]["schemaVersion"] == "openmagi.runView.v1"


def test_cli_dry_run_prints_view_no_upload(tmp_path: Path, capsys, monkeypatch) -> None:
    _write_transcript(tmp_path, "proj-a", "sess-1", _events())

    def boom(*a, **k):
        raise AssertionError("must not upload on --dry-run")

    monkeypatch.setattr(share, "upload", boom)
    rc = share.cli(["sess-1", "--projects-dir", str(tmp_path), "--dry-run"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sessionId"] == "sess-1"


def test_cli_missing_transcript_returns_1(tmp_path: Path, capsys) -> None:
    rc = share.cli(["nope", "--projects-dir", str(tmp_path)])
    assert rc == 1
    assert "no Claude Code transcript" in capsys.readouterr().err


def test_cli_requires_api_key(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("MAGI_CP_API_KEY", raising=False)
    _write_transcript(tmp_path, "proj-a", "sess-1", _events())
    rc = share.cli(["sess-1", "--projects-dir", str(tmp_path)])
    assert rc == 2
    assert "api-key" in capsys.readouterr().err


def test_cli_happy_path_prints_url(tmp_path: Path, capsys, monkeypatch) -> None:
    _write_transcript(tmp_path, "proj-a", "sess-1", _events())
    monkeypatch.setattr(share, "upload", lambda view, **k: {"url": "https://cloud/r/abc"})
    rc = share.cli(["sess-1", "--projects-dir", str(tmp_path), "--api-key", "k"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "https://cloud/r/abc"


def test_cli_rejects_non_http_cloud_url(tmp_path: Path, capsys) -> None:
    _write_transcript(tmp_path, "proj-a", "sess-1", _events())
    rc = share.cli(["sess-1", "--projects-dir", str(tmp_path), "--api-key", "k",
                    "--cloud-url", "file:///etc/passwd"])
    assert rc == 2
    assert "http(s)" in capsys.readouterr().err


def test_cli_upload_http_error_returns_1(tmp_path: Path, capsys, monkeypatch) -> None:
    _write_transcript(tmp_path, "proj-a", "sess-1", _events())

    def boom(view, **k):
        raise share.urllib.error.HTTPError("u", 503, "down", {}, None)

    monkeypatch.setattr(share, "upload", boom)
    rc = share.cli(["sess-1", "--projects-dir", str(tmp_path), "--api-key", "k"])
    assert rc == 1
    assert "503" in capsys.readouterr().err


def test_cli_malformed_response_returns_1(tmp_path: Path, capsys, monkeypatch) -> None:
    _write_transcript(tmp_path, "proj-a", "sess-1", _events())
    monkeypatch.setattr(share, "upload", lambda view, **k: {"nope": 1})
    rc = share.cli(["sess-1", "--projects-dir", str(tmp_path), "--api-key", "k"])
    assert rc == 1
    assert "malformed" in capsys.readouterr().err


def test_find_transcript_newest_wins(tmp_path: Path) -> None:
    import os
    import time

    old = _write_transcript(tmp_path, "proj-old", "sess-1", _events("old"))
    new = _write_transcript(tmp_path, "proj-new", "sess-1", _events("new"))
    # Make 'new' clearly newer regardless of write timing.
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(new, (now, now))
    assert share.find_transcript("sess-1", projects_dir=tmp_path) == new
