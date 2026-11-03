"""Claude Code 5-hour usage for the Improve panel, readable on demand.

The reading is the server's own number: every Claude Code API reply carries the
plan's rate-limit state, and `claude -p --output-format stream-json` passes it on
as a rate_limit_event (unifiedWindows.five_hour.utilization + resetsAt). Improve
jobs already save that event to improve-logs/usage.json; when that copy is more
than MAX_AGE seconds old, current() makes one tiny Haiku call (no tools, no hooks,
no MCP, no session saved) to get a fresh one. At most one probe per MAX_AGE.

    GET /api/improve/usage   -> {"utilization", "resets_at", "status", "seen", "source"}
                                or {"error": "<why there is no reading>"}
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# same LOGDIR derivation as improve.py: honour IMPROVE_STATE / IMPROVE_LOGS so a job's own
# usage.json (written under improve.py's LOGDIR) is the one we read (finding 33)
_state = Path(os.environ.get("IMPROVE_STATE") or _HERE / "improvements.json").resolve()
_default_logs = _HERE / "improve-logs" if _state == (_HERE / "improvements.json").resolve() \
    else _state.with_name(_state.stem + "-logs")
LOGDIR = Path(os.environ.get("IMPROVE_LOGS") or _default_logs).resolve()
USAGE = LOGDIR / "usage.json"
MAX_AGE = 60
_lock = threading.Lock()
_last_try = 0.0
_last_error = None


def saved():
    try:
        return json.loads(USAGE.read_text())
    except (OSError, ValueError):
        return None


def note(info, source):
    """Store a rate_limit_info dict's five_hour window; returns the reading or None."""
    window = ((info or {}).get("unifiedWindows") or {}).get("five_hour")
    if not window or window.get("utilization") is None:
        return None
    usage = {"utilization": window["utilization"], "resets_at": window.get("resetsAt"),
             "status": info.get("status"), "seen": time.time(), "source": source}
    USAGE.parent.mkdir(exist_ok=True)
    USAGE.write_text(json.dumps(usage))
    return usage


def _probe():
    exe = shutil.which("claude") or "/opt/homebrew/bin/claude"
    cmd = [exe, "-p", "Reply with: ok", "--output-format", "stream-json", "--verbose", "--model", "haiku",
           "--max-turns", "1", "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
           "--no-session-persistence", "--settings", '{"disableAllHooks": true}']
    env = {k: v for k, v in os.environ.items()  # same filter improve.py uses for its jobs
           if not (k in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "AI_AGENT", "CLAUDE_CODE_MESSAGING_TOKEN")
                   or (k.startswith("CLAUDE_CODE_") and "TOKEN" not in k and not k.startswith("CLAUDE_CODE_USE_")))}
    with tempfile.TemporaryDirectory() as cwd:
        try:
            p = subprocess.run(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=60)
        except FileNotFoundError:
            raise RuntimeError("the claude command isn't installed on this machine")
        except subprocess.TimeoutExpired:
            raise RuntimeError("Claude Code didn't answer within 60 s")
    last = ""
    for raw in p.stdout.splitlines():
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if ev.get("type") == "rate_limit_event":
            got = note(ev.get("rate_limit_info"), "check")
            if got:
                return got
        if ev.get("type") == "result":
            last = str(ev.get("result") or "")
    why = (last or p.stderr or p.stdout).strip().splitlines()
    raise RuntimeError("Claude Code gave no 5-hour reading" + (f": {why[-1][:160]}" if why else
                       " (it reports one only when logged in with a Claude subscription)"))


def current():
    """Latest reading, refreshed with a probe when it is older than MAX_AGE (one probe at a time)."""
    global _last_try, _last_error
    u = saved()
    if u and time.time() - u.get("seen", 0) < MAX_AGE:
        return u
    with _lock:
        u = saved()
        if u and time.time() - u.get("seen", 0) < MAX_AGE:
            return u
        if time.time() - _last_try >= MAX_AGE:
            _last_try = time.time()
            try:
                u, _last_error = _probe(), None
                return u
            except Exception as e:  # noqa: BLE001 - any failure becomes the line the panel shows
                _last_error = str(e)
    if u:
        return dict(u, error=_last_error) if _last_error else u
    return {"error": _last_error or "no reading yet"}
