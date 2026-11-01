"""Improvements box ("liquid software"): type how the software should be different,
and a headless Claude Code run implements it as exactly one git commit.

    POST /api/improve              {"request": "...", "target": "poke-routes" | "rom"}
    GET  /api/improve              history (newest first) + targets
    GET  /api/improve/<id>?since=N status and log lines from N on
    POST /api/improve/<id>/undo    queue a git revert of that job's commit

One job at a time, FIFO (undo jobs share the queue). History lives in
webui/improvements.json, full logs in webui/improve-logs/ (both gitignored).
The server is bound to 127.0.0.1; on top of that every /api/improve request must
come from a same-origin page (Host and Origin checked, JSON content type) because
these endpoints run an unattended agent.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
POKE = HERE.parent
ARK = Path("/Users/aryan/Desktop/experiments/arklarna")
ROM = ARK / "rom"
ROM_BRANCH = "arklarna"
RELEASE_ROM = ARK / "release/v1/arklarna-v1.gba"
TOOLCHAIN = Path.home() / "toolchains/arm-gnu-toolchain-14.2.rel1-darwin-arm64-arm-none-eabi/bin"
DOCS = ARK / "documenting-agent-work"
HISTORY = HERE / "improvements.json"
LOGDIR = HERE / "improve-logs"
TIMEOUT = 20 * 60
AUTHOR = ("AdalricP", "78260488+AdalricP@users.noreply.github.com")

TARGETS = {
    "poke-routes": {"label": "Generator & web UI", "repo": POKE, "branch": None},
    "rom": {"label": "ROM (Arklarna)", "repo": ROM, "branch": ROM_BRANCH},
}

# Tools the unattended run may never use, even in bypassPermissions mode (deny rules win).
DENY = ["Bash(git push:*)", "Bash(git push *)", "Bash(*git push*)", "Bash(git remote:*)",
        "Bash(git config:*)", "Bash(gh:*)", "Bash(sudo:*)", "Bash(rm -rf /*)", "Bash(rm -rf ~*)",
        "Bash(curl * -X POST*)", "Bash(npm publish:*)", "Bash(vercel:*)"]

_lock = threading.RLock()
_jobs: dict = {}          # id -> job dict (persisted, minus live log)
_logs: dict = {}          # id -> list[str] (live)
_q: "queue.Queue[str]" = queue.Queue()
_worker = None
_current = {"id": None, "proc": None}


# ------------------------------------------------------------------ persistence

def _load():
    if HISTORY.exists():
        try:
            for j in json.loads(HISTORY.read_text()):
                if j.get("status") in ("queued", "running"):
                    j["status"] = "failed"
                    j["error"] = "server restarted while the job was queued or running"
                _jobs[j["id"]] = j
        except (ValueError, KeyError):
            pass


def _save():
    with _lock:
        data = sorted(_jobs.values(), key=lambda j: j["created"])
        tmp = HISTORY.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(HISTORY)


def _log(jid, line):
    line = line.rstrip("\n")
    with _lock:
        _logs.setdefault(jid, []).append(line)
    LOGDIR.mkdir(exist_ok=True)
    with open(LOGDIR / f"{jid}.log", "a") as f:
        f.write(line + "\n")


def _lines(jid):
    with _lock:
        if jid in _logs:
            return list(_logs[jid])
    p = LOGDIR / f"{jid}.log"
    return p.read_text().splitlines() if p.exists() else []


def _worklog(text):
    """One line in the owner's daily agent log."""
    try:
        p = DOCS / "log" / f"{time.strftime('%Y-%m-%d')}.md"
        with open(p, "a") as f:
            f.write(f"- {time.strftime('%H:%M')} {text}\n")
    except OSError:
        pass


# ------------------------------------------------------------------ git helpers

def _git(repo, *args, env=None, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def _head(target):
    t = TARGETS[target]
    return _git(t["repo"], "rev-parse", t["branch"] or "HEAD")


def _git_env():
    env = dict(os.environ)
    env.update(GIT_AUTHOR_NAME=AUTHOR[0], GIT_AUTHOR_EMAIL=AUTHOR[1],
               GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1])
    return env


