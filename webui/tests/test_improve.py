"""Tests for the improve queue (webui/improve.py): state machine, startup recovery, the queue
lock, the merge step, the memory guard, and whole jobs with a fake claude.

    python3 -m unittest webui/tests/test_improve.py -v

Everything runs in temp dirs (own git repo, state file, lock); nothing touches the live queue.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ["IMPROVE_WORKLOG"] = "0"
os.environ["IMPROVE_CLAUDE"] = str(HERE / "fake_claude.py")
os.environ.pop("IMPROVE_WORKERS", None)
_TMP0 = tempfile.mkdtemp(prefix="improve-test-")
os.environ["IMPROVE_STATE"] = str(Path(_TMP0) / "state.json")   # never the live file, even on import
sys.path.insert(0, str(HERE.parent))
import improve  # noqa: E402

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                     6575.
Pages active:                                 119108.
Pages inactive:                               116372.
Pages speculative:                              2433.
Pages wired down:                             129698.
"""


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@x", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def make_repo(root):
    repo = Path(root) / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\ntwo\nthree\n")
    (repo / "b.txt").write_text("bee\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "start")
    return repo


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="improve-test-"))
        self.repo = make_repo(self.tmp)
        I = improve
        I.release_lock()
        I.REPO = self.repo
        I.TARGETS["poke-routes"]["repo"] = self.repo
        I.STATE = self.tmp / "state.json"
        I.LOCKFILE = self.tmp / "state.json.lock"
        I.META = self.tmp / "state.meta.json"
        I.LOGDIR = self.tmp / "logs"
        I.USAGE = I.LOGDIR / "usage.json"
        I.WT_ROOT = self.tmp / "wt"
        I.ROOT_WAIT = 0
        I.LEGACY_GRACE = 0
        I.MEM_MIN_MB = 0
        I.START_GAP = 0.2
        I._jobs.clear()
        I._meta.clear()
        I._slots.clear()
        I._st.update(owner=False, workers=1, last_start=0.0, mem=None, mem_wait=False, legacy=[],
                     legacy_idle_since=None, last_write=None, started=False)

    def tearDown(self):
        improve._st["workers"] = 0          # running worker threads exit
        with improve._cv:
            improve._cv.notify_all()
        time.sleep(0.05)
        improve.release_lock()

    def write_state(self, jobs):
        improve.STATE.write_text(json.dumps(jobs))


class StateMachine(Base):
    def test_allowed_path(self):
        j = {"id": "x", "status": "queued"}
        for s in ("running", "merging", "running", "merging", "done", "undone"):
            improve.set_status(j, s)
        self.assertEqual(j["status"], "undone")

    def test_rejects_bad_moves(self):
        for cur, new in (("queued", "done"), ("done", "queued"), ("cancelled", "running"),
                         ("conflict", "queued"), ("undone", "done"), ("queued", "merging")):
            with self.assertRaises(improve.TransitionError, msg=f"{cur}->{new}"):
                improve.set_status({"id": "x", "status": cur}, new)

    def test_note_and_fields(self):
        j = improve.set_status({"id": "x", "status": "running"}, "queued", note="why", slot=None)
        self.assertEqual(j["notes"], ["why"])
        self.assertIn("updated", j)

    def test_pick_next_fifo_rom_cap(self):
        me = improve._me
        jobs = {
            "r1": {"id": "r1", "target": "rom", "status": "running", "owner": me, "created": 1},
            "r2": {"id": "r2", "target": "rom", "status": "queued", "owner": me, "created": 2},
            "p1": {"id": "p1", "target": "poke-routes", "status": "queued", "owner": me, "created": 3},
            "p0": {"id": "p0", "target": "poke-routes", "status": "queued", "created": 0},  # old-style, not ours
        }
        self.assertEqual(improve.pick_next(jobs)["id"], "p1")      # r2 waits for r1
        jobs["r1"]["status"] = "done"
        self.assertEqual(improve.pick_next(jobs)["id"], "r2")


