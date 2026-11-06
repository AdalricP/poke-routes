"""Improvements box ("liquid software"): type how the software should be different,
and a headless Claude Code run implements it as exactly one git commit.

    POST /api/improve              {"request": "...", "target": "poke-routes" | "rom",
                                    "screenshot": optional "data:image/png;base64,...",
                                    "screenshots": optional list of those (up to 6),
                                    "mode": "improve" (default) | "agent"}
    GET  /api/improve              history (newest first), worker slots, owner, targets, usage
    GET  /api/improve/<id>?since=N status and log lines from N on
    POST /api/improve/<id>/undo    queue a git revert of that job's commit
    POST /api/improve/<id>/fix     {"request": "..."}  queue a follow-up job on top of a done one,
                                    for when the agent got it wrong or incomplete
    POST /api/improve/<id>/retry   queue a fresh job with the same request and drop the old one,
                                    for a failed/timeout/conflict/cancelled job
    POST /api/improve/<id>/cancel  cancel a queued or running job
    POST /api/improve/<id>/delete  drop a finished job (not queued/running/merging) from the list
    POST /api/improve/<id>/steer   {"text": "..."}  feed extra instructions to a running job's live agent
    POST /api/improve/<id>/pin     {"pinned": true/false}  protect a finished job from /clear, highlight it
    POST /api/improve/clear        {"status": "done"|"error"|"cancelled"}  dismiss every finished, unpinned
                                    job of that kind from history (they stay on disk, just hidden)
    POST /api/improve/workers      {"workers": n}  how many jobs run at once (1-8)

"agent" jobs come from the shell's Agent tab: a task done *for* the owner in the tool
("get this tileset", "apply that tileset") instead of a change to the software.
Same queue, same one-commit-and-Undo rule, only the prompt's opening differs.

Several workers run in parallel (default 3). A poke-routes job gets its own git worktree
(../poke-routes-wt/job-<id>, branch improve/job-<id>) made from main; once its commit is
there, a merge step under a global lock rebases it onto the latest main and fast-forwards main
(and the repo-root checkout the server serves from). A conflict is first handed to a short Opus
run that resolves only the conflicted lines; if it can't, the job re-runs once on fresh main, and a
second conflict leaves the branch for the owner. ROM jobs keep their own worktree flow
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


def read_local_settings_of_this_checkout(local_settings_path: Path) -> dict:
    if not local_settings_path.is_file():
        return {}
    try:
        local_settings = json.loads(local_settings_path.read_text())
    except (OSError, ValueError) as read_error:
        print(f"improve: ignoring {local_settings_path}: {read_error}", flush=True)
        return {}
    if not isinstance(local_settings, dict):
        print(f"improve: ignoring {local_settings_path}: not a JSON object", flush=True)
        return {}
    return local_settings


def optional_path_from_local_settings(setting_name: str):
    configured_path_text = LOCAL_SETTINGS.get(setting_name)
    if not configured_path_text:
        return None
    configured_path = Path(os.path.expanduser(str(configured_path_text)))
    return configured_path


def commit_author_from_local_settings():
    configured_author = LOCAL_SETTINGS.get("commit_author")
    if not (isinstance(configured_author, list) and len(configured_author) == 2):
        return None
    commit_author_name_and_email = (str(configured_author[0]), str(configured_author[1]))
    return commit_author_name_and_email


LOCAL_SETTINGS_PATH = Path(os.environ.get("POKEROUTES_LOCAL_SETTINGS") or HERE / "local-settings.json")
LOCAL_SETTINGS = read_local_settings_of_this_checkout(LOCAL_SETTINGS_PATH)
ARK = optional_path_from_local_settings("rom_project_dir")
ROM = ARK / "rom" if ARK else None
ROM_BRANCH = "arklarna"
RELEASE_ROM = ARK / "release/v1/arklarna-v1.gba" if ARK else None
TOOLCHAIN = optional_path_from_local_settings("arm_toolchain_bin_dir")
DOCS = ARK / "documenting-agent-work" if ARK else None
AUTHOR = commit_author_from_local_settings()
OWNER_RULES = str(LOCAL_SETTINGS.get("owner_rules") or "")


def is_improve_box_enabled() -> bool:
    improve_box_switch_from_environment = os.environ.get("POKEROUTES_IMPROVE_BOX", "")
    if improve_box_switch_from_environment:
        return improve_box_switch_from_environment == "1"
    improve_box_switch_from_local_settings = bool(LOCAL_SETTINGS.get("improve_box"))
    return improve_box_switch_from_local_settings


def is_rom_target_available() -> bool:
    rom_target_is_available = bool(ROM and ROM.is_dir())
    return rom_target_is_available


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
maximum_screenshots_per_job = 6
maximum_total_screenshot_size_in_bytes = 24 * 1024 * 1024
png_data_url_prefix = "data:image/png;base64,"
png_file_signature = b"\x89PNG\r\n\x1a\n"
MAX_WORKERS = 8
DEFAULT_WORKERS = 3
PORT_BASE = 8800                 # a job in slot s verifies with its own server copy on PORT_BASE + s
MEM_MIN_MB = _env_int("IMPROVE_MEM_MIN_MB", 800)
ROOT_WAIT = _env_int("IMPROVE_ROOT_WAIT", 120)    # seconds to wait for the root checkout to be clean
LEGACY_GRACE = _env_int("IMPROVE_LEGACY_GRACE", 20)
START_GAP = 5                    # seconds between job starts, so the last agent has allocated before we measure
WORKLOG = os.environ.get("IMPROVE_WORKLOG", "1") != "0" and bool(DOCS and DOCS.is_dir())
LOST_ERR = "server restarted while the job was queued or running"

TARGETS = {
    "poke-routes": {"label": "Generator & web UI", "repo": REPO, "branch": None},
    **({"rom": {"label": "ROM (Arklarna)", "repo": ROM, "branch": ROM_BRANCH}} if is_rom_target_available() else {}),
}

# Tools the unattended run may never use, even in bypassPermissions mode (deny rules win).
DENY = ["Bash(git push:*)", "Bash(git push *)", "Bash(*git push*)", "Bash(git remote:*)",
        "Bash(git config:*)", "Bash(gh:*)", "Bash(sudo:*)", "Bash(rm -rf /*)", "Bash(rm -rf ~*)",
        "Bash(npm publish:*)", "Bash(vercel:*)", "Bash(*openqodex*)", "mcp__openqodex"]

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
_stdin_locks: dict = {}            # id -> Lock guarding that agent's stdin pipe (steer() writes to it)
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
    if AUTHOR:
        env.update(GIT_AUTHOR_NAME=AUTHOR[0], GIT_AUTHOR_EMAIL=AUTHOR[1],
                   GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1])
    return env


def _committer_env():
    """Rebases and fast-forwards keep the job commit's author; only the committer is set."""
    env = dict(os.environ, GIT_EDITOR="true")
    if AUTHOR:
        env.update(GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1])
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