# ------------------------------------------------------------------ prompts

RULES = f"""Standing rules from the owner (obey them):
- Read {DOCS}/decisions.md and {DOCS}/AGENT_BRIEF.md first. Later entries override earlier ones.
- Route design: linear and branching (split -> lanes -> converge), straight orthogonal paths for a d-pad,
  special Pokemon areas rare and hidden, never next to the start village or route entrances. Every feature has a reason.
- Art direction: must read as Pokemon (DS/Unbound look). No LPC/RPG-Maker art, no stock Emerald look, no ripping
  assets from other hacks' ROMs, never download ROMs. Credit any third-party art in a CREDITS.md.
- Less is more. Make the smallest change that does what was asked, and look at the result yourself.

Hard limits for this unattended run:
- Only touch files inside the working directory {{scope}}. Do not read or write secrets, credentials or dotfiles
  in the home directory. Do not touch other repos, other branches or other worktrees.
- No network-facing changes: do not bind anything to 0.0.0.0, open ports, add remote calls or telemetry.
- Never push, never add or change git remotes, never change git config, never delete the repo or its history,
  never rewrite history (no reset --hard of commits you did not make, no force anything), never commit secrets.
- Do not stop or restart the web UI server on port 8790; it is running this job.
- Don't sign up for accounts or install global software.
- Other agents may be working in the same tree. Run `git status` first and leave changes you did not make alone:
  never stage, commit, revert, stash or check out them. Stage only your own files by path (`git add <paths>`),
  never `git add -A`, `git add .` or `git commit -a`.
- If the request is unsafe, impossible or would break these rules, make no commit and explain why in your last message.
"""


def build_prompt(job) -> str:
    jid, req, target = job["id"], job["request"].strip(), job["target"]
    if target == "poke-routes":
        return f"""You are implementing an improvement the owner typed into the "Improve" box of the poke-routes web UI.

Owner's request:
<<<
{req}
>>>

Context: {POKE} is poke-routes, a rough procedural Pokemon Emerald route generator (Python, package pokeroutes/)
with a stdlib web UI in webui/ (server.py, static/index.html, app.js, style.css; served at http://127.0.0.1:8790).
It is an experiment log, not a product: keep the lab-notebook tone in any docs you touch (see ../AGENTS.md).
The generator modules are hot-reloaded by the running server; static files are read per request. If you change
webui/*.py, say in your last message that the server needs a restart.

{RULES.format(scope=POKE)}
How to verify before committing (do all that apply, and fix what breaks):
- python3 -m pokeroutes generate examples/bernsteinweg.yaml (and erlenbruch.yaml, kiefernheide.yaml) all succeed.
- Web UI checks: `node --check` on every changed .js file; start a second copy with
  `PORT=8799 python3 webui/server.py` in the background, then
  `curl -s localhost:8799/api/spec-schema` and
  `curl -s -X POST -H 'Content-Type: application/json' -d '{{"seed":7}}' localhost:8799/api/generate`
  must return JSON without "error"; then kill that copy (only the one on 8799).
- If the change is visual, render or screenshot it and look at it.

Finish with exactly one git commit containing all your changes (no other commits), message starting with
"improve: " followed by a short summary of the request, e.g. "improve: show walkable percentage in stats".
Commit with: git -c user.name="{AUTHOR[0]}" -c user.email="{AUTHOR[1]}" commit ...
Do not commit files under out/, region_out/, webui/improvements.json or webui/improve-logs/.
Last message: 1-3 sentences on what changed and how you verified it.
"""
    wt = ARK / "rom-wt" / f"improve-{jid}"
    br = f"arklarna/improve-{jid}"
    return f"""You are implementing an improvement the owner typed into the "Improve" box, targeting the Arklarna ROM hack.

Owner's request:
<<<
{req}
>>>

Context: {ROM} is Arklarna, a Pokemon ROM hack on pokeemerald-expansion (decomp), branch `{ROM_BRANCH}`.
Current phase is ASSETS ONLY: maps, NPCs, tilesets, sprites, text. No engine/C logic changes. If the request
needs engine logic, make no commit and say so.

{RULES.format(scope=f"{wt} (and, for the final merge only, {ROM} plus copying the ROM to {RELEASE_ROM})")}
Workflow:
1. cd {ROM} && git worktree add {wt} -b {br} {ROM_BRANCH}   then work only in {wt}.
2. Build: export PATH="{TOOLCHAIN}:$PATH"; make -j10   (in {wt}). Fix errors until it builds.
3. Screenshot from a fresh game with {ARK}/tools/romshot/romshot.sh (read its README.md; e.g.
   romshot.sh {wt}/pokeemerald.gba /tmp/improve-{jid} --script {ARK}/tools/romshot/newgame_to_overworld.txt --scale 2),
   plus whatever scripted walk shows the change. Look at the PNGs. Copy the useful ones to
   {DOCS}/screens/improve-{jid}-*.png.
4. Commit on {br}.
5. ONLY if the build passed and the screenshots look right: cd {ROM} (it must be on {ROM_BRANCH} with a clean tree;
   if not, stop and say so), then
   git merge --squash {br} && git commit -m "improve: <short summary of the request>"
   so `{ROM_BRANCH}` gains exactly one new non-merge commit. Then build in {ROM} (same PATH, make -j10) and
   cp {ROM}/pokeemerald.gba {RELEASE_ROM}.
   If the build failed, do not touch {ROM_BRANCH} or the release ROM; leave the branch for the owner.
6. Remove the worktree when merged (git worktree remove {wt}); keep the branch.
Last message: 1-3 sentences: what changed, build y/n, merged y/n, screenshot paths.
"""