class Recovery(Base):
    def lost(self, jid, created):
        return {"id": jid, "kind": "improve", "request": jid, "target": "poke-routes", "status": "failed",
                "created": created, "error": improve.LOST_ERR}

    def test_startup_recovery(self):
        dead = 999999
        self.write_state([
            {"id": "run", "kind": "improve", "request": "r", "target": "poke-routes", "status": "running",
             "created": 1, "owner": dead, "slot": 0, "started": 5},
            {"id": "run2", "kind": "improve", "request": "r", "target": "poke-routes", "status": "running",
             "created": 2, "owner": dead, "requeued": 1},
            {"id": "q", "kind": "improve", "request": "q", "target": "poke-routes", "status": "queued",
             "created": 3, "owner": dead},
            {"id": "legacy", "kind": "improve", "request": "l", "target": "poke-routes", "status": "queued",
             "created": 4},
            {"id": "ok", "kind": "improve", "request": "o", "target": "poke-routes", "status": "done",
             "created": 5, "commit": "abc"},
            self.lost("lost1", 6), self.lost("lost2", 7),
            {"id": "realfail", "kind": "improve", "request": "f", "target": "poke-routes", "status": "failed",
             "created": 8, "error": "RuntimeError: boom"},
        ])
        spawn = improve._spawn_workers
        improve._spawn_workers = lambda: None
        try:
            self.assertTrue(improve.try_acquire_lock(1234))
            improve._become_owner()
        finally:
            improve._spawn_workers = spawn
        J = improve._jobs
        self.assertEqual(J["run"]["status"], "queued")
        self.assertEqual(J["run"]["requeued"], 1)
        self.assertTrue(any("requeued" in n for n in J["run"]["notes"]))
        self.assertNotIn("slot", J["run"])
        self.assertEqual(J["run2"]["status"], "failed")              # only requeued once
        self.assertEqual(J["q"]["status"], "queued")
        self.assertEqual(J["q"]["owner"], improve._me)
        self.assertNotIn("owner", J["legacy"])                        # left to its old-style server
        self.assertEqual(J["ok"]["status"], "done")
        for k in ("lost1", "lost2"):
            self.assertEqual(J[k]["status"], "queued")
            self.assertEqual(J[k]["owner"], improve._me)
            self.assertNotIn("error", J[k])
        self.assertEqual(J["realfail"]["status"], "failed")
        disk = {j["id"]: j for j in json.loads(improve.STATE.read_text())}
        self.assertEqual(disk["lost1"]["status"], "queued")
        self.assertEqual(json.loads(improve.META.read_text())["migrated_ids"], ["lost1", "lost2"])

    def test_recovered_merged_undo_marks_source_undone_and_drops_branch(self):
        # the server died right after an undo job's branch landed on main but before it could
        # record "done"; recover_interrupted() sees the commit already there and should also
        # undo the source and clean up the branch it no longer needs (finding 48)
        wt = self.tmp / "wt-u1"
        base = git(self.repo, "rev-parse", "main")
        br = "improve/job-u1"
        git(self.repo, "worktree", "add", "-q", "-b", br, str(wt), base)
        (wt / "b.txt").write_text("reverted\n")
        git(wt, "add", "b.txt")
        git(wt, "commit", "-q", "-m", "Revert improve: write b.txt")
        tip = git(wt, "rev-parse", "HEAD")
        git(self.repo, "worktree", "remove", "--force", str(wt))   # the crash: worktree gone, branch left
        git(self.repo, "update-ref", "refs/heads/main", tip)       # ...but the merge had already landed
        src = {"id": "src", "kind": "improve", "request": "r", "target": "poke-routes",
               "status": "done", "commit": "c1", "created": 1}
        job = {"id": "u1", "kind": "undo", "undo_of": "src", "status": "merging", "branch": br,
               "base": base, "worktree": str(wt), "created": 2, "owner": 999999}
        improve._jobs["src"] = src
        improve._jobs["u1"] = job
        improve.recover_interrupted(job)
        self.assertEqual(job["status"], "done")
        self.assertEqual(src["status"], "undone")
        self.assertEqual(src["undone_by"], "u1")
        self.assertEqual(git(self.repo, "branch", "--list", br), "")

    def test_migration_runs_once(self):
        improve._meta["migrated_lost_v1"] = 1
        improve.META.write_text(json.dumps(improve._meta))
        self.write_state([self.lost("late", 1)])
        spawn = improve._spawn_workers
        improve._spawn_workers = lambda: None
        try:
            improve.try_acquire_lock(1)
            improve._become_owner()
        finally:
            improve._spawn_workers = spawn
        self.assertEqual(improve._jobs["late"]["status"], "failed")

    def test_old_style_jobs_adopted_when_idle(self):
        self.write_state([{"id": "legacy", "kind": "improve", "request": "l", "target": "poke-routes",
                           "status": "queued", "created": 1},
                          {"id": "legacy-run", "kind": "improve", "request": "l", "target": "poke-routes",
                           "status": "running", "created": 2}])
        spawn = improve._spawn_workers
        improve._spawn_workers = lambda: None
        real = improve._legacy_workers
        try:
            improve.try_acquire_lock(1)
            improve._become_owner()
            improve._legacy_workers = lambda: [4242]          # an old server is busy: hands off
            improve._adopt_unowned()
            self.assertNotIn("owner", improve._jobs["legacy"])
            self.assertIn("held_by", improve.public(improve._jobs["legacy"]))
            improve._legacy_workers = lambda: []              # idle (grace 0 in tests): adopt
            improve._adopt_unowned()
            improve._adopt_unowned()
        finally:
            improve._spawn_workers = spawn
            improve._legacy_workers = real
        self.assertEqual(improve._jobs["legacy"]["owner"], improve._me)
        self.assertEqual(improve._jobs["legacy-run"]["status"], "queued")

    def test_undo_claims_unowned_source_so_undone_sticks(self):
        # the source job came from an old-style server (no "owner" field) and is already on disk
        # as "done"; undo() must claim it so the later "undone" update is not overwritten by that
        # stale disk copy on the next _save (finding 4)
        improve.try_acquire_lock(1)
        improve._st["owner"] = True
        src = {"id": "src", "kind": "improve", "request": "r", "target": "poke-routes",
               "status": "done", "commit": "c1", "created": 1}
        improve._jobs["src"] = src
        improve._save()   # disk now holds the unowned, "done" copy
        improve.undo("src")
        self.assertEqual(src["owner"], improve._me)
        improve.set_status(src, "undone", undone_by="src-undo")
        improve._save()
        self.assertEqual(improve._jobs["src"]["status"], "undone")
        disk = {j["id"]: j for j in json.loads(improve.STATE.read_text())}
        self.assertEqual(disk["src"]["status"], "undone")

    def test_other_writers_cannot_clobber_owned_jobs(self):
        improve.try_acquire_lock(1)
        improve._st["owner"] = True
        improve._jobs["mine"] = {"id": "mine", "status": "queued", "owner": improve._me, "created": 1}
        improve._save()
        # an old-style server rewrites the file from its own memory, marking it failed and adding one
        self.write_state([{"id": "mine", "status": "failed", "error": improve.LOST_ERR, "created": 1},
                          {"id": "theirs", "status": "queued", "created": 2}])
        improve._save()
        disk = {j["id"]: j for j in json.loads(improve.STATE.read_text())}
        self.assertEqual(disk["mine"]["status"], "queued")
        self.assertEqual(disk["theirs"]["status"], "queued")