RESOLVER_MODEL = os.environ.get("IMPROVE_RESOLVER_MODEL", "claude-opus-5-5")
RESOLVER_EFFORT = os.environ.get("IMPROVE_RESOLVER_EFFORT", "medium")
RESOLVE_PROMPT = """A git merge in this worktree stopped with merge conflicts in: {files}.
Two independent changes touched the same lines. One side is a job's branch being merged; the other
side is what is already on main.

Resolve ONLY the conflicts: edit the conflict regions so both changes' intent is kept, and touch no
other lines. When every conflict marker is gone, run:
  git add {files}
  git commit --no-edit
Never run git merge --abort, git reset, git push or anything that rewrites main. If the two changes
truly cannot both be kept, stop without committing and say why in one sentence."""


def resolve_conflict(wt, files, log=lambda s: None):
    """Let one short Opus run resolve a paused rebase in place (instead of redoing the whole job).
    True when the rebase completed cleanly; False leaves the rebase paused for the caller to abort."""
    if os.environ.get("IMPROVE_RESOLVER", "1") == "0":
        return False
    log(f"[resolve] {RESOLVER_MODEL} ({RESOLVER_EFFORT}) resolving {', '.join(files)}")
    prompt = RESOLVE_PROMPT.format(files=" ".join(files))
    try:
        r = subprocess.run([_claude(), "-p", prompt, "--model", RESOLVER_MODEL, "--effort", RESOLVER_EFFORT,
                            "--permission-mode", "bypassPermissions", "--strict-mcp-config", "--no-chrome",
                            "--disallowedTools", *DENY],
                           cwd=str(wt), capture_output=True, text=True, timeout=900,
                           env={**_child_env("poke-routes"), **_committer_env()}, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        log("[resolve] timed out")
        return False
    in_rebase = any(Path(_git(wt, "rev-parse", "--git-path", d, check=False)).exists()
                    for d in ("rebase-merge", "rebase-apply", "MERGE_HEAD"))
    markers = _gitr(wt, "grep", "-l", "-E", "^(<<<<<<<|>>>>>>>) ", "--", ".").stdout.strip()
    if in_rebase or markers:
        log(f"[resolve] not resolved ({'rebase still paused' if in_rebase else 'markers left: ' + markers}); "
            f"{(r.stdout or '').strip()[-200:]}")
        return False
    log("[resolve] resolved; merge completed")
    return True


def merge_onto_main(repo, wt, base, log=lambda s: None, root_wait=None):
    """Land the job's branch (base..HEAD of worktree wt) on main as one merge commit, so the session
    graph shows the branch forking and joining. Never forces, never rewrites commits already on main.
    Returns {"ok", "head", "commits", "root_synced", "note"} or {"ok": False, "conflict": True,
    "files", "details"}."""
    root_wait = ROOT_WAIT if root_wait is None else root_wait
    job_branch = _git(wt, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    job_tip = _git(wt, "rev-parse", "HEAD")
    job_subject = _git(wt, "log", "-1", "--format=%s", job_tip)
    with _merge_lock:
        for _ in range(10):
            old = _git(repo, "rev-parse", f"refs/heads/{MAIN}")
            merged = create_merge_commit_of_job_branch(wt, old, job_tip, job_branch, job_subject, log)
            if not merged["ok"]:
                restore_job_branch_checkout(wt, job_branch, job_tip)
                return merged
            new = merged["merge_commit"]
            moved, synced, note = _advance_main(repo, old, new, log, root_wait)
            restore_job_branch_checkout(wt, job_branch, job_tip)
            if moved:
                log(f"[merge] {MAIN} {old[:10]} -> {new[:10]} (merge of {job_branch or job_tip[:10]})")
                return {"ok": True, "head": new, "commits": [new], "root_synced": synced, "note": note}
            log(f"[merge] {MAIN} moved while merging; merging again")
        raise RuntimeError(f"{MAIN} kept moving; gave up merging")


def create_merge_commit_of_job_branch(wt, main_head, job_tip, job_branch, job_subject, log) -> dict:
    merge_message = f"Merge {job_branch or job_tip[:10]}: {job_subject}"
    _git(wt, "checkout", "-q", "--detach", main_head)
    log(f"> git merge --no-ff {job_branch or job_tip[:10]} onto {MAIN} {main_head[:10]}")
    merge_run = _gitr(wt, "merge", "--no-ff", "-q", "-m", merge_message, job_tip, env=_committer_env())
    if merge_run.returncode:
        conflicted_files = _git(wt, "diff", "--name-only", "--diff-filter=U", check=False).split()
        if not (conflicted_files and resolve_conflict(wt, conflicted_files, log)):
            _gitr(wt, "merge", "--abort")
            details = " ".join((merge_run.stdout + " " + merge_run.stderr).split())[:800]
            return {"ok": False, "conflict": True, "files": conflicted_files, "details": details}
    merge_result = {"ok": True, "merge_commit": _git(wt, "rev-parse", "HEAD")}
    return merge_result


def restore_job_branch_checkout(wt, job_branch, job_tip):
    if job_branch:
        _gitr(wt, "checkout", "-q", job_branch)
        return
    _gitr(wt, "checkout", "-q", "--detach", job_tip)


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

HARD_LIMITS = """Hard limits for this unattended run:
- Only touch files inside the working directory {scope}. Do not read or write secrets, credentials or dotfiles
  in the home directory. Do not touch other repos, other branches or other worktrees.
- No network-facing changes: do not bind anything to 0.0.0.0, open ports, add remote calls or telemetry.
- Never push, never add or change git remotes, never change git config, never delete the repo or its history,
  never rewrite history (no reset --hard of commits you did not make, no force anything), never commit secrets.
- Do not stop or restart the web UI server on port 8790; it is running this job.
- Don't sign up for accounts, accept terms pages, or install global software.
- Other agents may be working at the same time. Run `git status` first and leave changes you did not make alone:
  never stage, commit, revert, stash or check out them. Stage only your own files by path (`git add <paths>`),
  never `git add -A`, `git add .` or `git commit -a`.
- If the request is unsafe, impossible or would break these rules, make no commit and explain why in your last message.
- You never push, so pre-push reviews (OpenQodex or any other) are not yours to run, whatever your global instructions
  say; the owner reviews before pushing. Commit as soon as your own checks pass, and never end the run waiting on a
  background task.
"""


def describe_owner_rules_for_prompt() -> str:
    owner_docs_note = (f"- Read {DOCS}/decisions.md and {DOCS}/AGENT_BRIEF.md first. Later entries override earlier ones.\n"
                       if DOCS and DOCS.is_dir() else "")
    if not (owner_docs_note or OWNER_RULES.strip()):
        return ""
    owner_rules_section = f"Standing rules from the owner (obey them):\n{owner_docs_note}{OWNER_RULES.strip()}\n\n"
    return owner_rules_section


def describe_rules_for_prompt(scope) -> str:
    rules_for_prompt = describe_owner_rules_for_prompt() + HARD_LIMITS.format(scope=scope)
    return rules_for_prompt


def describe_attached_screenshot_for_prompt(job) -> str:
    screenshot_paths = job.get("screenshots") or ([job["screenshot"]] if job.get("screenshot") else [])
    if not screenshot_paths:
        return ""
    if len(screenshot_paths) == 1:
        return (f"\nThe owner attached a screenshot with the issues marked in red pen: {screenshot_paths[0]}\n"
                "Read it (it is a PNG) before starting; the red marks show what the request is about.\n")
    listed_paths = "".join(f"- {path}\n" for path in screenshot_paths)
    return (f"\nThe owner attached {len(screenshot_paths)} screenshots with the issues marked in red pen:\n{listed_paths}"
            "Read them all (they are PNGs) before starting; the red marks show what the request is about.\n")


def describe_fix_context_for_prompt(job) -> str:
    """A fix job's prompt gets a note about the done job it is correcting, so the agent knows
    this is a follow-up on top of current main rather than a fresh request."""
    src = _jobs.get(job.get("fix_of") or "")
    if not src:
        return ""
    bits = [f"\nThis is a follow-up to an earlier job ({job['fix_of']}) that the user found wrong or "
            "incomplete; their instruction below is what to change about it, on top of the current code "
            "(which already includes that earlier work)."]
    if src.get("request"):
        bits.append(f"Its original request was: {' '.join(src['request'].split())[:300]}")
    if src.get("subject"):
        bits.append(f"Its commit: {src['subject']}")
    return "\n".join(bits) + "\n"


def describe_commit_command_for_prompt() -> str:
    if AUTHOR:
        return f'git -c user.name="{AUTHOR[0]}" -c user.email="{AUTHOR[1]}" commit -m "..."'
    return 'git commit -m "..."'


def describe_editor_context_for_prompt(job) -> str:
    editor_context_path = job.get("editor_context")
    if not editor_context_path:
        return "The editor sent no context with this task.\n"
    editor_context_note = (f"What the user has open in the editor right now: {editor_context_path} (JSON: `params` is the "
                           "route spec as the editor holds it, `tiler`, `route` is the generated route's name/size/seed if "
                           "any, `region_seed` if a region is open). Read it when the task says \"this route\", \"my route\" "
                           "or \"it\", and start from it.\n")
    return editor_context_note


AGENT_PROMPT = """You are the poke-routes Agent. The user typed a task into the Agent tab of the poke-routes editor
(a procedural Pokemon Emerald route and region generator with a web editor) so that you do it for them.
Nobody watches you live and nobody answers questions: if the task is ambiguous, take the most useful reading,
do it, and say which reading you took.
{fix_note}
Task:
<<<
{request}
>>>
{editor_context}
Where you are: {worktree} is a git worktree of the user's checkout ({repo}) on branch `{branch}`, made from the
latest main for this task. Work only there. When you commit, the queue rebases your commit onto main and the
user's editor (it serves {repo} on port 8790) picks it up. Generator modules hot-reload; webui/*.py needs a restart.

Skills (.claude/skills/, load the ones that fit before starting, they save you the code search):
- codebase-map: where every stage, tiler, endpoint and test lives. Load it before reading or changing code.
- route-design: what every spec field does to the map, which biomes are solid, recipes for well-known route styles.
- tilesets: how tiling works, switching tilesets, adding one the user brings, credits.

Tools: the MCP server "routes" (this worktree's code, re-imported on every call) is loaded, call it directly:
- generate_route(spec | spec_object, seed, seeds, overrides, tiler): one route or a few seeds of it; returns PNG paths
  (Read them), the report and an id per route. spec_object takes the editor's `params` as they are.
- route_stats(id), crop_route_image(id, x0, y0, x1, y1, scale), route_grid(id, ...), paint_route(id, cells, rects).
- save_spec(path, spec_object | from_id): writes a validated YAML spec under specs/. show_in_editor(path, note): puts
  an "Open in editor" button for that spec in the user's Agent tab. Use both for every route the user should see.
- generate_world(seed): a whole region (terrain, towns, route network) with its map PNG and per-route specs.
- tilers(), spec_fields(), list_specs(), check_examples().
- check_web_ui(paths): starts this job's own server copy on port {port}, checks the API and the pages, stops it.
  screenshot_web_ui(path, width, height): the same, plus a headless browser screenshot you can Read.
Use these instead of shell scripts, sleeps, curl and killing servers by hand.

How to work:
- Making or tuning routes or regions: start from the editor context or a spec, generate 2-4 seeds, Read the PNGs,
  adjust the spec, repeat until it looks right (usually 2-4 rounds), then save_spec + show_in_editor the best ones.
- Tileset tasks: load the tilesets skill first.
- Changes to the generator or editor: load codebase-map, make the smallest change that does it, verify with
  check_examples and check_web_ui (screenshot_web_ui if the change is visible), and look at the result.
- Look at every image you produce before you say it is done.

{rules}
Finish with exactly one git commit on `{branch}` holding all the files you want kept (specs you saved, code,
docs), message "agent: <short summary of the task>". Commit with: {commit_command}
If the task only produced specs shown with show_in_editor, still commit them so they stay in specs/.
Do not commit files under out/, region_out/, vendor/, webui/improvements.json or webui/improve-logs/.
Last message (the user reads it in the Agent tab): 2-4 plain sentences: what you made or changed, which specs
are waiting under "Open in editor", and anything they have to do themselves.
"""


def build_agent_prompt_for_poke_routes(job, wt=None, port=None) -> str:
    worktree_path = Path(wt or REPO)
    job_server_port = port or PORT_BASE
    job_branch = job.get("branch") or f"improve/job-{job['id']}"

    agent_prompt = AGENT_PROMPT.format(
        fix_note=describe_fix_context_for_prompt(job),
        request=job["request"].strip() + describe_attached_screenshot_for_prompt(job),
        editor_context=describe_editor_context_for_prompt(job),
        worktree=worktree_path, repo=REPO, branch=job_branch, port=job_server_port,
        rules=describe_rules_for_prompt(worktree_path),
        commit_command=describe_commit_command_for_prompt())
    return agent_prompt


def build_prompt(job, wt=None, port=None) -> str:
    jid, target = job["id"], job["target"]
    if target == "poke-routes" and job.get("mode") == "agent":
        return build_agent_prompt_for_poke_routes(job, wt, port)
    req = job["request"].strip() + describe_attached_screenshot_for_prompt(job)
    fix_note = describe_fix_context_for_prompt(job)
    agent = ("This came from the Agent tab, not the Improve box: do the task itself for the owner; don't build a "
             "feature for it unless they asked for one.\n") if job.get("mode") == "agent" else ""
    if target == "poke-routes":
        wt = Path(wt or REPO)
        port = port or PORT_BASE
        br = job.get("branch") or f"improve/job-{jid}"
        return f"""You are implementing an improvement the owner typed into the "Improve" box of the poke-routes web UI.
{fix_note}
Owner's request:
<<<
{req}
>>>
Context: {wt} is a git worktree of poke-routes ({REPO}) on branch `{br}`, made for this job from the latest main.
Work and commit only in {wt}. Other improve jobs run in parallel in their own worktrees; once you commit,
the queue rebases your commit onto main and the owner's server (it serves {REPO} on port 8790) picks it up.
poke-routes is a rough procedural Pokemon Emerald route generator (Python, package pokeroutes/)
with a stdlib web UI in webui/ (server.py; an editor shell: static/shell.html, shell.js, bench.js, shell-logic.js, shell.css).
The generator modules are hot-reloaded by the running server; static files are read per request. If you change
webui/*.py, say in your last message that the server needs a restart.
Route tools (MCP server "routes", webui/route_tools.py, on this worktree's code, already loaded): generate_route,
route_stats, crop_route_image, route_grid, paint_route, save_spec, generate_world, generate_region, tilers,
spec_fields, list_specs, check_examples, check_web_ui (this job's server copy on port {port}: API and pages checked,
then stopped) and screenshot_web_ui (the same plus a headless browser PNG). Use them instead of hand-written
scripts, sleeps, curl and killing servers by hand. Skills in .claude/skills/: codebase-map, route-design, tilesets.

{describe_rules_for_prompt(wt)}
How to verify before committing (do all that apply, and fix what breaks), all inside {wt}:
- python3 -m pokeroutes generate examples/bernsteinweg.yaml (and erlenbruch.yaml, kiefernheide.yaml) all succeed.
- Web UI checks: `node --check` on every changed .js file, then the check_web_ui tool.
- If the change is visual, screenshot it (screenshot_web_ui, or generate_route for maps) and look at it.

Finish with exactly one git commit on `{br}` containing all your changes (no other commits), message starting with
"improve: " followed by a short summary of the request, e.g. "improve: show walkable percentage in stats".
Commit with: {describe_commit_command_for_prompt()}
Do not commit files under out/, region_out/, vendor/, webui/improvements.json or webui/improve-logs/.
Last message: 1-3 sentences on what changed and how you verified it.
"""
    wt = ARK / "rom-wt" / f"improve-{jid}"
    br = f"arklarna/improve-{jid}"
    return f"""You are implementing an improvement the owner typed into the "Improve" box, targeting the Arklarna ROM hack.
{fix_note}
Owner's request:
<<<
{req}
>>>
{agent}
Context: {ROM} is Arklarna, a Pokemon ROM hack on pokeemerald-expansion (decomp), branch `{ROM_BRANCH}`.
Current phase is ASSETS ONLY: maps, NPCs, tilesets, sprites, text. No engine/C logic changes. If the request
needs engine logic, make no commit and say so.

{describe_rules_for_prompt(f"{wt} (and, for the final merge only, {ROM} plus copying the ROM to {RELEASE_ROM})")}
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


ROUTER_MODEL = os.environ.get("IMPROVE_ROUTER_MODEL", "claude-sonnet-5")
MODELS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5"}
ROUTER_PROMPT = """You route coding jobs to a model. Decide how hard this change request is for an
agent working in the {target} repo ({about}).

Sonnet is a strong coder and should take most jobs, including moderately complex ones: multi-file
features, new UI panels or tools, bug hunts, refactors within one area, and changes to generation
rules or parameters. Answer "opus" only for the hardest work: designing a new subsystem or algorithm
from scratch (e.g. a new terrain or world-generation model), deep changes across several modules at
once, ROM/engine work, or very vague requests that need real design judgement. When in doubt,
answer "sonnet".

Request:
<<<
{request}
>>>

Reply with exactly one line of JSON: {{"model": "opus" | "sonnet", "reason": "<10 words>"}}"""


def route_model(job):
    """Ask Sonnet how hard the job is; pick Opus for complex work, Sonnet otherwise.
    IMPROVE_MODEL pins one model for every job. Undo jobs need no thought: Sonnet."""
    if os.environ.get("IMPROVE_MODEL"):
        return os.environ["IMPROVE_MODEL"], "pinned by IMPROVE_MODEL"
    if job.get("kind") == "undo":
        return MODELS["sonnet"], "undo"
    about = "a Pokemon ROM hack (pokeemerald-expansion, C and assembly data)" if job["target"] == "rom" \
        else "a Python route generator with a stdlib web UI"
    prompt = ROUTER_PROMPT.format(target=job["target"], about=about, request=job["request"][:4000])
    try:
        out = subprocess.run([_claude(), "-p", prompt, "--model", ROUTER_MODEL, "--output-format", "json",
                              "--tools", "", "--strict-mcp-config", "--no-chrome", "--setting-sources", ""],
                             capture_output=True, text=True, timeout=90, env=_child_env(job["target"]),
                             stdin=subprocess.DEVNULL)
        text = json.loads(out.stdout).get("result", "")
        verdict = json.loads(text[text.index("{"):text.rindex("}") + 1])
        pick = "opus" if str(verdict.get("model", "")).lower().startswith("opus") else "sonnet"
        return MODELS[pick], str(verdict.get("reason", ""))[:80]
    except Exception as e:  # router trouble never blocks a job: fall back to Sonnet
        return MODELS["sonnet"], f"router failed ({type(e).__name__}), default"


def claude_cmd(cwd_target, wt=None, model=None, port=None, jid=None):
    # the job's worktree is the cwd; the log dir holds an attached screenshot
    # stdin stays open (stream-json in/out) so a running job can be steered with extra instructions
    add = [str(ARK / "rom-wt"), str(DOCS), str(RELEASE_ROM.parent), str(ARK / "tools"), str(LOGDIR)] \
        if cwd_target == "rom" else [str(LOGDIR)]
    cmd = [_claude(), "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--permission-mode", "bypassPermissions", "--strict-mcp-config", "--no-chrome",
           "--model", model or os.environ.get("IMPROVE_MODEL", MODELS["sonnet"])]
    tools = Path(wt or REPO) / "webui" / "route_tools.py"
    if cwd_target == "poke-routes" and tools.exists():   # route tools, run on the worktree's own code
        route_tools_environment = {"POKEROUTES_JOB_SERVER_PORT": str(port or PORT_BASE)}
        if jid:
            route_tools_environment["POKEROUTES_EDITOR_SPECS_FILE"] = str(editor_specs_file_for_job(jid))
        cmd += ["--mcp-config", json.dumps({"mcpServers": {"routes": {"command": sys.executable, "args": [str(tools)],
                                                                      "env": route_tools_environment}}})]
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
    env["ENABLE_TOOL_SEARCH"] = "false"
    if target == "poke-routes" and AUTHOR:
        env.update(GIT_AUTHOR_NAME=AUTHOR[0], GIT_AUTHOR_EMAIL=AUTHOR[1],
                   GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1])
    if TOOLCHAIN:
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


def _send_stdin(proc, lock, text) -> bool:
    """Write one more user turn into a running agent's stdin (its initial prompt, or a steer)."""
    msg = json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})
    with lock:
        if proc.poll() is not None:
            return False
        try:
            proc.stdin.write(msg + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return False
    return True


def _run_agent(job, cwd, wt=None, port=None):
    jid, target = job["id"], job["target"]
    if job.get("killed"):   # cancelled before the agent process existed to kill (finding 15)
        return -1, ""
    prompt = build_prompt(job, wt=wt, port=port)
    LOGDIR.mkdir(parents=True, exist_ok=True)
    (LOGDIR / f"{jid}.prompt.txt").write_text(prompt)
    if not job.get("model"):   # a re-run (conflict, restart) keeps its first decision
        job["model"], job["model_reason"] = route_model(job)
        _save()
    _log(jid, f"[router] {job['model']} ({job.get('model_reason', '')})")
    if job.get("killed"):   # cancelled while routing the model, still before any process exists
        return -1, ""
    cmd = claude_cmd(target, wt, model=job["model"], port=port, jid=jid)
    _log(jid, f"[run] {Path(cmd[0]).name} " + " ".join(cmd[1:]) + f"  (cwd {cwd})")
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.PIPE, text=True, env=_child_env(target), start_new_session=True)
    lock = threading.Lock()
    with _lock:
        _procs[jid] = proc
        _stdin_locks[jid] = lock
        job["agent_pid"] = proc.pid
    _save()
    _send_stdin(proc, lock, prompt)
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
                close_agent_input_after_result(jid, proc, lock)
            for line in _fmt_event(ev):
                _log(jid, line)
        proc.wait()
    finally:
        timer.cancel()
        with _lock:
            _procs.pop(jid, None)
            _stdin_locks.pop(jid, None)
            job.pop("agent_pid", None)
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
    return proc.returncode, final


