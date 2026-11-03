"""Improvements box ("liquid software"): type how the software should be different,
and a headless Claude Code run implements it as exactly one git commit.

    POST /api/improve              {"request": "...", "target": "poke-routes" | "rom",
                                    "screenshot": optional "data:image/png;base64,...",
                                    "mode": "improve" (default) | "agent"}
    GET  /api/improve              history (newest first), worker slots, owner, targets, usage
    GET  /api/improve/<id>?since=N status and log lines from N on
    POST /api/improve/<id>/undo    queue a git revert of that job's commit
    POST /api/improve/<id>/cancel  cancel a queued or running job
    POST /api/improve/workers      {"workers": n}  how many jobs run at once (1-6)

"agent" jobs come from the shell's Agent tab: a task done *for* the owner in the tool
("get this tileset", "apply that tileset") instead of a change to the software.
Same queue, same one-commit-and-Undo rule, only the prompt's opening differs.

Several workers run in parallel (default 3). A poke-routes job gets its own git worktree
(../poke-routes-wt/job-<id>, branch improve/job-<id>) made from main; once its commit is
there, a merge step under a global lock rebases it onto the latest main and fast-forwards main
(and the repo-root checkout the server serves from). A conflict re-runs the job once on fresh
main; a second conflict leaves the branch for the owner. ROM jobs keep their own worktree flow
in the ROM repo and only one runs at a time. No job starts while free + inactive memory is
under ~0.8 GB.

Only one server process runs workers: it holds an fcntl lock on webui/improvements.json.lock.
Other servers show the queue read-only (from the state file) and forward submit / undo /
cancel / workers to the lock holder. The queue lives in webui/improvements.json; jobs that were
running when their server died are requeued once on the next start. Logs: webui/improve-logs/.
The server is bound to 127.0.0.1; on top of that every /api/improve request must come from a
same-origin page (Host and Origin checked, JSON content type) because these endpoints run an
unattended agent.

Env (mostly for testing on a throwaway clone): IMPROVE_REPO, IMPROVE_STATE, IMPROVE_LOGS,
IMPROVE_WT_ROOT, IMPROVE_WORKERS (0 = never run workers), IMPROVE_CLAUDE, IMPROVE_WORKLOG=0,
IMPROVE_MEM_MIN_MB, IMPROVE_TIMEOUT, IMPROVE_ROOT_WAIT, IMPROVE_LEGACY_GRACE.
"""
from __future__ import annotations

import base64
import errno
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
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
AUTHOR = ("AdalricP", "78260488+AdalricP@users.noreply.github.com")


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


REPO = Path(os.environ.get("IMPROVE_REPO") or POKE).resolve()   # the poke-routes repo jobs change
MAIN = "main"
STATE = Path(os.environ.get("IMPROVE_STATE") or HERE / "improvements.json").resolve()
HISTORY = STATE
_default_logs = HERE / "improve-logs" if STATE == (HERE / "improvements.json").resolve() \
    else STATE.with_name(STATE.stem + "-logs")
LOGDIR = Path(os.environ.get("IMPROVE_LOGS") or _default_logs).resolve()
USAGE = LOGDIR / "usage.json"
LOCKFILE = STATE.with_name(STATE.name + ".lock")
META = STATE.with_name(STATE.stem + ".meta.json")
WT_ROOT = Path(os.environ.get("IMPROVE_WT_ROOT") or REPO.parent / f"{REPO.name}-wt").resolve()
TIMEOUT = _env_int("IMPROVE_TIMEOUT", 20 * 60)
maximum_screenshot_size_in_bytes = 8 * 1024 * 1024
png_data_url_prefix = "data:image/png;base64,"
png_file_signature = b"\x89PNG\r\n\x1a\n"
MAX_WORKERS = 6
DEFAULT_WORKERS = 3
PORT_BASE = 8800                 # a job in slot s verifies with its own server copy on PORT_BASE + s
MEM_MIN_MB = _env_int("IMPROVE_MEM_MIN_MB", 800)
ROOT_WAIT = _env_int("IMPROVE_ROOT_WAIT", 120)    # seconds to wait for the root checkout to be clean
LEGACY_GRACE = _env_int("IMPROVE_LEGACY_GRACE", 20)
START_GAP = 5                    # seconds between job starts, so the last agent has allocated before we measure
WORKLOG = os.environ.get("IMPROVE_WORKLOG", "1") != "0"
LOST_ERR = "server restarted while the job was queued or running"

TARGETS = {
    "poke-routes": {"label": "Generator & web UI", "repo": REPO, "branch": None},
    "rom": {"label": "ROM (Arklarna)", "repo": ROM, "branch": ROM_BRANCH},
}

# Tools the unattended run may never use, even in bypassPermissions mode (deny rules win).
DENY = ["Bash(git push:*)", "Bash(git push *)", "Bash(*git push*)", "Bash(git remote:*)",
        "Bash(git config:*)", "Bash(gh:*)", "Bash(sudo:*)", "Bash(rm -rf /*)", "Bash(rm -rf ~*)",
        "Bash(npm publish:*)", "Bash(vercel:*)"]

# status -> statuses it may move to
ALLOWED = {
    "queued": {"running", "cancelled"},
    "running": {"merging", "done", "failed", "no-change", "timeout", "cancelled", "queued", "conflict"},
    "merging": {"done", "conflict", "failed", "queued", "running"},   # running = re-run after a conflict
    "done": {"undone"},
    "failed": {"queued"},          # only the one-off recovery of wrongly failed jobs
    "conflict": set(), "cancelled": set(), "undone": set(), "no-change": set(), "timeout": set(),
}
ACTIVE = ("running", "merging")

_lock = threading.RLock()
_cv = threading.Condition(_lock)
_merge_lock = threading.Lock()    # one merge onto main at a time
_jobs: dict = {}                  # id -> job dict (persisted)
_logs: dict = {}                  # id -> list[str] (live, this process)
_procs: dict = {}                 # id -> Popen of its agent
_slots: dict = {}                 # slot -> job id or None
_threads: dict = {}               # slot -> worker thread
_me = os.getpid()
_st = {"owner": False, "port": None, "started": False, "workers": DEFAULT_WORKERS, "lockfd": None,
       "last_write": None, "last_start": 0.0, "mem": None, "mem_wait": False,
       "legacy": [], "legacy_idle_since": None, "disk_cache": (None, [])}
_meta: dict = {}


class TransitionError(ValueError):
    pass


def set_status(job, status, note=None, **fields):
    """The queue's state machine: every status change goes through here."""
    cur = job.get("status")
    if status != cur and status not in ALLOWED.get(cur, set()):
        raise TransitionError(f"job {job.get('id')}: {cur} -> {status} is not allowed")
    job["status"] = status
    job.update(fields)
    job["updated"] = time.time()
    if note:
        job.setdefault("notes", []).append(note)
    return job