LOCKER = textwrap.dedent("""
    import os, sys, time
    os.environ["IMPROVE_STATE"] = sys.argv[1]
    sys.path.insert(0, sys.argv[2])
    import improve
    print("got" if improve.try_acquire_lock(int(sys.argv[3])) else "busy", flush=True)
    time.sleep(30)
""")


class Lock(Base):
    def test_one_owner_and_takeover(self):
        p = subprocess.Popen([sys.executable, "-c", LOCKER, str(improve.STATE), str(HERE.parent), "8123"],
                             stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), "got")
            self.assertFalse(improve.try_acquire_lock(9999))
            self.assertEqual(improve._lock_holder(), {"pid": p.pid, "port": 8123})
            q = subprocess.Popen([sys.executable, "-c", LOCKER, str(improve.STATE), str(HERE.parent), "8124"],
                                 stdout=subprocess.PIPE, text=True)
            self.assertEqual(q.stdout.readline().strip(), "busy")
            q.kill()
            q.wait()
        finally:
            p.kill()
            p.wait()
        self.assertIsNone(improve._lock_holder())     # the dead owner's lock is gone with it
        self.assertTrue(improve.try_acquire_lock(9999))
        self.assertEqual(improve._lock_holder(), {"pid": improve._me, "port": 9999})

    def test_non_owner_forwards_or_explains(self):
        out, code = improve._forward("/api/improve", {"request": "x"})
        self.assertEqual(code, 503)
        self.assertIn("no server owns", out["error"])


class Cancel(Base):
    def test_cancel_running_job_with_no_agent_process_yet_still_flags_it(self):
        # worktree setup, model routing and an undo's revert/build all run before _procs gets an
        # entry for the job; cancel used to do nothing in that window (finding 15)
        improve.try_acquire_lock(1)
        improve._st["owner"] = True
        job = {"id": "r1", "kind": "improve", "status": "running", "owner": improve._me,
               "target": "poke-routes", "request": "r", "created": 1}
        improve._jobs["r1"] = job
        improve.cancel("r1")
        self.assertEqual(job["killed"], "cancelled")
        code, final = improve._run_agent(job, cwd=self.repo)
        self.assertEqual((code, final), (-1, ""))   # the agent is never even started
        self.assertNotIn("r1", improve._procs)