# ------------------------------------------------------------------ running claude

def _claude():
    return shutil.which("claude") or "/opt/homebrew/bin/claude"


def claude_cmd(prompt, cwd_target):
    add = [str(ARK / "rom-wt"), str(DOCS), str(RELEASE_ROM.parent), str(ARK / "tools")] if cwd_target == "rom" else []
    cmd = [_claude(), "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "bypassPermissions", "--strict-mcp-config", "--no-chrome",
           "--disallowedTools", *DENY]
    for d in add:
        cmd += ["--add-dir", d]
    return cmd


def _child_env(target):
    env = {k: v for k, v in os.environ.items()
           if not (k == "CLAUDECODE" or k in ("CLAUDE_PID", "CLAUDE_EFFORT", "AI_AGENT")
                   or (k.startswith("CLAUDE_CODE_") and "TOKEN" not in k and not k.startswith("CLAUDE_CODE_USE_"))
                   or k == "CLAUDE_CODE_MESSAGING_TOKEN")}
    if target == "poke-routes":
        env.update(GIT_AUTHOR_NAME=AUTHOR[0], GIT_AUTHOR_EMAIL=AUTHOR[1],
                   GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1])
    env["PATH"] = f"{TOOLCHAIN}:{env.get('PATH', '')}"
    return env