def close_agent_input_after_result(jid, proc, stdin_lock):
    with _lock:
        _stdin_locks.pop(jid, None)
    with stdin_lock:
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass


def steer(jid: str, text: str) -> dict:
    """Send extra instructions straight into a running job's live agent (same session, no new job)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("text is empty")
    if len(text) > 4000:
        raise ValueError("text is longer than 4000 characters")
    with _lock:
        job = _jobs.get(jid)
        if not job:
            raise KeyError(jid)
        if job.get("status") != "running" or job.get("killed"):
            raise ValueError(f"job isn't running (status {job.get('status')})")
        proc, lock = _procs.get(jid), _stdin_locks.get(jid)
    if not proc or not lock or not _send_stdin(proc, lock, text):
        raise ValueError("couldn't reach the running agent (it may have just finished)")
    _log(jid, f"[steer] {text}")
    return public(job)


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
        is_merge_commit = len(_git(REPO, "rev-list", "--parents", "-n", "1", c).split()) > 2
        revert_arguments = ["revert", "--no-commit", *(["-m", "1"] if is_merge_commit else []), c]
        _log(job["id"], f"> git {' '.join(revert_arguments[:-1])} {c[:10]}")
        r = _gitr(wt, *revert_arguments, env=env)
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
    # the revert commit is on the branch now, whatever happens to the rebuild below: mark the
    # source undone right away so a build failure can't leave it offering Undo again (finding 5)
    with _lock:
        set_status(src, "undone", undone_by=jid)
    _log(jid, "> make -j10 (rebuilding the ROM after the revert)")
    env = dict(os.environ, PATH=f"{TOOLCHAIN}:{os.environ.get('PATH', '')}" if TOOLCHAIN else os.environ.get("PATH", ""))
    r = subprocess.run(["make", "-j10"], cwd=str(repo), env=env, capture_output=True, text=True, timeout=TIMEOUT)
    for line in (r.stdout + r.stderr).splitlines()[-15:]:
        _log(jid, line)
    if r.returncode:
        raise RuntimeError("ROM build after the revert failed; release ROM left as it was")
    shutil.copy2(repo / "pokeemerald.gba", RELEASE_ROM)
    _log(jid, f"[copy] {RELEASE_ROM}")
    with _lock:
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
            if job.get("kind") == "undo" and job.get("undo_of") in _jobs:
                set_status(_jobs[job["undo_of"]], "undone", undone_by=job["id"])
            _drop_worktree(job.get("worktree") or WT_ROOT / f"job-{job['id']}", br)
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


def save_screenshot_for_job(jid: str, png_bytes: bytes, number: int = 1) -> str:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    # the first keeps the old <id>.png name; the rest are <id>-2.png, <id>-3.png, ...
    screenshot_path = LOGDIR / (f"{jid}.png" if number == 1 else f"{jid}-{number}.png")
    screenshot_path.write_bytes(png_bytes)
    return str(screenshot_path)


def decode_all_screenshots(screenshot_data_urls) -> list:
    if len(screenshot_data_urls) > maximum_screenshots_per_job:
        raise ValueError(f"at most {maximum_screenshots_per_job} screenshots per request")
    all_png_bytes = [decode_png_screenshot_data_url(data_url) for data_url in screenshot_data_urls]
    if sum(map(len, all_png_bytes)) > maximum_total_screenshot_size_in_bytes:
        raise ValueError("screenshots are larger than 24 MB together")
    return all_png_bytes


maximum_editor_context_size_in_bytes = 256 * 1024


def save_editor_context_for_job(jid: str, editor_context) -> str:
    if not isinstance(editor_context, dict):
        raise ValueError("editor_context must be a JSON object")
    editor_context_text = json.dumps(editor_context, indent=1, default=str)
    if len(editor_context_text) > maximum_editor_context_size_in_bytes:
        raise ValueError("editor_context is larger than 256 KB")
    LOGDIR.mkdir(parents=True, exist_ok=True)
    editor_context_path = LOGDIR / f"{jid}.editor-context.json"
    editor_context_path.write_text(editor_context_text)
    return str(editor_context_path)


def editor_specs_file_for_job(jid: str) -> Path:
    editor_specs_path = LOGDIR / f"{jid}.editor-specs.json"
    return editor_specs_path


def read_editor_specs_offered_by_job(jid: str) -> list:
    editor_specs_path = editor_specs_file_for_job(jid)
    if not editor_specs_path.is_file():
        return []
    try:
        offered_editor_specs = json.loads(editor_specs_path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(offered_editor_specs, list):
        return []
    well_formed_editor_specs = [offered_spec for offered_spec in offered_editor_specs if is_well_formed_offered_spec(offered_spec)]
    return well_formed_editor_specs


def is_well_formed_offered_spec(offered_spec) -> bool:
    has_path_and_text = isinstance(offered_spec, dict) and isinstance(offered_spec.get("path"), str) \
        and isinstance(offered_spec.get("text"), str)
    return has_path_and_text


def features() -> dict:
    claude_executable_path = _claude()
    is_claude_installed = bool(shutil.which(claude_executable_path) or Path(claude_executable_path).is_file())
    available_features = {"improve_box": is_improve_box_enabled(), "agent": is_claude_installed,
                          "claude": claude_executable_path if is_claude_installed else None,
                          "targets": {name: target["label"] for name, target in TARGETS.items()}}
    return available_features


def submit(request: str, target: str, screenshot_data_url: str = "", mode: str = "improve",
           screenshot_data_urls=(), editor_context=None) -> dict:
    request = (request or "").strip()
    if not request:
        raise ValueError("request is empty")
    if len(request) > 4000:
        raise ValueError("request is longer than 4000 characters")
    if target not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}")
    if mode not in ("improve", "agent"):
        raise ValueError("mode must be improve or agent")
    if mode == "improve" and not is_improve_box_enabled():
        raise ValueError("the Improve box is turned off in this checkout; use the Agent tab")
    if not isinstance(screenshot_data_urls, (list, tuple)) or not all(isinstance(u, str) for u in screenshot_data_urls):
        raise ValueError("screenshots must be a list of data URLs")
    # the old single "screenshot" field and the "screenshots" list both work; together they add up
    all_png_bytes = decode_all_screenshots(([screenshot_data_url] if screenshot_data_url else []) +
                                           [u for u in screenshot_data_urls if u])
    jid = time.strftime("%m%d%H%M") + "-" + uuid.uuid4().hex[:4]
    job = {"id": jid, "kind": "improve", "request": request, "target": target, "status": "queued",
           "created": time.time(), "owner": _me}
    if mode == "agent":
        job["mode"] = "agent"
    if editor_context:
        job["editor_context"] = save_editor_context_for_job(jid, editor_context)
    if all_png_bytes:
        job["screenshots"] = [save_screenshot_for_job(jid, png_bytes, number)
                              for number, png_bytes in enumerate(all_png_bytes, 1)]
        job["screenshot"] = job["screenshots"][0]
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
        src["owner"] = _me     # claim it: an unowned (old-style) source's "undone" update must not
                                # be clobbered by its stale disk copy on the next _save (finding 4)
        uid = jid + "-undo"
        n = 1
        while uid in _jobs:
            n += 1
            uid = f"{jid}-undo{n}"
        job = {"id": uid, "kind": "undo", "undo_of": jid, "request": f"undo: {src['request'][:80]}",
               "target": src["target"], "status": "queued", "created": time.time(), "owner": _me}
        if src.get("mode") == "agent":   # keep an agent-tab undo visible in the Agent tab (finding 21)
            job["mode"] = "agent"
        _jobs[uid] = job
        _cv.notify_all()
    _save()
    return public(job)


def fix(jid: str, request: str) -> dict:
    """A follow-up job on top of a done one, for when the agent got it wrong or incomplete:
    a normal improve job (own worktree, own commit, own merge) with context about what it's
    correcting. The job it fixes stays "done"; this is independent, not a revert."""
    request = (request or "").strip()
    if not request:
        raise ValueError("request is empty")
    if len(request) > 4000:
        raise ValueError("request is longer than 4000 characters")
    with _cv:
        src = _jobs.get(jid)
        if not src or src["kind"] != "improve":
            raise KeyError(jid)
        if src["status"] != "done" or not src.get("commit"):
            raise ValueError(f"nothing to fix (status {src['status']})")
        fid = jid + "-fix"
        n = 1
        while fid in _jobs:
            n += 1
            fid = f"{jid}-fix{n}"
        job = {"id": fid, "kind": "improve", "fix_of": jid, "request": request, "target": src["target"],
               "status": "queued", "created": time.time(), "owner": _me}
        if src.get("mode") == "agent":
            job["mode"] = "agent"
        _jobs[fid] = job
        _cv.notify_all()
    _save()
    return public(job)


def retry(jid: str) -> dict:
    """A one-click do-over for a failed/timeout/conflict/cancelled job: a new job with the same
    request, target, mode and screenshots, replacing the old job in the list."""
    with _cv:
        src = _jobs.get(jid)
        if not src or src["kind"] != "improve":
            raise KeyError(jid)
        if src["status"] not in ("failed", "timeout", "conflict", "cancelled"):
            raise ValueError(f"nothing to retry (status {src['status']})")
        rid = jid + "-retry"
        n = 1
        while rid in _jobs:
            n += 1
            rid = f"{jid}-retry{n}"
        job = {"id": rid, "kind": "improve", "retry_of": jid, "request": src["request"],
               "target": src["target"], "status": "queued", "created": time.time(), "owner": _me}
        if src.get("mode") == "agent":
            job["mode"] = "agent"
        if src.get("screenshots"):
            job["screenshots"] = src["screenshots"]
            job["screenshot"] = src.get("screenshot", "")
        _jobs[rid] = job
        del _jobs[jid]
        _cv.notify_all()
    _save()
    return public(job)


def delete(jid: str) -> dict:
    """Drop a finished job (done/failed/timeout/conflict/cancelled/no-change) from the list."""
    with _lock:
        job = _jobs.get(jid)
        if not job or job["kind"] != "improve":
            raise KeyError(jid)
        if job["status"] == "queued" or job["status"] in ACTIVE:
            raise ValueError(f"can't delete a job that's still {job['status']}; cancel it first")
        del _jobs[jid]
    _save()
    return {"ok": True}


def cancel(jid: str) -> dict:
    with _lock:
        job = _jobs.get(jid)
        if not job:
            raise KeyError(jid)
        if job["status"] == "queued":
            if not _mine(job):
                raise ValueError("an older server is running this job's queue; cancel it there")
            set_status(job, "cancelled")
        elif job["status"] == "running":
            # set the flag even with no agent process yet (worktree setup, model routing, an undo's
            # revert/build): _run_agent and _do_poke check it before starting or merging (finding 15)
            job["killed"] = "cancelled"
            if _procs.get(jid):
                threading.Thread(target=_kill, args=(_procs[jid], jid, "cancelled"), daemon=True).start()
        elif job["status"] == "merging":
            raise ValueError("the job is being merged; undo it once it is done")
    _save()
    return public(job)


def pin(jid: str, pinned: bool) -> dict:
    """Highlight a finished job so it stands out in its tab and survives clear()."""
    with _lock:
        job = _jobs.get(jid)
        if not job:
            raise KeyError(jid)
        job["pinned"] = bool(pinned)
    _save()
    return public(job)


_CLEARABLE = {"done": ("done",), "error": ("failed", "timeout", "conflict"), "cancelled": ("cancelled",)}


def clear(tab: str) -> dict:
    """Dismiss every unpinned job in one history tab (Done/Error/Cancelled). Dismissed jobs stay
    in storage (other jobs' fix_of/undo_of/retry_of links keep working) but history() hides them."""
    statuses = _CLEARABLE.get(tab)
    if not statuses:
        raise ValueError(f"cannot clear tab {tab!r}")
    with _lock:
        n = 0
        for job in _jobs.values():
            if job.get("status") in statuses and not job.get("pinned") and not job.get("dismissed"):
                job["dismissed"] = True
                n += 1
    _save()
    return {"cleared": n}


def public(job, since=None) -> dict:
    d = {k: v for k, v in job.items() if k not in ("summary", "agent_pid")}
    d["summary"] = job.get("summary", "")
    d["target_label"] = TARGETS.get(job.get("target"), {}).get("label", job.get("target"))
    if job.get("status") in ACTIVE and job.get("slot") is not None and job.get("target") == "poke-routes":
        d["port"] = PORT_BASE + job["slot"]
    if _st["owner"] and not job.get("owner") and job.get("status") in ("queued",) + ACTIVE:
        lp = _st["legacy"]
        d["held_by"] = f"older server pid {', '.join(map(str, lp))}" if lp else "older server (adopting soon)"
    offered_editor_specs = read_editor_specs_offered_by_job(job["id"]) if job.get("mode") == "agent" else []
    if offered_editor_specs:
        d["editor_specs"] = offered_editor_specs if since is not None else \
            [{"path": offered_spec["path"], "note": offered_spec.get("note", "")} for offered_spec in offered_editor_specs]
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
    jobs = sorted((j for j in _view_jobs().values() if not j.get("dismissed")),
                  key=lambda j: j.get("created", 0), reverse=True)
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


# ------------------------------------------------------------------ sessions (the Improve tab's graph)

maximum_commits_in_session_graph = 400
graph_field_separator = "\x1f"


def main_head_commit() -> str:
    main_head = _git(REPO, "rev-parse", f"refs/heads/{MAIN}")
    return main_head


def first_unpushed_session_root() -> str:
    pushed_main = _git(REPO, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{MAIN}", check=False)
    main_head = main_head_commit()
    if pushed_main and _is_ancestor(REPO, pushed_main, main_head):
        return pushed_main
    return main_head


def current_session() -> dict:
    session = _meta.get("session")
    if session and session.get("root"):
        return session
    session = {"number": len(_meta.get("sessions") or []) + 1, "root": first_unpushed_session_root(), "started": time.time()}
    if not _st["owner"]:
        return session
    _meta["session"] = session
    _save_meta()
    return session


def merged_job_subjects_since(session_root: str, main_head: str) -> list:
    merge_subjects = _git(REPO, "log", "--first-parent", "--merges", "--format=%s", f"{session_root}..{main_head}").splitlines()
    return merge_subjects


def finish_session(session_message: str = "") -> dict:
    with _merge_lock:
        active_job_ids = [job["id"] for job in _jobs.values() if job.get("status") in ACTIVE]
        if active_job_ids:
            raise ValueError(f"wait until the running jobs finish first ({', '.join(active_job_ids)})")
        session = current_session()
        old_main_head = main_head_commit()
        if old_main_head == session["root"]:
            raise ValueError("nothing has landed in this session yet")
        merged_subjects = merged_job_subjects_since(session["root"], old_main_head)
        session_commit = squash_session_into_one_commit(session, old_main_head, session_message, merged_subjects)
        archive_reference = f"refs/sessions/{session['number']}"
        _git(REPO, "update-ref", archive_reference, old_main_head)
        _git(REPO, "update-ref", f"refs/heads/{MAIN}", session_commit, old_main_head)
        finished_session = {**session, "finished": time.time(), "archive": archive_reference, "commit": session_commit,
                            "merged_jobs": len(merged_subjects)}
        _meta["sessions"] = (_meta.get("sessions") or []) + [finished_session]
        _meta["session"] = {"number": session["number"] + 1, "root": session_commit, "started": time.time()}
        _save_meta()
    return {"finished": finished_session, "session": _meta["session"]}


def squash_session_into_one_commit(session, old_main_head, session_message, merged_subjects) -> str:
    session_tree = _git(REPO, "rev-parse", f"{old_main_head}^{{tree}}")
    headline = session_message.strip() or f"session {session['number']}: {len(merged_subjects)} change(s)"
    listed_changes = "".join(f"- {merged_subject}\n" for merged_subject in merged_subjects)
    full_message = f"{headline}\n\n{listed_changes}\nFull history: refs/sessions/{session['number']}\n"
    session_commit = _git(REPO, "commit-tree", session_tree, "-p", session["root"], "-m", full_message, env=_git_env())
    return session_commit


def job_branch_references() -> list:
    job_branches = _git(REPO, "for-each-ref", "--format=%(refname)", "refs/heads/improve/").splitlines()
    return job_branches


def parse_graph_log_line(log_line: str):
    fields = log_line.split(graph_field_separator)
    if len(fields) != 8:
        return None
    boundary_mark, sha, short_sha, parents, author, timestamp, subject, decorations = fields
    graph_commit = {"sha": sha, "short": short_sha, "parents": parents.split() if parents else [], "author": author,
                    "time": int(timestamp or 0), "subject": subject, "boundary": boundary_mark == "-",
                    "refs": [decoration.strip() for decoration in decorations.split(",") if decoration.strip()]}
    return graph_commit


def commits_between(session_root: str, tip_references: list) -> list:
    log_format = graph_field_separator.join(["%m", "%H", "%h", "%P", "%an", "%at", "%s", "%D"])
    log_run = _gitr(REPO, "log", "--topo-order", "--boundary", f"-{maximum_commits_in_session_graph}", f"--format={log_format}",
                    *tip_references, f"^{session_root}")
    graph_commits = [graph_commit for graph_commit in map(parse_graph_log_line, log_run.stdout.splitlines()) if graph_commit]
    if not any(graph_commit["sha"] == session_root for graph_commit in graph_commits):
        root_line = _gitr(REPO, "log", "-1", f"--format=-{graph_field_separator}" + log_format.split(graph_field_separator, 1)[1], session_root).stdout.strip()
        root_commit = parse_graph_log_line(root_line)
        graph_commits += [root_commit] if root_commit else []
    return graph_commits


def describe_active_job_for_graph(job) -> dict:
    branch_tip = _git(REPO, "rev-parse", "-q", "--verify", f"refs/heads/{job['branch']}", check=False) if job.get("branch") else ""
    active_job = {"id": job["id"], "status": job.get("status"), "slot": job.get("slot"), "mode": job.get("mode", "improve"),
                  "request": job.get("request", "")[:120], "branch": job.get("branch"), "tip": branch_tip or job.get("base")}
    return active_job


def session_graph(viewed_session_number=None) -> dict:
    if not _st["owner"]:
        _load_meta()
    past_sessions = _meta.get("sessions") or []
    viewed_past_session = next((past for past in past_sessions if past.get("number") == viewed_session_number), None)
    if viewed_past_session:
        graph_commits = commits_between(viewed_past_session["root"], [viewed_past_session["archive"]])
        return {"session": viewed_past_session, "live": False, "commits": graph_commits, "active_jobs": [],
                "past_sessions": past_sessions}
    session = current_session()
    active_jobs = [describe_active_job_for_graph(job) for job in _view_jobs().values() if job.get("status") in ACTIVE]
    active_job_branches = [f"refs/heads/{active_job['branch']}" for active_job in active_jobs
                           if active_job["branch"] and f"refs/heads/{active_job['branch']}" in job_branch_references()]
    graph_commits = commits_between(session["root"], [f"refs/heads/{MAIN}", *active_job_branches])
    live_graph = {"session": session, "live": True, "commits": graph_commits, "active_jobs": active_jobs,
                  "past_sessions": past_sessions, "main": main_head_commit()}
    return live_graph


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
            if parts == ["features"]:
                return h._json(features())
            if parts == ["session"]:
                from urllib.parse import parse_qs
                viewed_session_text = (parse_qs(u.query).get("number") or [""])[0]
                return h._json(session_graph(int(viewed_session_text) if viewed_session_text.isdigit() else None))
            if parts == ["usage"]:
                if not is_improve_box_enabled():
                    return h._json({"error": "the Improve box is turned off in this checkout"}, 404)
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
                                  str(body.get("screenshot") or ""), str(body.get("mode") or "improve"),
                                  body.get("screenshots") or [], body.get("editor_context")), 202)
        if parts == ["workers"]:
            return h._json(set_workers(body.get("workers")))
        if parts == ["session", "finish"]:
            return h._json(finish_session(str(body.get("message") or "")))
        if parts == ["clear"]:
            return h._json(clear(str(body.get("status", ""))))
        if len(parts) == 2 and parts[1] == "undo":
            return h._json(undo(parts[0]), 202)
        if len(parts) == 2 and parts[1] == "fix":
            return h._json(fix(parts[0], str(body.get("request", ""))), 202)
        if len(parts) == 2 and parts[1] == "retry":
            return h._json(retry(parts[0]), 202)
        if len(parts) == 2 and parts[1] == "cancel":
            return h._json(cancel(parts[0]))
        if len(parts) == 2 and parts[1] == "delete":
            return h._json(delete(parts[0]))
        if len(parts) == 2 and parts[1] == "steer":
            return h._json(steer(parts[0], str(body.get("text", ""))))
        if len(parts) == 2 and parts[1] == "pin":
            return h._json(pin(parts[0], bool(body.get("pinned"))))
        return h._json({"error": "not found"}, 404)
    except KeyError:
        return h._json({"error": "unknown job"}, 404)
    except ValueError as e:
        return h._json({"error": str(e)}, 400)