class Merge(Base):
    def job_wt(self, name, edit):
        wt = self.tmp / f"wt-{name}"
        base = git(self.repo, "rev-parse", "main")
        git(self.repo, "worktree", "add", "-q", "-b", f"job-{name}", str(wt), base)
        for f, text in edit.items():
            (wt / f).write_text(text)
            git(wt, "add", f)
        git(wt, "commit", "-q", "-m", f"improve: {name}")
        return wt, base

    def main_commit(self, f, text, msg="other"):
        (self.repo / f).write_text(text)
        git(self.repo, "add", f)
        git(self.repo, "commit", "-q", "-m", msg)

    def test_fast_forward_and_root_updated(self):
        wt, base = self.job_wt("a", {"b.txt": "bee 2\n"})
        res = improve.merge_onto_main(self.repo, wt, base)
        self.assertTrue(res["ok"])
        self.assertEqual(git(self.repo, "rev-parse", "main"), res["head"])
        self.assertEqual((self.repo / "b.txt").read_text(), "bee 2\n")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_rebase_onto_moved_main_keeps_others(self):
        wt, base = self.job_wt("a", {"b.txt": "bee 2\n"})
        self.main_commit("a.txt", "one\ntwo\nthree\nfour\n")
        other = git(self.repo, "rev-parse", "main")
        res = improve.merge_onto_main(self.repo, wt, base)
        self.assertTrue(res["ok"])
        self.assertEqual(git(self.repo, "rev-parse", "main~1"), other)    # others' commit untouched, below ours
        self.assertEqual(len(res["commits"]), 1)
        self.assertEqual(git(self.repo, "log", "-1", "--format=%an", "main"), "t")   # author kept
        self.assertEqual((self.repo / "a.txt").read_text(), "one\ntwo\nthree\nfour\n")
        self.assertEqual((self.repo / "b.txt").read_text(), "bee 2\n")

    def test_conflict_aborts_cleanly(self):
        wt, base = self.job_wt("a", {"a.txt": "ONE\ntwo\nthree\n"})
        self.main_commit("a.txt", "uno\ntwo\nthree\n")
        before = git(self.repo, "rev-parse", "main")
        tip = git(wt, "rev-parse", "HEAD")
        res = improve.merge_onto_main(self.repo, wt, base)
        self.assertFalse(res["ok"])
        self.assertEqual(res["files"], ["a.txt"])
        self.assertEqual(git(self.repo, "rev-parse", "main"), before)
        self.assertEqual(git(wt, "rev-parse", "HEAD"), tip)                  # branch as the job left it
        self.assertEqual(git(wt, "status", "--porcelain"), "")
        self.assertFalse((Path(git(wt, "rev-parse", "--git-dir")) / "rebase-merge").exists())

    def test_dirty_root_moves_ref_then_catches_up(self):
        wt, base = self.job_wt("a", {"b.txt": "bee 2\n"})
        (self.repo / "b.txt").write_text("someone typing\n")          # uncommitted edit, same file
        (self.repo / "a.txt").write_text("one\ntwo\nthree\nlocal\n")  # and an unrelated one
        res = improve.merge_onto_main(self.repo, wt, base, root_wait=0)
        self.assertTrue(res["ok"])
        self.assertFalse(res["root_synced"])
        self.assertIn("not updated", res["note"])
        self.assertEqual(git(self.repo, "rev-parse", "main"), res["head"])
        self.assertEqual((self.repo / "b.txt").read_text(), "someone typing\n")    # their edit kept
        self.assertEqual(improve._meta["root_stale_from"], base)
        (self.repo / "b.txt").write_text("bee\n")                     # they drop their edit
        self.assertTrue(improve._sync_root(self.repo))
        self.assertEqual((self.repo / "b.txt").read_text(), "bee 2\n")
        self.assertEqual((self.repo / "a.txt").read_text(), "one\ntwo\nthree\nlocal\n")  # unrelated kept
        self.assertEqual(git(self.repo, "diff", "--cached", "--name-only"), "")
        self.assertNotIn("root_stale_from", improve._meta)

    def test_root_on_other_branch_only_moves_ref(self):
        git(self.repo, "checkout", "-q", "-b", "side")
        wt, base = self.job_wt("a", {"b.txt": "bee 2\n"})
        git(self.repo, "update-ref", "refs/heads/main", base)
        res = improve.merge_onto_main(self.repo, wt, base)
        self.assertTrue(res["ok"])
        self.assertEqual(git(self.repo, "rev-parse", "main"), res["head"])
        self.assertEqual(git(self.repo, "branch", "--show-current"), "side")