def _short(v, n=160):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _fmt_event(ev):
    """stream-json event -> readable log lines."""
    t = ev.get("type")
    out = []
    if t == "system" and ev.get("subtype") == "init":
        out.append(f"[init] model {ev.get('model', '?')}, cwd {ev.get('cwd', '?')}")
    elif t == "assistant":
        for c in (ev.get("message") or {}).get("content") or []:
            if c.get("type") == "text" and c.get("text", "").strip():
                out.append(c["text"].strip())
            elif c.get("type") == "tool_use":
                inp = c.get("input") or {}
                arg = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("description") or inp
                out.append(f"> {c.get('name')}: {_short(arg)}")
    elif t == "user":
        for c in (ev.get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                cont = c.get("content")
                if isinstance(cont, list):
                    cont = " ".join(x.get("text", "") for x in cont if isinstance(x, dict))
                out.append(f"  ! {_short(cont or '')}")
    elif t == "result":
        out.append(f"[done] {ev.get('subtype', '')}, {round((ev.get('duration_ms') or 0) / 1000)} s, "
                   f"{ev.get('num_turns', '?')} turns")
        if ev.get("result"):
            out.append(str(ev["result"]).strip())
    return out


def _run_agent(job):
    jid, target = job["id"], job["target"]
    prompt = build_prompt(job)
    LOGDIR.mkdir(exist_ok=True)
    (LOGDIR / f"{jid}.prompt.txt").write_text(prompt)
    cmd = claude_cmd(prompt, target)
    _log(jid, f"[run] {Path(cmd[0]).name} -p <prompt> " + " ".join(a for a in cmd[3:] if "\n" not in a))
    proc = subprocess.Popen(cmd, cwd=str(TARGETS[target]["repo"]), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, env=_child_env(target), start_new_session=True)
    _current["proc"] = proc
    timer = threading.Timer(TIMEOUT, lambda: _kill(proc, jid, "timeout"))
    timer.start()
    final = ""
    try:
        for raw in proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                _log(jid, raw)
                continue
            if ev.get("type") == "result":
                final = str(ev.get("result") or "")
            for line in _fmt_event(ev):
                _log(jid, line)
        proc.wait()
    finally:
        timer.cancel()
        _current["proc"] = None
    return proc.returncode, final


def _kill(proc, jid, why):
    if proc.poll() is None:
        _log(jid, f"[{why}] stopping the agent")
        with _lock:
            _jobs[jid]["killed"] = why
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            time.sleep(5)
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _do_improve(job):
    jid, target = job["id"], job["target"]
    before = _head(target)
    job["base"] = before
    code, final = _run_agent(job)
    after = _head(target)
    repo = TARGETS[target]["repo"]
    commits = _git(repo, "rev-list", "--reverse", "--no-merges", f"{before}..{after}").split() if after != before else []
    # other agents may commit in the same repo while the job runs: keep only the job's own "improve: " commit(s)
    mine = [c for c in commits if _git(repo, "log", "-1", "--format=%s", c).startswith("improve: ")]
    if len(mine) != len(commits):
        _log(jid, f"[note] {len(commits) - len(mine)} other commit(s) landed meanwhile; not counted as this job's")
    commits = mine
    job["commits"] = commits
    job["summary"] = final[-600:]
    if commits:
        job["commit"] = commits[-1]
        job["subject"] = _git(TARGETS[target]["repo"], "log", "-1", "--format=%s", commits[-1])
        if len(commits) > 1:
            _log(jid, f"[warn] {len(commits)} commits instead of one; Undo reverts all of them")
        changed = sorted({f for c in commits for f in _git(repo, "show", "--name-only", "--format=", c).split()})
        job["files"] = changed[:60]
        if target == "poke-routes" and any(f.startswith("webui/") and f.endswith(".py") for f in changed):
            job["restart_needed"] = True
            _log(jid, "[note] webui/*.py changed: restart the web UI to load it")
    if job.get("killed"):
        job["status"] = "timeout" if job["killed"] == "timeout" else "cancelled"
    elif not commits:
        job["status"] = "no-change" if code == 0 else "failed"
    else:
        job["status"] = "done"
    _log(jid, f"[status] {job['status']}" + (f", commit {job['commit'][:10]}" if job.get("commit") else ""))
    return job


def _do_undo(job):
    """Revert the target job's commits as one commit; for the ROM, rebuild and recopy the release."""
    jid = job["id"]
    src = _jobs[job["undo_of"]]
    t = TARGETS[src["target"]]
    repo = t["repo"]
    if t["branch"] and _git(repo, "branch", "--show-current") != t["branch"]:
        raise RuntimeError(f"{repo} is not on {t['branch']}; not reverting")
    commits = src.get("commits") or [src["commit"]]
    if _git(repo, "diff", "--cached", "--name-only"):
        raise RuntimeError(f"{repo} has staged changes; not reverting")
    touched = set(_git(repo, "diff", "--name-only", f"{commits[0]}~1", commits[-1]).split())
    dirty = touched & set(_git(repo, "diff", "--name-only").split())
    if dirty:
        raise RuntimeError("uncommitted edits to files this improvement touched: " + ", ".join(sorted(dirty)))
    env = _git_env() if src["target"] == "poke-routes" else None
    try:
        for c in reversed(commits):
            _log(jid, f"> git revert --no-commit {c[:10]}")
            _git(repo, "revert", "--no-commit", c, env=env)
        msg = f"Revert \"{src.get('subject') or 'improve: ' + src['request'][:60]}\"\n\nThis reverts " + \
              ", ".join(c[:12] for c in commits) + f" (improvement {src['id']})."
        _git(repo, "commit", "-m", msg, env=env)
    except Exception:
        _git(repo, "revert", "--abort", check=False)
        raise
    job["commit"] = _git(repo, "rev-parse", "HEAD")
    _log(jid, f"[revert] {job['commit'][:10]}")
    if src["target"] == "rom":
        _log(jid, "> make -j10 (rebuilding the ROM after the revert)")
        env = dict(os.environ, PATH=f"{TOOLCHAIN}:{os.environ.get('PATH', '')}")
        r = subprocess.run(["make", "-j10"], cwd=str(repo), env=env, capture_output=True, text=True, timeout=TIMEOUT)
        for line in (r.stdout + r.stderr).splitlines()[-15:]:
            _log(jid, line)
        if r.returncode:
            raise RuntimeError("ROM build after the revert failed; release ROM left as it was")
        shutil.copy2(repo / "pokeemerald.gba", RELEASE_ROM)
        _log(jid, f"[copy] {RELEASE_ROM}")
    src["status"] = "undone"
    src["undone_by"] = jid
    if src["target"] == "poke-routes":
        changed = _git(repo, "diff", "--name-only", f"{job['commit']}~1..{job['commit']}").split()
        if any(f.startswith("webui/") and f.endswith(".py") for f in changed):
            job["restart_needed"] = True
    job["status"] = "done"
    return job


def _work():
    while True:
        jid = _q.get()
        with _lock:
            job = _jobs[jid]
            if job["status"] == "cancelled":
                continue
            job["status"] = "running"
            job["started"] = time.time()
            _current["id"] = jid
        _save()
        try:
            (_do_undo if job["kind"] == "undo" else _do_improve)(job)
        except Exception as e:
            job["status"] = "failed"
            job["error"] = f"{type(e).__name__}: {e}"
            _log(jid, f"[error] {job['error']}")
        finally:
            job["finished"] = time.time()
            _current["id"] = None
            _save()
            _worklog_job(job)


def _worklog_job(job):
    mins = round((job["finished"] - (job.get("started") or job["finished"])) / 60, 1)
    if job["kind"] == "undo":
        src = _jobs.get(job["undo_of"], {})
        _worklog(f"Improve box: undo of {src.get('id')} ({src.get('target')}) -> {job['status']}"
                 + (f", revert {job['commit'][:10]}" if job.get("commit") else "") + (f": {job['error']}" if job.get("error") else ""))
        return
    req = " ".join(job["request"].split())[:140]
    _worklog(f"Improve box ({job['target']}, {mins} min): \"{req}\" -> {job['status']}"
             + (f", commit {job['commit'][:10]} on {TARGETS[job['target']]['branch'] or 'poke-routes'}" if job.get("commit") else "")
             + (f": {job['error']}" if job.get("error") else ""))


def _ensure_worker():
    global _worker
    with _lock:
        if _worker is None:
            _load()
            _worker = threading.Thread(target=_work, name="improve-worker", daemon=True)
            _worker.start()


# ------------------------------------------------------------------ api

def submit(request: str, target: str) -> dict:
    _ensure_worker()
    request = (request or "").strip()
    if not request:
        raise ValueError("request is empty")
    if len(request) > 4000:
        raise ValueError("request is longer than 4000 characters")
    if target not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}")
    jid = time.strftime("%m%d%H%M") + "-" + uuid.uuid4().hex[:4]
    job = {"id": jid, "kind": "improve", "request": request, "target": target, "status": "queued",
           "created": time.time()}
    with _lock:
        _jobs[jid] = job
    _save()
    _q.put(jid)
    return public(job)