# ------------------------------------------------------------------ persistence

def _read_disk():
    try:
        data = json.loads(STATE.read_text())
        return [j for j in data if isinstance(j, dict) and j.get("id")] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _mine(job):
    return job.get("owner") == _me


def _merge_disk_locked():
    """Take other processes' versions of jobs this process does not own (an old-style server may
    still be running its own queue and rewriting the file); keep ours for the ones we own."""
    for d in _read_disk():
        cur = _jobs.get(d["id"])
        if cur is None or not _mine(cur):
            _jobs[d["id"]] = d


def _save():
    with _lock:
        if not _st["owner"]:
            return       # only the lock holder writes the queue
        _merge_disk_locked()
        data = sorted(_jobs.values(), key=lambda j: j.get("created", 0))
        tmp = STATE.with_name(f".{STATE.stem}.{_me}.tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(STATE)
        try:
            _st["last_write"] = STATE.stat().st_mtime_ns
        except OSError:
            pass


def _load_meta():
    try:
        _meta.update(json.loads(META.read_text()))
    except (OSError, ValueError):
        pass


def _save_meta():
    tmp = META.with_name(f".{META.name}.{_me}.tmp")
    tmp.write_text(json.dumps(_meta, indent=1))
    tmp.replace(META)


def _log(jid, line):
    line = line.rstrip("\n")
    with _lock:
        _logs.setdefault(jid, []).append(line)
    LOGDIR.mkdir(parents=True, exist_ok=True)
    with open(LOGDIR / f"{jid}.log", "a") as f:
        f.write(line + "\n")


def _lines(jid):
    with _lock:
        if jid in _logs:
            return list(_logs[jid])
    p = LOGDIR / f"{jid}.log"
    return p.read_text(errors="replace").splitlines() if p.exists() else []


def _worklog(text):
    """One line in the owner's daily agent log."""
    if not WORKLOG:
        return
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


def _gitr(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)


def _head(target):
    t = TARGETS[target]
    return _git(t["repo"], "rev-parse", t["branch"] or "HEAD")


def _git_env():
    env = dict(os.environ)
    env.update(GIT_AUTHOR_NAME=AUTHOR[0], GIT_AUTHOR_EMAIL=AUTHOR[1],
               GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1])
    return env


def _committer_env():
    """Rebases and fast-forwards keep the job commit's author; only the committer is set."""
    env = dict(os.environ)
    env.update(GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1], GIT_EDITOR="true")
    return env


def _is_ancestor(repo, a, b):
    return _gitr(repo, "merge-base", "--is-ancestor", a, b).returncode == 0


# ------------------------------------------------------------------ merge step

def _sync_root(repo, log=lambda s: None):
    """If an earlier merge had to move main without updating the repo-root checkout (it had
    uncommitted edits to the merged files), try to bring the checkout up to main now: a two-tree
    read-tree is the same check-and-update a fast-forward does, and refuses on local edits."""
    stale = _meta.get("root_stale_from")
    if not stale:
        return True
    if _git(repo, "symbolic-ref", "-q", "HEAD", check=False) != f"refs/heads/{MAIN}":
        return False
    _gitr(repo, "update-index", "-q", "--refresh")      # stat info only, so unchanged files count as clean
    if _gitr(repo, "read-tree", "-m", "-u", stale, f"refs/heads/{MAIN}").returncode:
        return False
    _meta.pop("root_stale_from", None)
    _save_meta()
    log(f"[root] repo checkout caught up with {MAIN}")
    return True


def _advance_main(repo, old, new, log, root_wait):
    """Move main from old to new (never force). Returns (moved, root_synced, note); moved=False
    means main changed under us and the caller should rebase again."""
    head_ref = _git(repo, "symbolic-ref", "-q", "HEAD", check=False)
    if head_ref != f"refs/heads/{MAIN}":
        r = _gitr(repo, "update-ref", f"refs/heads/{MAIN}", new, old)
        return r.returncode == 0, None, ""
    synced = _sync_root(repo, log)
    deadline = time.time() + root_wait
    told = False
    while synced:
        r = _gitr(repo, "merge", "--ff-only", "-q", new, env=_committer_env())
        if r.returncode == 0:
            return True, True, ""
        if _git(repo, "rev-parse", f"refs/heads/{MAIN}") != old:
            return False, None, ""
        # an old-style server's agent may be editing the root and will commit from its index: moving
        # main under it would make that commit undo ours, so while one is busy we only ever wait
        if time.time() >= deadline and not _st["legacy"]:
            break
        if not told:
            log("[merge] the repo checkout has uncommitted edits to files this job changes; waiting "
                f"up to {root_wait} s: {' '.join((r.stderr or r.stdout).split())[:300]}")
            told = True
        time.sleep(min(5, max(0.2, root_wait / 10)))
    r = _gitr(repo, "update-ref", f"refs/heads/{MAIN}", new, old)
    if r.returncode:
        return False, None, ""
    if not _meta.get("root_stale_from"):
        _meta["root_stale_from"] = old
        _save_meta()
    note = (f"{MAIN} has this commit, but the checkout in {repo} was not updated because it has "
            f"uncommitted edits to the same files; it catches up on a later merge once they are gone")
    log("[merge] " + note)
    return True, False, note


def merge_onto_main(repo, wt, base, log=lambda s: None, root_wait=None):
    """Put the job's commit(s) base..HEAD of worktree wt on top of the latest main and fast-forward
    main to them. Never forces, never rewrites commits already on main.
    Returns {"ok", "head", "commits", "root_synced", "note"} or {"ok": False, "conflict": True,
    "files", "details"}."""
    root_wait = ROOT_WAIT if root_wait is None else root_wait
    with _merge_lock:
        for _ in range(10):
            old = _git(repo, "rev-parse", f"refs/heads/{MAIN}")
            if not _is_ancestor(wt, old, "HEAD"):
                log(f"> git rebase --onto {old[:10]} {base[:10]}")
                r = _gitr(wt, "rebase", "--onto", old, base, env=_committer_env())
                if r.returncode:
                    files = _git(wt, "diff", "--name-only", "--diff-filter=U", check=False).split()
                    _gitr(wt, "rebase", "--abort")
                    details = " ".join((r.stdout + " " + r.stderr).split())[:800]
                    return {"ok": False, "conflict": True, "files": files, "details": details}
                base = old
            new = _git(wt, "rev-parse", "HEAD")
            commits = _git(wt, "rev-list", "--reverse", f"{old}..{new}").split()
            moved, synced, note = _advance_main(repo, old, new, log, root_wait)
            if moved:
                log(f"[merge] {MAIN} {old[:10]} -> {new[:10]}")
                return {"ok": True, "head": new, "commits": commits, "root_synced": synced, "note": note}
            log(f"[merge] {MAIN} moved while merging; rebasing again")
        raise RuntimeError(f"{MAIN} kept moving; gave up merging")