class MemoryGuard(Base):
    def test_parse_vm_stat(self):
        mb = improve.mem_available_mb(VM_STAT)
        self.assertAlmostEqual(mb, (6575 + 116372) * 16384 / 2**20, places=3)
        self.assertIsNone(improve.mem_available_mb("no vm_stat here"))

    def test_threshold(self):
        self.assertFalse(improve.mem_ok(1000, 1536))
        self.assertTrue(improve.mem_ok(2000, 1536))
        self.assertTrue(improve.mem_ok(None, 1536))

    def test_worker_waits_for_memory(self):
        improve.MEM_MIN_MB = 1536
        real = improve.mem_available_mb
        free = {"mb": 900}
        improve.mem_available_mb = lambda text=None: free["mb"]
        try:
            improve.try_acquire_lock(1)
            improve._st["owner"] = True
            j = improve.submit("write b.txt from memory test", "poke-routes")
            improve._st["workers"] = 1
            improve._spawn_workers()
            # poll instead of a fixed sleep: under a loaded test run the worker thread can take
            # longer than any fixed delay to get scheduled and hit the memory check
            deadline = time.time() + 10
            while not improve._st["mem_wait"] and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(improve._st["mem_wait"])
            self.assertEqual(improve._jobs[j["id"]]["status"], "queued")
            free["mb"] = 4000
            deadline = time.time() + 30
            while "finished" not in improve._jobs[j["id"]] and time.time() < deadline:
                time.sleep(0.2)
            self.assertEqual(improve._jobs[j["id"]]["status"], "done")
        finally:
            improve.mem_available_mb = real


class Screenshots(Base):
    PNG = "data:image/png;base64," + __import__("base64").b64encode(improve.png_file_signature + b"x").decode()

    def test_old_field_and_list_are_all_saved_and_listed(self):
        j = improve.submit("look at these", "poke-routes", self.PNG, screenshot_data_urls=[self.PNG, self.PNG])
        paths = improve._jobs[j["id"]]["screenshots"]
        self.assertEqual([Path(p).name for p in paths], [f"{j['id']}.png", f"{j['id']}-2.png", f"{j['id']}-3.png"])
        self.assertEqual(improve._jobs[j["id"]]["screenshot"], paths[0])
        self.assertTrue(all(Path(p).read_bytes().startswith(improve.png_file_signature) for p in paths))
        note = improve.describe_attached_screenshot_for_prompt(improve._jobs[j["id"]])
        self.assertIn("3 screenshots", note)
        self.assertTrue(all(p in note for p in paths))

    def test_one_screenshot_prompt_unchanged_and_cap(self):
        j = improve.submit("look", "poke-routes", self.PNG)
        self.assertIn("attached a screenshot with", improve.describe_attached_screenshot_for_prompt(improve._jobs[j["id"]]))
        with self.assertRaises(ValueError):
            improve.submit("too many", "poke-routes", screenshot_data_urls=[self.PNG] * 7)