def undo(jid: str) -> dict:
    _ensure_worker()
    with _lock:
        src = _jobs.get(jid)
        if not src or src["kind"] != "improve":
            raise KeyError(jid)
        if src["status"] != "done" or not src.get("commit"):
            raise ValueError(f"nothing to undo (status {src['status']})")
        if any(j.get("undo_of") == jid and j["status"] in ("queued", "running") for j in _jobs.values()):
            raise ValueError("an undo for this job is already queued")
        uid = jid + "-undo"
        n = 1
        while uid in _jobs:
            n += 1
            uid = f"{jid}-undo{n}"
        job = {"id": uid, "kind": "undo", "undo_of": jid, "request": f"undo: {src['request'][:80]}",
               "target": src["target"], "status": "queued", "created": time.time()}
        _jobs[uid] = job
    _save()
    _q.put(uid)
    return public(job)


def cancel(jid: str) -> dict:
    with _lock:
        job = _jobs.get(jid)
        if not job:
            raise KeyError(jid)
        if job["status"] == "queued":
            job["status"] = "cancelled"   # the worker skips it below
        elif job["status"] == "running" and _current["id"] == jid and _current["proc"]:
            threading.Thread(target=_kill, args=(_current["proc"], jid, "cancelled"), daemon=True).start()
    _save()
    return public(job)