def _make_worktree(job):
    wt = WT_ROOT / f"job-{job['id']}"
    br = f"improve/job-{job['id']}"
    _drop_worktree(wt, br)
    WT_ROOT.mkdir(parents=True, exist_ok=True)
    base = _git(REPO, "rev-parse", f"refs/heads/{MAIN}")
    _git(REPO, "worktree", "add", "-q", "-b", br, str(wt), base)
    # gitignored inputs the generator needs (the pret checkout) are shared by symlink
    for name in ("vendor",):
        src = REPO / name
        if src.is_dir() and not (wt / name).exists():
            (wt / name).symlink_to(src)
    job.update(worktree=str(wt), branch=br, base=base)
    return wt, br, base


def _drop_worktree(wt, br=None, keep_branch=False):
    wt = Path(wt)
    if wt.exists():
        _gitr(REPO, "worktree", "remove", "--force", str(wt))
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
    _gitr(REPO, "worktree", "prune")
    if br and not keep_branch and _gitr(REPO, "rev-parse", "-q", "--verify", f"refs/heads/{br}").returncode == 0:
        _gitr(REPO, "branch", "-D", br)


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
- Other agents may be working at the same time. Run `git status` first and leave changes you did not make alone:
  never stage, commit, revert, stash or check out them. Stage only your own files by path (`git add <paths>`),
  never `git add -A`, `git add .` or `git commit -a`.
- If the request is unsafe, impossible or would break these rules, make no commit and explain why in your last message.
"""


def describe_attached_screenshot_for_prompt(job) -> str:
    if not job.get("screenshot"):
        return ""

    screenshot_note = (f"\nThe owner attached a screenshot with the issues marked in red pen: {job['screenshot']}\n"
                       "Read it (it is a PNG) before starting; the red marks show what the request is about.\n")
    return screenshot_note


AGENT_TASK = f"""This came from the Agent tab, not the Improve box: the owner wants a task done for them in
the tool so they don't have to learn it (typically finding a tileset, or applying / switching one). Do the
task itself; don't build a UI feature for it unless they asked for one.
Tilesets: routes are tiled either with the learned Emerald General + secondary models (pokeroutes/models/,
pokeroutes/learn.py, `python3 -m pokeroutes learn`) or with the Ark rule tiler (pokeroutes/ruletile.py: ArkOutdoor +
ArkWoodland from {ROM}, built by tools/arklarna_tiles/build.py there; the web UI picks it via Generate > Tiler).
Getting a new tileset: only art its authors released for hacks (see AGENT_BRIEF.md), downloaded from them with
their stated terms (fetching that file is fine; adding network calls to the code is not). Keep it next to where
it is used with a CREDITS.md (author, URL, terms). Unclear terms, a login, or a terms page to accept: stop and
say exactly what the owner has to do. Stock Emerald art doesn't count as a new tileset.
"""


def build_prompt(job, wt=None, port=None) -> str:
    jid, target = job["id"], job["target"]
    req = job["request"].strip() + describe_attached_screenshot_for_prompt(job)
    agent = AGENT_TASK if job.get("mode") == "agent" else ""
    if target == "poke-routes":
        wt = Path(wt or REPO)
        port = port or PORT_BASE
        br = job.get("branch") or f"improve/job-{jid}"
        return f"""You are implementing an improvement the owner typed into the "Improve" box of the poke-routes web UI.

