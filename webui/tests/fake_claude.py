#!/usr/bin/env python3
"""Stand-in for `claude -p` in the improve-queue tests (IMPROVE_CLAUDE=this file).

Reads the owner's request out of the prompt and acts on it in the cwd (the job's worktree):
    write <file> <text>     replace <file> with <text> and commit it as "improve: write <file>"
    race                    first time only: also commit a different <file> straight onto main in
                            the main repo first, so this job's merge conflicts once
    sleep <seconds>         wait before doing anything (to see jobs run side by side)
Prints stream-json events like the real thing.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

# the real CLI now feeds the prompt as a stream-json user turn on stdin (-p with --input-format
# stream-json takes no prompt argument), so read our first turn there instead of from argv.
_msg = json.loads(sys.stdin.readline())
prompt = "".join(c.get("text", "") for c in _msg["message"]["content"] if isinstance(c, dict))
req = prompt.split("<<<\n", 1)[1].split("\n>>>", 1)[0]
repo = re.search(r"worktree of poke-routes \((.+?)\) on branch", prompt).group(1)
cwd = Path.cwd()


def say(text):
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}), flush=True)


def git(where, *args):
    for _ in range(50):          # the queue may hold the main repo's index.lock for a moment
        if subprocess.run(["git", "-C", str(where), *args], capture_output=True).returncode == 0:
            return
        time.sleep(0.1)
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)


print(json.dumps({"type": "system", "subtype": "init", "model": "fake", "cwd": str(cwd)}), flush=True)
cmds = [c.strip() for c in req.split(";")]
m = re.search(r"sleep (\d+(?:\.\d+)?)", req)
if m:
    time.sleep(float(m.group(1)))
m = next((re.match(r"write (\S+) (.+)", c) for c in cmds if c.startswith("write ")), None)
if m:
    name, text = m.group(1), m.group(2).strip()
    marker = cwd.parent / f".raced-{name.replace('/', '_')}"
    if "race" in cmds and not marker.exists():
        marker.write_text("x")
        (Path(repo) / name).write_text("someone else got here first\n")
        git(repo, "add", name)
        git(repo, "-c", "user.name=other", "-c", "user.email=o@x", "commit", "-q", "-m", f"other: {name}")
        say("raced main")
    (cwd / name).parent.mkdir(parents=True, exist_ok=True)
    (cwd / name).write_text(text + "\n")
    git(cwd, "add", name)
    git(cwd, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", f"improve: write {name}")
    say(f"wrote {name}")
print(json.dumps({"type": "result", "subtype": "success", "duration_ms": 10, "num_turns": 1,
                  "result": "fake run finished"}), flush=True)
