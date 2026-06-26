---
name: share
description: Create a public Magi share link for the CURRENT Claude Code session, governed and redacted, served from our cloud. User-invoked only.
disable-model-invocation: true
allowed-tools: Bash(magi-cp share:*)
---

The user wants to share THIS Claude Code session as a public, governed run page.

Run exactly this command, with the current session id substituted in (do not
change it, do not pick a different session):

```
magi-cp share "${CLAUDE_SESSION_ID}"
```

Notes:
- The command reads this session's transcript, builds an `openmagi.runView.v1`
  view, redacts it (allowlist fail-closed), and uploads it to the configured
  cloud (`MAGI_CP_CLOUD_URL` / `MAGI_CP_API_KEY`, already set by the installer).
  It exports the WHOLE session up to now; trim it afterward from the dashboard.
- It prints the public URL to stdout and a "review before sharing" note to stderr.

After it runs, reply with ONLY:
- one short line: `Public share link (review before sharing):`
- the `https://.../r/...` URL on its own line.

Do not summarize the run, do not add commentary. If the command fails (e.g.
missing `MAGI_CP_API_KEY`), report the error verbatim and stop.