Owner's request:
<<<
{req}
>>>
{agent}
Context: {wt} is a git worktree of poke-routes ({REPO}) on branch `{br}`, made for this job from the latest main.
Work and commit only in {wt}. Other improve jobs run in parallel in their own worktrees; once you commit,
the queue rebases your commit onto main and the owner's server (it serves {REPO} on port 8790) picks it up.
poke-routes is a rough procedural Pokemon Emerald route generator (Python, package pokeroutes/)
with a stdlib web UI in webui/ (server.py; an editor shell: static/shell.html, shell.js, bench.js, shell-logic.js, shell.css).
It is an experiment log, not a product: keep the lab-notebook tone in any docs you touch (see ../AGENTS.md).
The generator modules are hot-reloaded by the running server; static files are read per request. If you change
webui/*.py, say in your last message that the server needs a restart.
Route tools (MCP server "routes", webui/route_tools.py, on this worktree's code): list_specs, spec_fields,
generate_route (spec + seed + overrides -> tiled/plan PNGs, report, an id), route_grid, paint_route (fix cells and
re-tile), generate_region, check_examples. Use them instead of hand-written scripts where they fit.

{RULES.format(scope=wt)}
How to verify before committing (do all that apply, and fix what breaks), all inside {wt}:
- python3 -m pokeroutes generate examples/bernsteinweg.yaml (and erlenbruch.yaml, kiefernheide.yaml) all succeed.
- Web UI checks: `node --check` on every changed .js file; start this job's own copy of the server with
  `IMPROVE_WORKERS=0 PORT={port} python3 webui/server.py` in the background (port {port} is this job's), then
  `curl -s 127.0.0.1:{port}/api/spec-schema` and
  `curl -s -X POST -H 'Content-Type: application/json' -d '{{"seed":7}}' 127.0.0.1:{port}/api/generate`
  must return JSON without "error"; then kill that copy (only the one on {port}).
- If the change is visual, render or screenshot it and look at it.

Finish with exactly one git commit on `{br}` containing all your changes (no other commits), message starting with
"improve: " followed by a short summary of the request, e.g. "improve: show walkable percentage in stats".
Commit with: git -c user.name="{AUTHOR[0]}" -c user.email="{AUTHOR[1]}" commit ...
Do not commit files under out/, region_out/, vendor/, webui/improvements.json or webui/improve-logs/.
Last message: 1-3 sentences on what changed and how you verified it.
"""
    wt = ARK / "rom-wt" / f"improve-{jid}"
    br = f"arklarna/improve-{jid}"
    return f"""You are implementing an improvement the owner typed into the "Improve" box, targeting the Arklarna ROM hack.

Owner's request:
<<<
{req}
>>>
{agent}
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
    return os.environ.get("IMPROVE_CLAUDE") or shutil.which("claude") or "/opt/homebrew/bin/claude"


def claude_cmd(prompt, cwd_target, wt=None):
    # the job's worktree is the cwd; the log dir holds an attached screenshot
    add = [str(ARK / "rom-wt"), str(DOCS), str(RELEASE_ROM.parent), str(ARK / "tools"), str(LOGDIR)] \
        if cwd_target == "rom" else [str(LOGDIR)]
    cmd = [_claude(), "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "bypassPermissions", "--strict-mcp-config", "--no-chrome",
           "--model", os.environ.get("IMPROVE_MODEL", "claude-sonnet-5")]
    tools = Path(wt or REPO) / "webui" / "route_tools.py"
    if cwd_target == "poke-routes" and tools.exists():   # route tools, run on the worktree's own code
        cmd += ["--mcp-config", json.dumps({"mcpServers": {"routes": {"command": sys.executable, "args": [str(tools)]}}})]
    cmd += ["--disallowedTools", *DENY]
    for d in add:
        cmd += ["--add-dir", d]
    return cmd


def _child_env(target):
    env = {k: v for k, v in os.environ.items()
           if not (k == "CLAUDECODE" or k in ("CLAUDE_PID", "CLAUDE_EFFORT", "AI_AGENT", "PORT")
                   or k.startswith("IMPROVE_")
                   or (k.startswith("CLAUDE_CODE_") and "TOKEN" not in k and not k.startswith("CLAUDE_CODE_USE_"))
                   or k == "CLAUDE_CODE_MESSAGING_TOKEN")}
    env["IMPROVE_WORKERS"] = "0"     # a server copy started by the job never runs the queue
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


def _note_usage(ev):
    """Keep the 5-hour window from a rate_limit_event (Claude Code emits one per run in stream-json)."""
    info = ev.get("rate_limit_info") or {}
    window = (info.get("unifiedWindows") or {}).get("five_hour")
    if not window or window.get("utilization") is None:
        return
    usage = {"utilization": window["utilization"], "resets_at": window.get("resetsAt"),
             "status": info.get("status"), "seen": time.time()}
    LOGDIR.mkdir(parents=True, exist_ok=True)
    USAGE.write_text(json.dumps(usage))


def _usage():
    try:
        return json.loads(USAGE.read_text())
    except (OSError, ValueError):
        return None


def _run_agent(job, cwd, wt=None, port=None):
    jid, target = job["id"], job["target"]
    prompt = build_prompt(job, wt=wt, port=port)
    LOGDIR.mkdir(parents=True, exist_ok=True)
    (LOGDIR / f"{jid}.prompt.txt").write_text(prompt)
    cmd = claude_cmd(prompt, target, wt)
    _log(jid, f"[run] {Path(cmd[0]).name} -p <prompt> " + " ".join(a for a in cmd[3:] if "\n" not in a)
         + f"  (cwd {cwd})")
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, env=_child_env(target), start_new_session=True)
    with _lock:
        _procs[jid] = proc
        job["agent_pid"] = proc.pid
    _save()
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
            if ev.get("type") == "rate_limit_event":
                _note_usage(ev)
            if ev.get("type") == "result":
                final = str(ev.get("result") or "")
            for line in _fmt_event(ev):
                _log(jid, line)
        proc.wait()
    finally:
        timer.cancel()
        with _lock:
            _procs.pop(jid, None)
            job.pop("agent_pid", None)
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


def _kill_orphan(pid, jid):
    """An agent left running by a server that died: stop it before its job runs again."""
    try:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    except OSError:
        return
    if "claude" not in cmd and Path(_claude()).name not in cmd:
        return
    _log(jid, f"[recover] stopping the agent (pid {pid}) left over from the previous server")
    try:
        os.killpg(pid, signal.SIGTERM)
        time.sleep(2)
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


# ------------------------------------------------------------------ jobs

def _note_changes(job, repo, commits):
    changed = sorted({f for c in commits for f in _git(repo, "show", "--name-only", "--format=", c).split()})
    job["files"] = changed[:60]
    if job["target"] == "poke-routes" and any(f.startswith("webui/") and f.endswith(".py") for f in changed):
        job["restart_needed"] = True
        _log(job["id"], "[note] webui/*.py changed: restart the web UI to load it")


def _produce_revert(job, wt):
    """Undo of a poke-routes job: one revert commit in the job's worktree."""
    src = _jobs[job["undo_of"]]
    commits = src.get("commits") or [src["commit"]]
    for c in commits:
        if not _is_ancestor(REPO, c, f"refs/heads/{MAIN}"):
            raise RuntimeError(f"{c[:10]} is not on {MAIN}; nothing to revert")
    env = _git_env()
    for c in reversed(commits):
        _log(job["id"], f"> git revert --no-commit {c[:10]}")
        r = _gitr(wt, "revert", "--no-commit", c, env=env)
        if r.returncode:
            files = _git(wt, "diff", "--name-only", "--diff-filter=U", check=False).split()
            _gitr(wt, "revert", "--abort")
            raise RuntimeError("revert conflicts with later changes on main"
                               + (f" in {', '.join(files)}" if files else "") + ": " + " ".join(r.stderr.split())[:300])
    msg = f"Revert \"{src.get('subject') or 'improve: ' + src['request'][:60]}\"\n\nThis reverts " + \
          ", ".join(c[:12] for c in commits) + f" (improvement {src['id']})."
    _git(wt, "commit", "-q", "-m", msg, env=env)
    return 0, ""


def _do_poke(job, slot):
    """A poke-routes improve or undo: worktree from main, produce one commit, merge it onto main.
    A merge conflict re-runs the job once on fresh main; a second one keeps the branch."""
    jid = job["id"]
    for attempt in (1, 2):
        job["attempt"] = attempt
        wt, br, base = _make_worktree(job)
        _log(jid, f"[worktree] {wt} on {br} from {MAIN} {base[:10]}" + (" (re-run)" if attempt > 1 else ""))
        _save()
        keep_branch = False
        try:
            if job["kind"] == "undo":
                code, final = _produce_revert(job, wt)
            else:
                code, final = _run_agent(job, cwd=wt, wt=wt, port=PORT_BASE + slot)
                job["summary"] = final[-600:]
            commits = _git(wt, "rev-list", "--reverse", "--no-merges", f"{base}..HEAD").split()
            keep_branch = bool(commits)        # until merged, the work is only on this branch
            if job.get("killed"):
                keep_branch = False
                with _lock:
                    set_status(job, "timeout" if job["killed"] == "timeout" else "cancelled")
                return
            if not commits:
                with _lock:
                    set_status(job, "no-change" if code == 0 else "failed",
                               **({} if code == 0 else {"error": f"agent exited with code {code} and made no commit"}))
                return
            if len(commits) > 1:
                _log(jid, f"[warn] {len(commits)} commits instead of one; all of them get merged (and undone together)")
            job["subject"] = _git(wt, "log", "-1", "--format=%s", commits[-1])
            _note_changes(job, wt, commits)
            with _lock:
                set_status(job, "merging")
            _save()
            res = merge_onto_main(REPO, str(wt), base, log=lambda s: _log(jid, s))
            if res["ok"]:
                with _lock:
                    job["commits"] = res["commits"]
                    job["commit"] = res["commits"][-1]
                    if res.get("note"):
                        job["root_note"] = res["note"]
                    set_status(job, "done")
                    if job["kind"] == "undo":
                        set_status(_jobs[job["undo_of"]], "undone", undone_by=jid)
                _save()
                keep_branch = False
                return
            files = ", ".join(res["files"]) or "?"
            job["conflict"] = {"files": res["files"], "details": res["details"], "attempt": attempt}
            if attempt == 1:
                keep_branch = False
                _log(jid, f"[conflict] could not rebase onto {MAIN} ({files}); re-running once on fresh {MAIN}")
                with _lock:
                    set_status(job, "running", note=f"conflict with {MAIN} in {files}; re-ran on fresh {MAIN}")
                job.pop("summary", None)
                continue
            _log(jid, f"[conflict] again ({files}); branch {br} kept for review")
            with _lock:
                set_status(job, "conflict", error=f"conflicts with {MAIN} in {files} twice; branch {br} kept "
                           f"for manual review", note=res["details"][:300])
            return
        finally:
            _drop_worktree(wt, br, keep_branch=keep_branch)
            if keep_branch:
                job["kept_branch"] = br


def _do_improve_rom(job):
    """ROM jobs: the agent makes its own worktree in the ROM repo and squash-merges into arklarna."""
    jid, target = job["id"], job["target"]
    old_wt = ARK / "rom-wt" / f"improve-{jid}"
    if old_wt.exists():   # left by an interrupted run of this job: keep its branch under another name
        _gitr(ROM, "worktree", "remove", "--force", str(old_wt))
        _gitr(ROM, "branch", "-m", f"arklarna/improve-{jid}", f"arklarna/improve-{jid}-interrupted-{int(time.time())}")
    before = _head(target)
    job["base"] = before
    code, final = _run_agent(job, cwd=ROM)
    after = _head(target)
    repo = ROM
    commits = _git(repo, "rev-list", "--reverse", "--no-merges", f"{before}..{after}").split() if after != before else []
    mine = [c for c in commits if _git(repo, "log", "-1", "--format=%s", c).startswith("improve: ")]
    if len(mine) != len(commits):
        _log(jid, f"[note] {len(commits) - len(mine)} other commit(s) landed meanwhile; not counted as this job's")
    commits = mine
    job["commits"] = commits
    job["summary"] = final[-600:]
    if commits:
        job["commit"] = commits[-1]
        job["subject"] = _git(repo, "log", "-1", "--format=%s", commits[-1])
        _note_changes(job, repo, commits)
    with _lock:
        if job.get("killed"):
            set_status(job, "timeout" if job["killed"] == "timeout" else "cancelled")
        elif not commits:
            set_status(job, "no-change" if code == 0 else "failed")
        else:
            set_status(job, "done")


def _do_undo_rom(job):
    """Revert the ROM job's commits as one commit on arklarna, rebuild and recopy the release."""
    jid = job["id"]
    src = _jobs[job["undo_of"]]
    repo = ROM
    if _git(repo, "branch", "--show-current") != ROM_BRANCH:
        raise RuntimeError(f"{repo} is not on {ROM_BRANCH}; not reverting")
    commits = src.get("commits") or [src["commit"]]
    if _git(repo, "diff", "--cached", "--name-only"):
        raise RuntimeError(f"{repo} has staged changes; not reverting")
    touched = set(_git(repo, "diff", "--name-only", f"{commits[0]}~1", commits[-1]).split())
    dirty = touched & set(_git(repo, "diff", "--name-only").split())
    if dirty:
        raise RuntimeError("uncommitted edits to files this improvement touched: " + ", ".join(sorted(dirty)))
    try:
        for c in reversed(commits):
            _log(jid, f"> git revert --no-commit {c[:10]}")
            _git(repo, "revert", "--no-commit", c)
        msg = f"Revert \"{src.get('subject') or 'improve: ' + src['request'][:60]}\"\n\nThis reverts " + \
              ", ".join(c[:12] for c in commits) + f" (improvement {src['id']})."
        _git(repo, "commit", "-m", msg)
    except Exception:
        _git(repo, "revert", "--abort", check=False)
        raise
    job["commit"] = _git(repo, "rev-parse", "HEAD")
    job["commits"] = [job["commit"]]
    _log(jid, f"[revert] {job['commit'][:10]}")
    _log(jid, "> make -j10 (rebuilding the ROM after the revert)")
    env = dict(os.environ, PATH=f"{TOOLCHAIN}:{os.environ.get('PATH', '')}")
    r = subprocess.run(["make", "-j10"], cwd=str(repo), env=env, capture_output=True, text=True, timeout=TIMEOUT)
    for line in (r.stdout + r.stderr).splitlines()[-15:]:
        _log(jid, line)
    if r.returncode:
        raise RuntimeError("ROM build after the revert failed; release ROM left as it was")
    shutil.copy2(repo / "pokeemerald.gba", RELEASE_ROM)
    _log(jid, f"[copy] {RELEASE_ROM}")
    with _lock:
        set_status(src, "undone", undone_by=jid)
        set_status(job, "done")


def _run_job(job, slot):
    jid = job["id"]
    try:
        if job["target"] == "rom":
            (_do_undo_rom if job["kind"] == "undo" else _do_improve_rom)(job)
        else:
            _do_poke(job, slot)
    except Exception as e:
        with _lock:
            job["error"] = f"{type(e).__name__}: {e}"
            if job["status"] in ACTIVE:
                set_status(job, "failed")
        _log(jid, f"[error] {job['error']}")
    finally:
        with _lock:
            job["finished"] = time.time()
            job.pop("slot", None)
            _slots[slot] = None
            _cv.notify_all()
        _log(jid, f"[status] {job['status']}" + (f", commit {job['commit'][:10]}" if job.get("commit") else ""))
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
             + (f", commit {job['commit'][:10]} on {TARGETS[job['target']]['branch'] or 'poke-routes main'}" if job.get("commit") else "")
             + (f": {job['error']}" if job.get("error") else ""))


# ------------------------------------------------------------------ scheduling

def mem_available_mb(text=None):
    """Free + inactive memory in MB from vm_stat, or None where vm_stat is missing."""
    if text is None:
        try:
            text = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None
    m = re.search(r"page size of (\d+) bytes", text)
    if not m:
        return None
    page = int(m.group(1))

    def pages(name):
        mm = re.search(rf"^{name}:\s+(\d+)\.?$", text, re.M)
        return int(mm.group(1)) if mm else 0
    return (pages("Pages free") + pages("Pages inactive")) * page / (1 << 20)


def mem_ok(avail_mb, min_mb=None):
    min_mb = MEM_MIN_MB if min_mb is None else min_mb
    return avail_mb is None or avail_mb >= min_mb


def pick_next(jobs, me=None):
    """FIFO among queued jobs this process owns; at most one ROM job active at a time."""
    me = _me if me is None else me
    rom_busy = any(j.get("target") == "rom" and j.get("status") in ACTIVE for j in jobs.values())
    for j in sorted(jobs.values(), key=lambda j: j.get("created", 0)):
        if j.get("status") != "queued" or j.get("owner") != me:
            continue
        if j.get("target") == "rom" and rom_busy:
            continue
        return j
    return None


def _worker_loop(slot):
    while True:
        with _cv:
            if slot >= _st["workers"] or not _st["owner"]:
                _slots.pop(slot, None)
                _threads.pop(slot, None)
                return
            if pick_next(_jobs) is None:
                _cv.wait(3)
                continue
            wait = _st["last_start"] + START_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
            continue
        avail = mem_available_mb()
        _st["mem"] = avail
        if not mem_ok(avail):
            if not _st["mem_wait"]:
                print(f"improve: only {avail:.0f} MB free+inactive, waiting before starting a job", flush=True)
            _st["mem_wait"] = True
            time.sleep(10)
            continue
        _st["mem_wait"] = False
        with _cv:
            if slot >= _st["workers"] or _st["last_start"] + START_GAP > time.time():
                continue
            job = pick_next(_jobs)
            if job is None:
                continue
            set_status(job, "running", slot=slot, started=time.time())
            job.pop("killed", None)
            _slots[slot] = job["id"]
            _st["last_start"] = time.time()
        _save()
        _run_job(job, slot)


def _spawn_workers():
    with _lock:
        for s in range(_st["workers"]):
            t = _threads.get(s)
            if t is None or not t.is_alive():
                _slots.setdefault(s, None)
                t = threading.Thread(target=_worker_loop, args=(s,), name=f"improve-worker-{s}", daemon=True)
                _threads[s] = t
                t.start()
        _cv.notify_all()


def set_workers(n) -> dict:
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise ValueError(f"workers must be 1-{MAX_WORKERS}")
    if not 1 <= n <= MAX_WORKERS:
        raise ValueError(f"workers must be 1-{MAX_WORKERS}")
    with _lock:
        _st["workers"] = n
        _meta["workers"] = n
        _save_meta()
    _spawn_workers()
    return {"workers": n}


# ------------------------------------------------------------------ ownership, recovery

def _lock_holder():
    """{pid, port} of the process holding the queue lock, or None if nobody holds it."""
    try:
        fd = os.open(str(LOCKFILE), os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                raise
        try:
            info = json.loads(LOCKFILE.read_text() or "{}")
        except ValueError:
            info = {}
        return {"pid": info.get("pid"), "port": info.get("port")}
    finally:
        os.close(fd)


def try_acquire_lock(port=None):
    """Take the exclusive queue lock (fcntl.flock, held for the life of the process)."""
    if _st["lockfd"] is not None:
        return True
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCKFILE), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps({"pid": _me, "port": port, "since": time.time()}).encode())
    os.fsync(fd)
    _st["lockfd"] = fd
    return True


def release_lock():
    """Tests only: give the lock back."""
    fd = _st["lockfd"]
    if fd is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        _st["lockfd"] = None
    _st["owner"] = False


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def recover_interrupted(job, why="the server stopped while it was running"):
    """A job whose owner died mid-run goes back to queued once (it runs again from scratch)."""
    st = job.get("status")
    if job.get("agent_pid") and _pid_alive(job["agent_pid"]):
        _kill_orphan(job["agent_pid"], job["id"])
    job.pop("agent_pid", None)
    job["owner"] = _me
    if st not in ACTIVE:
        return job
    br = job.get("branch")
    if st == "merging" and br and _gitr(REPO, "rev-parse", "-q", "--verify", f"refs/heads/{br}").returncode == 0:
        tip = _git(REPO, "rev-parse", f"refs/heads/{br}")
        if tip != job.get("base") and _is_ancestor(REPO, tip, f"refs/heads/{MAIN}"):
            job["commit"], job["commits"] = tip, [tip]
            set_status(job, "done", note="its commit was already on main when the server restarted")
            return job
    if job.get("requeued", 0) >= 1:
        set_status(job, "failed", error=f"interrupted twice ({why}); not requeued again")
        return job
    job["requeued"] = job.get("requeued", 0) + 1
    for k in ("slot", "started", "killed"):
        job.pop(k, None)
    set_status(job, "queued", note=f"requeued: {why}")
    _log(job["id"], f"[recover] requeued: {why}")
    return job


def migrate_lost(jobs, me=None):
    """One-off, the first time this code owns the queue: jobs an old server wrongly marked
    "failed: server restarted ..." (it cleared other live servers' queues on start) go back to
    queued, owned by us. Returns the ids changed."""
    me = _me if me is None else me
    changed = []
    for j in sorted(jobs.values(), key=lambda j: j.get("created", 0)):
        if j.get("status") != "failed" or j.get("error") != LOST_ERR:
            continue
        j["owner"] = me
        j["old_error"] = j.pop("error")
        for k in ("started", "finished", "slot", "killed"):
            j.pop(k, None)
        set_status(j, "queued", note="requeued: an old server had wrongly marked it failed on restart")
        changed.append(j["id"])
    return changed


def _legacy_workers():
    """Old-style servers (from before the queue lock) running an agent from this repo's root right
    now. Their in-memory queues are invisible to us, so their jobs are left to them."""
    if REPO != POKE.resolve() and not os.environ.get("IMPROVE_LEGACY_CHECK"):
        return []
    try:
        out = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,command="], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    procs = {}
    for line in out.splitlines():
        p = line.split(None, 2)
        if len(p) == 3 and p[0].isdigit():
            procs[int(p[0])] = (int(p[1]), p[2])
    parents = {pp for pp, cmd in procs.values()
               if "implementing an improvement" in cmd and "claude" in cmd.split(" -p ")[0]}
    found = []
    for pp in sorted(parents - {_me, 1}):
        if pp not in procs or "server.py" not in procs[pp][1]:
            continue
        try:
            cwd = subprocess.run(["lsof", "-a", "-p", str(pp), "-d", "cwd", "-Fn"], capture_output=True,
                                 text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            cwd = ""
        if f"n{REPO}" in cwd.splitlines():
            found.append(pp)
    return found


def _adopt_unowned():
    """Jobs with no owner field come from old-style servers. While one of those is busy we leave
    them alone; once none has run an agent for LEGACY_GRACE seconds their queues are empty, so
    leftover queued jobs are ours to run and a leftover "running" one is requeued once."""
    legacy = _legacy_workers()
    now = time.time()
    changed = False
    with _lock:
        _st["legacy"] = legacy
        if legacy:
            _st["legacy_idle_since"] = None
            return
        if _st["legacy_idle_since"] is None:
            _st["legacy_idle_since"] = now
        if now - _st["legacy_idle_since"] < LEGACY_GRACE:
            return
        for j in _jobs.values():
            if j.get("owner") or j.get("status") not in ("queued",) + ACTIVE:
                continue
            if j["status"] == "queued":
                j["owner"] = _me
                j.setdefault("notes", []).append("adopted from an older server's queue")
            else:
                recover_interrupted(j, "its old-style server stopped while it was running")
            changed = True
        if changed:
            _cv.notify_all()
    if changed:
        _save()


def _become_owner():
    with _lock:
        _st["owner"] = True
        _jobs.clear()
        for d in _read_disk():
            _jobs[d["id"]] = d
        _load_meta()
        if os.environ.get("IMPROVE_WORKERS"):
            n = _env_int("IMPROVE_WORKERS", DEFAULT_WORKERS)
        else:
            n = int(_meta.get("workers") or DEFAULT_WORKERS)
        _st["workers"] = max(1, min(MAX_WORKERS, n))
        if not _meta.get("migrated_lost_v1"):
            ids = migrate_lost(_jobs)
            _meta["migrated_lost_v1"] = time.time()
            _meta["migrated_ids"] = ids
            _save_meta()
            if ids:
                print(f"improve: recovered {len(ids)} job(s) wrongly marked failed: {', '.join(ids)}", flush=True)
        for j in _jobs.values():
            o = j.get("owner")
            if o is None or o == _me:
                continue          # no owner = an old-style server's job: see _adopt_unowned
            # owned by an earlier lock holder, which is gone now that we hold the lock
            if j.get("status") in ACTIVE:
                recover_interrupted(j)
            else:
                j["owner"] = _me
    _save()
    print(f"improve: this server (pid {_me}) owns the queue {STATE}, {_st['workers']} worker(s)", flush=True)
    _spawn_workers()


def _workers_enabled():
    return os.environ.get("IMPROVE_WORKERS", "") != "0"


def _tick():
    while True:
        try:
            if not _st["owner"]:
                if _workers_enabled() and try_acquire_lock(_st["port"]):
                    _become_owner()
            else:
                _adopt_unowned()
                try:
                    m = STATE.stat().st_mtime_ns
                except OSError:
                    m = None
                if m != _st["last_write"]:
                    _save()       # someone else wrote the file: merge their jobs in, keep ours
                    with _cv:
                        _cv.notify_all()
                if _meta.get("root_stale_from") and _merge_lock.acquire(blocking=False):
                    try:
                        _sync_root(REPO)
                    finally:
                        _merge_lock.release()
                _spawn_workers()
        except Exception as e:  # pragma: no cover - keep ticking
            print(f"improve: tick error {type(e).__name__}: {e}", flush=True)
        time.sleep(3)


def start(port=None):
    """Called once by server.py (and lazily by the API): take the lock if free, else stay read-only."""
    with _lock:
        if _st["started"]:
            return
        _st["started"] = True
        _st["port"] = port
    if _workers_enabled() and try_acquire_lock(port):
        _become_owner()
    else:
        holder = _lock_holder() or {}
        print(f"improve: queue owned by pid {holder.get('pid')} on port {holder.get('port')}; "
              f"this server shows it read-only", flush=True)
    threading.Thread(target=_tick, name="improve-tick", daemon=True).start()


# ------------------------------------------------------------------ api (lock holder)

def decode_png_screenshot_data_url(screenshot_data_url: str) -> bytes:
    if not screenshot_data_url.startswith(png_data_url_prefix):
        raise ValueError("screenshot must be a data:image/png;base64 URL")

    encoded_png = screenshot_data_url[len(png_data_url_prefix):]
    if len(encoded_png) * 3 // 4 > maximum_screenshot_size_in_bytes:
        raise ValueError("screenshot is larger than 8 MB")
    try:
        png_bytes = base64.b64decode(encoded_png, validate=True)
    except ValueError as decode_error:
        raise ValueError(f"screenshot is not valid base64 ({decode_error})") from decode_error
    if not png_bytes.startswith(png_file_signature):
        raise ValueError("screenshot is not a PNG")
    return png_bytes


def save_screenshot_for_job(jid: str, png_bytes: bytes) -> str:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = LOGDIR / f"{jid}.png"
    screenshot_path.write_bytes(png_bytes)
    return str(screenshot_path)


def submit(request: str, target: str, screenshot_data_url: str = "", mode: str = "improve") -> dict:
    request = (request or "").strip()
    if not request:
        raise ValueError("request is empty")
    if len(request) > 4000:
        raise ValueError("request is longer than 4000 characters")
    if target not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}")
    if mode not in ("improve", "agent"):
        raise ValueError("mode must be improve or agent")
    screenshot_png_bytes = decode_png_screenshot_data_url(screenshot_data_url) if screenshot_data_url else b""
    jid = time.strftime("%m%d%H%M") + "-" + uuid.uuid4().hex[:4]
    job = {"id": jid, "kind": "improve", "request": request, "target": target, "status": "queued",
           "created": time.time(), "owner": _me}
    if mode == "agent":
        job["mode"] = "agent"
    if screenshot_png_bytes:
        job["screenshot"] = save_screenshot_for_job(jid, screenshot_png_bytes)
    with _cv:
        _jobs[jid] = job
        _cv.notify_all()
    _save()
    return public(job)


def undo(jid: str) -> dict:
    with _cv:
        src = _jobs.get(jid)
        if not src or src["kind"] != "improve":
            raise KeyError(jid)
        if src["status"] != "done" or not src.get("commit"):
            raise ValueError(f"nothing to undo (status {src['status']})")
        if any(j.get("undo_of") == jid and j["status"] in ("queued",) + ACTIVE for j in _jobs.values()):
            raise ValueError("an undo for this job is already queued")
        uid = jid + "-undo"
        n = 1
        while uid in _jobs:
            n += 1
            uid = f"{jid}-undo{n}"
        job = {"id": uid, "kind": "undo", "undo_of": jid, "request": f"undo: {src['request'][:80]}",
               "target": src["target"], "status": "queued", "created": time.time(), "owner": _me}
        _jobs[uid] = job
        _cv.notify_all()
    _save()
    return public(job)


def cancel(jid: str) -> dict:
    with _lock:
        job = _jobs.get(jid)
        if not job:
            raise KeyError(jid)
        if job["status"] == "queued":
            if not _mine(job):
                raise ValueError("an older server is running this job's queue; cancel it there")
            set_status(job, "cancelled")
        elif job["status"] == "running" and _procs.get(jid):
            threading.Thread(target=_kill, args=(_procs[jid], jid, "cancelled"), daemon=True).start()
        elif job["status"] == "merging":
            raise ValueError("the job is being merged; undo it once it is done")
    _save()
    return public(job)


def public(job, since=None) -> dict:
    d = {k: v for k, v in job.items() if k not in ("summary", "agent_pid")}
    d["summary"] = job.get("summary", "")
    d["target_label"] = TARGETS.get(job.get("target"), {}).get("label", job.get("target"))
    if job.get("status") in ACTIVE and job.get("slot") is not None and job.get("target") == "poke-routes":
        d["port"] = PORT_BASE + job["slot"]
    if _st["owner"] and not job.get("owner") and job.get("status") in ("queued",) + ACTIVE:
        lp = _st["legacy"]
        d["held_by"] = f"older server pid {', '.join(map(str, lp))}" if lp else "older server (adopting soon)"
    if since is not None:
        lines = _lines(job["id"])
        d["log"] = lines[since:]
        d["log_next"] = len(lines)
    return d


def _view_jobs():
    """Jobs as this process sees them: its memory when it owns the queue, else the file."""
    if _st["owner"]:
        with _lock:
            return dict(_jobs)
    try:
        m = STATE.stat().st_mtime_ns
    except OSError:
        m = None
    cached_m, cached = _st["disk_cache"]
    if m != cached_m:
        cached = _read_disk()
        _st["disk_cache"] = (m, cached)
    return {j["id"]: j for j in cached}


def history() -> dict:
    jobs = sorted(_view_jobs().values(), key=lambda j: j.get("created", 0), reverse=True)
    holder = {"pid": _me, "port": _st["port"]} if _st["owner"] else (_lock_holder() or {})
    active = [j for j in jobs if j.get("status") in ACTIVE]
    if _st["owner"]:
        with _lock:
            n = _st["workers"]
            slots = [{"slot": s, "job": _slots.get(s), "port": PORT_BASE + s} for s in range(n)]
            slots += [{"slot": s, "job": v, "port": PORT_BASE + s, "retiring": True}
                      for s, v in sorted(_slots.items()) if s >= n and v]
    else:
        n = 0
        try:
            n = int(json.loads(META.read_text()).get("workers") or 0)
        except (OSError, ValueError):
            pass
        n = n or DEFAULT_WORKERS
        by_slot = {j.get("slot"): j["id"] for j in active if j.get("slot") is not None}
        slots = [{"slot": s, "job": by_slot.get(s), "port": PORT_BASE + s}
                 for s in range(max([n] + [k + 1 for k in by_slot]))]
    return {"jobs": [public(j) for j in jobs[:100]],
            "running": [j["id"] for j in active],
            "queued": sum(1 for j in jobs if j.get("status") == "queued"),
            "workers": n, "max_workers": MAX_WORKERS, "slots": slots,
            "owner": {"me": bool(_st["owner"]), "pid": holder.get("pid"), "port": holder.get("port")},
            "readonly": not _st["owner"],
            "legacy": _st["legacy"],
            "mem": {"avail_mb": None if _st["mem"] is None else round(_st["mem"]),
                    "min_mb": MEM_MIN_MB, "waiting": _st["mem_wait"]},
            "root_stale": bool(_meta.get("root_stale_from")),
            "targets": {k: v["label"] for k, v in TARGETS.items()}, "usage": _usage()}


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


def _forward(path, body):
    """A server that does not own the queue passes writes on to the one that does."""
    import urllib.error
    import urllib.request
    holder = _lock_holder()
    if not holder or not holder.get("port"):
        return {"error": "no server owns the improve queue right now; start one (python3 webui/server.py) "
                         "or wait a few seconds"}, 503
    port = holder["port"]
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or b"{}"), r.status
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}"), e.code
        except ValueError:
            return {"error": f"queue owner on port {port}: HTTP {e.code}"}, e.code
    except (OSError, ValueError) as e:
        return {"error": f"the improve queue is owned by the server on port {port} (pid {holder.get('pid')}), "
                         f"which did not answer: {e}"}, 503


def handle(h, method: str, u) -> None:
    """Dispatch /api/improve*; h is the BaseHTTPRequestHandler (uses its _json)."""
    if not _same_origin(h):
        return h._json({"error": "improvements only accept requests from this machine's own page"}, 403)
    if not _st["started"]:
        start(getattr(getattr(h, "server", None), "server_address", (None, None))[1])
    parts = [p for p in u.path.split("/") if p][2:]  # after api/improve
    try:
        if method == "GET":
            if not parts:
                return h._json(history())
            if parts == ["usage"]:
                return h._json(__import__("usage").current())  # webui/usage.py: on-demand 5-hour reading
            job = _view_jobs().get(parts[0])
            if not job:
                return h._json({"error": "unknown job"}, 404)
            from urllib.parse import parse_qs
            since = int((parse_qs(u.query).get("since") or ["0"])[0] or 0)
            return h._json(public(job, since=max(0, since)))
        if (h.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return h._json({"error": "Content-Type must be application/json"}, 415)
        n = int(h.headers.get("Content-Length") or 0)
        body = json.loads(h.rfile.read(n) or b"{}") if n else {}
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        if not _st["owner"]:
            out, code = _forward(u.path, body)
            return h._json(out, code)
        if not parts:
            return h._json(submit(str(body.get("request", "")), str(body.get("target", "poke-routes")),
                                  str(body.get("screenshot") or ""), str(body.get("mode") or "improve")), 202)
        if parts == ["workers"]:
            return h._json(set_workers(body.get("workers")))
        if len(parts) == 2 and parts[1] == "undo":
            return h._json(undo(parts[0]), 202)
        if len(parts) == 2 and parts[1] == "cancel":
            return h._json(cancel(parts[0]))
        return h._json({"error": "not found"}, 404)
    except KeyError:
        return h._json({"error": "unknown job"}, 404)
    except ValueError as e:
        return h._json({"error": str(e)}, 400)