def public(job, since=None) -> dict:
    d = {k: v for k, v in job.items() if k not in ("summary",)}
    d["summary"] = job.get("summary", "")
    d["target_label"] = TARGETS[job["target"]]["label"]
    if since is not None:
        lines = _lines(job["id"])
        d["log"] = lines[since:]
        d["log_next"] = len(lines)
    return d


def history() -> dict:
    _ensure_worker()
    with _lock:
        jobs = sorted(_jobs.values(), key=lambda j: j["created"], reverse=True)
        return {"jobs": [public(j) for j in jobs[:100]], "running": _current["id"],
                "queued": _q.qsize(), "targets": {k: v["label"] for k, v in TARGETS.items()}}


# ------------------------------------------------------------------ http glue

_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _same_origin(h) -> bool:
    host = (h.headers.get("Host") or "").rsplit(":", 1)[0]
    if host not in _HOSTS:
        return False
    origin = h.headers.get("Origin")
    if origin:
        m = re.match(r"^https?://([^/:]+|\[::1\])(:\d+)?$", origin)
        if not m or m.group(1) not in _HOSTS:
            return False
    return True


def handle(h, method: str, u) -> None:
    """Dispatch /api/improve*; h is the BaseHTTPRequestHandler (uses its _json)."""
    if not _same_origin(h):
        return h._json({"error": "improvements only accept requests from this machine's own page"}, 403)
    parts = [p for p in u.path.split("/") if p][2:]  # after api/improve
    try:
        if method == "GET":
            if not parts:
                return h._json(history())
            job = _jobs.get(parts[0])
            if not job:
                return h._json({"error": "unknown job"}, 404)
            from urllib.parse import parse_qs
            since = int((parse_qs(u.query).get("since") or ["0"])[0] or 0)
            return h._json(public(job, since=max(0, since)))
        if (h.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return h._json({"error": "Content-Type must be application/json"}, 415)
        n = int(h.headers.get("Content-Length") or 0)
        body = json.loads(h.rfile.read(n) or b"{}") if n else {}
        if not parts:
            return h._json(submit(str(body.get("request", "")), str(body.get("target", "poke-routes"))), 202)
        if len(parts) == 2 and parts[1] == "undo":
            return h._json(undo(parts[0]), 202)
        if len(parts) == 2 and parts[1] == "cancel":
            return h._json(cancel(parts[0]))
        return h._json({"error": "not found"}, 404)
    except KeyError:
        return h._json({"error": "unknown job"}, 404)
    except ValueError as e:
        return h._json({"error": str(e)}, 400)