class Jobs(Base):
    """Whole jobs through the workers with the fake claude."""

    def run_jobs(self, requests, workers=3, timeout=60):
        improve.try_acquire_lock(1)
        improve._st["owner"] = True
        ids = [improve.submit(r, "poke-routes")["id"] for r in requests]
        improve._st["workers"] = workers
        improve._spawn_workers()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if all("finished" in improve._jobs[i] and improve._jobs[i]["status"] not in ("queued", "running", "merging")
                   for i in ids):
                break
            time.sleep(0.2)
        return [improve._jobs[i] for i in ids]

    def test_parallel_jobs_merge_and_conflict_reruns(self):
        improve._st["last_start"] = 0
        jobs = self.run_jobs(["write c.txt sea; sleep 1", "write d.txt dee; sleep 1",
                              "write a.txt changed; race; sleep 1"], workers=3)
        for j in jobs:
            self.assertEqual(j["status"], "done", j)
        self.assertEqual(jobs[2]["attempt"], 2)                     # conflicted once, re-ran on fresh main
        self.assertTrue(any("conflict" in n for n in jobs[2]["notes"]))
        self.assertEqual((self.repo / "a.txt").read_text(), "changed\n")
        self.assertEqual((self.repo / "c.txt").read_text(), "sea\n")
        subjects = git(self.repo, "log", "--format=%s", "main").splitlines()
        self.assertIn("other: a.txt", subjects)                     # the racing commit is kept
        self.assertEqual(sum(s.startswith("improve: ") for s in subjects), 3)
        self.assertEqual(git(self.repo, "worktree", "list", "--porcelain").count("worktree "), 1)
        self.assertEqual(git(self.repo, "branch", "--list", "improve/*"), "")
        starts = sorted(j["started"] for j in jobs)
        self.assertLess(starts[-1] - starts[0], 15)                 # ran side by side, not one after another

    def test_conflict_twice_keeps_branch(self):
        real = improve.merge_onto_main
        improve.merge_onto_main = lambda *a, **k: {"ok": False, "conflict": True, "files": ["a.txt"],
                                                   "details": "CONFLICT (content)"}
        try:
            [j] = self.run_jobs(["write a.txt mine"], workers=1)
        finally:
            improve.merge_onto_main = real
        self.assertEqual(j["status"], "conflict")
        self.assertEqual(j["attempt"], 2)
        self.assertIn("a.txt", j["error"])
        self.assertEqual(git(self.repo, "branch", "--list", j["branch"]).strip("* "), j["branch"])
        self.assertFalse(Path(j["worktree"]).exists())

    def test_undo_goes_through_the_queue(self):
        [j] = self.run_jobs(["write b.txt changed"], workers=1)
        self.assertEqual(j["status"], "done")
        u = improve.undo(j["id"])
        deadline = time.time() + 30
        while "finished" not in improve._jobs[u["id"]] and time.time() < deadline:
            time.sleep(0.2)
        self.assertEqual(improve._jobs[u["id"]]["status"], "done")
        self.assertEqual(improve._jobs[j["id"]]["status"], "undone")
        self.assertEqual((self.repo / "b.txt").read_text(), "bee\n")
        self.assertTrue(git(self.repo, "log", "-1", "--format=%s", "main").startswith("Revert \"improve: write b.txt"))

    def test_undo_of_an_agent_job_keeps_mode_agent(self):
        improve.try_acquire_lock(1)
        improve._st["owner"] = True
        src = {"id": "src", "kind": "improve", "request": "r", "target": "poke-routes", "mode": "agent",
               "status": "done", "commit": "c1", "created": 1}
        improve._jobs["src"] = src
        u = improve.undo("src")
        self.assertEqual(u["mode"], "agent")

    def test_no_commit_is_no_change(self):
        [j] = self.run_jobs(["just look around"], workers=1)
        self.assertEqual(j["status"], "no-change")
        self.assertEqual(git(self.repo, "branch", "--list", "improve/*"), "")


class RomUndo(Base):
    """_do_undo_rom with git and the rebuild faked out, so no real ROM checkout is touched."""

    def test_failed_rebuild_still_marks_source_undone(self):
        I = improve

        def fake_git(repo, *args, **kwargs):
            if args[:2] == ("branch", "--show-current"):
                return I.ROM_BRANCH
            if args[0] == "rev-parse":
                return "deadbeef0123"
            return ""   # diff / revert / commit: nothing staged, nothing conflicts

        real_git, real_run, real_copy = I._git, I.subprocess.run, I.shutil.copy2
        I._git = fake_git
        I.subprocess.run = lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "", "stderr": "make: *** [x] Error 1"})()
        I.shutil.copy2 = lambda *a, **k: self.fail("rebuild failed; the release ROM must not be touched")
        try:
            src = {"id": "src", "kind": "improve", "request": "r", "target": "rom",
                   "status": "done", "commit": "c1", "commits": ["c1"], "created": 1}
            job = {"id": "u1", "kind": "undo", "undo_of": "src", "target": "rom", "status": "running", "created": 2}
            I._jobs["src"] = src
            I._jobs["u1"] = job
            with self.assertRaises(RuntimeError):
                I._do_undo_rom(job)
            self.assertEqual(src["status"], "undone")           # the revert commit landed
            self.assertEqual(src["undone_by"], "u1")
            self.assertEqual(job.get("commit"), "deadbeef0123")  # revert commit recorded
            self.assertNotEqual(job.get("status"), "done")        # the build failure itself is reported
        finally:
            I._git, I.subprocess.run, I.shutil.copy2 = real_git, real_run, real_copy


if __name__ == "__main__":
    unittest.main()
