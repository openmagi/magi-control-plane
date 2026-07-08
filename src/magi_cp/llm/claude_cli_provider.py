"""ClaudeCliProvider - LlmProvider impl backed by the local `claude -p` CLI.

Purpose (self-host single-user only): let an operator who has NOT set an API
key power the conversational policy-authoring compiler with their existing
Claude subscription. `claude login` (OAuth on the same machine) is the auth;
this provider shells out to `claude -p --output-format json` and returns the
model's text. magi-cp is deployed single-user self-host only, so the ToS
concern about multi-tenant subscription sharing does not apply.

Precedence (see cloud/app.py `_resolve_llm_provider_optional`): a successfully
built API-key provider ALWAYS wins. This CLI fallback runs only when the env
resolution yielded None (no provider wired, or a wired provider whose key is
missing). Kevin's rule: "API key 등록 안한 경우에만 폴백."

Verified `claude` flags (empirically from `claude -p --help` in the target
environment, Claude Code CLI):
  - `-p` / `--print` : non-interactive; reads the prompt from STDIN when no
    prompt arg is given. The untrusted compiler prompt is passed via stdin so
    it is NEVER interpolated into a command line.
  - `--output-format json` : emits a single JSON object with a `result` field
    carrying the model text (choices: text, json, stream-json).
  - `--system-prompt <text>` : sets the session system prompt.
  - `--allowedTools ""` : empty allow-list disables all tools.
  - `--strict-mcp-config` : with no `--mcp-config`, uses NO MCP servers.
  - `--model <alias-or-id>` : only added when MAGI_CP_CLAUDE_CLI_MODEL is set;
    otherwise the CLI's own default model is used.

Security posture: argv list only (never shell=True); prompt via stdin;
start_new_session=True so a timeout kills the whole process group; generous
120s timeout since a headless CC turn is slower than a raw API call.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess

from .provider import LlmMessage, LlmProviderError


_CLI_BINARY = "claude"
_DEFAULT_TIMEOUT = 120.0
# How many characters of stderr to surface in an error (keep it short).
_STDERR_SLICE = 500
# Best-effort, advisory-only credentials signal. macOS keychain auth leaves
# no such file, so its ABSENCE proves nothing; presence is a weak positive.
_CREDS_HINT = os.path.expanduser("~/.claude/.credentials.json")
# Substrings that, in stderr, strongly suggest the CLI is not logged in.
_AUTH_SIGNALS = (
    "not logged in",
    "not authenticated",
    "unauthenticated",
    "please log in",
    "please run `claude login`",
    "run claude login",
    "invalid api key",
    "authentication",
    "401",
    "oauth",
    "no credentials",
)


def claude_cli_available() -> bool:
    """True iff the `claude` binary is on PATH.

    Deliberately cheap: no auth probe (keeps boot fast). Authentication
    failures surface actionably at the first `complete()` call.
    """
    return shutil.which(_CLI_BINARY) is not None


class ClaudeCliProvider:
    """LlmProvider that drives the local `claude -p` CLI (subscription auth)."""

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        binary: str | None = None,
    ) -> None:
        # Model resolution: explicit arg -> env -> None (use the CLI default).
        self.model = model or os.environ.get("MAGI_CP_CLAUDE_CLI_MODEL") or None
        self.timeout = timeout
        self.binary = binary or _CLI_BINARY

    @staticmethod
    def _split(messages: list[LlmMessage]) -> tuple[str, str]:
        """Linearize messages into (system_text, prompt_text).

        System entries are joined newline-double (mirrors AnthropicProvider).
        `claude -p` is single-turn, so user/assistant turns are rendered into
        one prompt string with explicit role markers; the last user turn
        carries the actual ask and earlier turns are context.
        """
        systems = [m["content"] for m in messages if m["role"] == "system"]
        turns: list[str] = []
        for m in messages:
            role = m["role"]
            if role == "user":
                turns.append(f"User:\n{m['content']}")
            elif role == "assistant":
                turns.append(f"Assistant:\n{m['content']}")
        return ("\n\n".join(systems), "\n\n".join(turns))

    def _build_argv(self, system_text: str) -> list[str]:
        argv = [
            self.binary,
            "-p",
            "--output-format",
            "json",
            # Disable ALL tools (empty allow-list) and MCP (strict config with
            # no --mcp-config == no servers). The compiler is a pure text
            # transform; the CLI must not touch the filesystem or network.
            "--allowedTools",
            "",
            "--strict-mcp-config",
        ]
        if system_text:
            argv += ["--system-prompt", system_text]
        if self.model:
            argv += ["--model", self.model]
        return argv

    def complete(self, messages: list[LlmMessage]) -> str:
        system_text, prompt_text = self._split(messages)
        argv = self._build_argv(system_text)

        popen_kwargs: dict = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "close_fds": True,
        }
        # Own process group so a timeout can kill grandchildren too.
        if hasattr(os, "setsid"):
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(argv, **popen_kwargs)  # noqa: S603 - argv list, no shell
        except FileNotFoundError as e:
            raise LlmProviderError(
                f"claude CLI binary {self.binary!r} not found on PATH: {e}"
            ) from e
        except OSError as e:
            raise LlmProviderError(f"claude CLI could not be spawned: {e}") from e

        try:
            out_b, err_b = proc.communicate(
                input=prompt_text.encode("utf-8"),
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as e:
            # Kill the whole group, then reap so we do not leak a zombie.
            try:
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                else:  # pragma: no cover - non-*nix fallback
                    proc.terminate()
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    if hasattr(os, "killpg"):
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    else:  # pragma: no cover
                        proc.kill()
                except (ProcessLookupError, OSError):
                    pass
            raise LlmProviderError(
                f"claude cli timed out after {self.timeout:.0f}s"
            ) from e

        stdout = (out_b or b"").decode("utf-8", "replace")
        stderr = (err_b or b"").decode("utf-8", "replace")
        stderr_slice = stderr.strip()[:_STDERR_SLICE]

        if proc.returncode != 0:
            if self._looks_unauthenticated(stderr, proc.returncode):
                raise LlmProviderError(
                    "claude CLI is not authenticated - run `claude login` or "
                    "set ANTHROPIC_API_KEY"
                    + (f" (stderr: {stderr_slice})" if stderr_slice else "")
                )
            raise LlmProviderError(
                f"claude cli exited {proc.returncode}"
                + (f": {stderr_slice}" if stderr_slice else "")
            )

        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError) as e:
            # Some auth failures print a plain-text banner to stdout with a
            # zero exit; treat an unauthenticated signal there as actionable.
            if self._looks_unauthenticated(stdout, proc.returncode):
                raise LlmProviderError(
                    "claude CLI is not authenticated - run `claude login` or "
                    "set ANTHROPIC_API_KEY"
                ) from e
            raise LlmProviderError(
                "claude cli did not return JSON on stdout "
                f"(--output-format json expected): {stdout[:_STDERR_SLICE]!r}"
            ) from e

        if not isinstance(data, dict) or "result" not in data:
            raise LlmProviderError(
                "claude cli JSON had no `result` field "
                f"(keys={list(data)[:10] if isinstance(data, dict) else type(data)!r})"
            )
        if data.get("is_error"):
            raise LlmProviderError(
                f"claude cli reported an error result: "
                f"{str(data.get('result') or '')[:_STDERR_SLICE]}"
            )
        result = data.get("result")
        if not isinstance(result, str) or not result.strip():
            raise LlmProviderError("claude cli returned an empty `result`")
        # Downstream _parse_json_response tolerates markdown fences + prose,
        # so return the raw model text unmodified.
        return result

    @staticmethod
    def _looks_unauthenticated(text: str, returncode: int) -> bool:
        low = (text or "").lower()
        return any(sig in low for sig in _AUTH_SIGNALS)


def claude_cli_default() -> ClaudeCliProvider:
    """Env-driven factory (mirrors anthropic_default()).

    Reads MAGI_CP_CLAUDE_CLI_MODEL for the model override; unset => CLI default.
    Does NOT probe auth (kept cheap so it is safe to call at boot).
    """
    return ClaudeCliProvider()


__all__ = [
    "ClaudeCliProvider",
    "claude_cli_available",
    "claude_cli_default",
]
