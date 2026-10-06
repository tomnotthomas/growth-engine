"""Engine self-update: only green, signed commits; a failed self-check rolls back and is not retried."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from growth import guard
from growth.update import update
from growth.util import read_json


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class SelfUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="growth-update-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = self.tmp / "origin.git"
        self.author = self.tmp / "author"
        self.repo = self.tmp / "engine"
        self.state = self.tmp / "state"
        git(self.tmp, "init", "--bare", "-b", "main", str(self.origin))
        git(self.tmp, "init", "-b", "main", str(self.author))
        for key, value in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
            git(self.author, "config", key, value)
        self.commit("v1")
        git(self.author, "remote", "add", "origin", str(self.origin))
        git(self.author, "push", "-q", "origin", "main")
        git(self.tmp, "clone", "-q", str(self.origin), str(self.repo))
        self.green: set[str] = set()
        self.checked: list[str] = []
        self.check_result = (True, "ok")

    def commit(self, text: str) -> str:
        (self.author / "VERSION").write_text(text)
        git(self.author, "add", "VERSION")
        git(self.author, "commit", "-q", "-m", text)
        return git(self.author, "rev-parse", "HEAD")

    def push(self) -> None:
        git(self.author, "push", "-q", "origin", "main")

    def run_update(self):
        def verdict(slug: str, sha: str):
            return (sha in self.green, "green" if sha in self.green else "checks not green")

        def check(repo: Path, home: Path):
            self.checked.append(git(repo, "rev-parse", "HEAD"))
            return self.check_result

        return update(self.tmp / "home", self.state, repo=self.repo, verdict=verdict, check=check,
                      restart=lambda *a: "restarted", slug="owner/repo")

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD")

    def test_moves_to_the_newest_green_commit_only(self) -> None:
        a = self.commit("v2")
        self.commit("v3")  # red: its checks failed
        self.push()
        self.green = {a}
        outcome = self.run_update()
        self.assertEqual((outcome.status, self.head()), ("updated", a))
        self.assertEqual(read_json(self.state / "engine" / "update.json")["history"][-1]["status"], "updated")
        self.assertEqual(guard.read_audit(self.state, 1)[0]["event"], "update.updated")

    def test_nothing_green_means_no_move(self) -> None:
        before = self.head()
        self.commit("v2")
        self.push()
        self.assertEqual(self.run_update().status, "no-green-commit")
        self.assertEqual(self.head(), before)

    def test_a_failed_self_check_rolls_back_and_is_not_retried(self) -> None:
        before = self.head()
        bad = self.commit("v2")
        self.push()
        self.green = {bad}
        self.check_result = (False, "unit tests failed")
        outcome = self.run_update()
        self.assertEqual((outcome.status, self.head()), ("rolled-back", before))
        self.assertIn(bad, read_json(self.state / "engine" / "update.json")["rejected"])
        self.check_result = (True, "ok")
        self.assertEqual(self.run_update().status, "no-green-commit")
        self.assertEqual(self.checked, [bad])
        good = self.commit("v3")
        self.push()
        self.green.add(good)
        self.assertEqual((self.run_update().status, self.head()), ("updated", good))

    def test_local_changes_are_never_overwritten(self) -> None:
        (self.repo / "VERSION").write_text("edited by hand")
        self.commit("v2")
        self.push()
        self.assertEqual(self.run_update().status, "refused")
        self.assertEqual((self.repo / "VERSION").read_text(), "edited by hand")

    def test_the_kill_switch_stops_updates(self) -> None:
        guard.set_kill(self.state, True, reason="hold", actor="cli")
        with self.assertRaises(guard.Halted):
            self.run_update()


if __name__ == "__main__":
    unittest.main()


class Verdict(unittest.TestCase):
    def fake(self, verified: bool, conclusions: list[str], status: str = "success"):
        def get_json(url: str, **_):
            if url.endswith("/check-runs?per_page=100"):
                return {"check_runs": [{"name": f"c{i}", "status": "completed", "conclusion": c} for i, c in enumerate(conclusions)]}
            if url.endswith("/status"):
                return {"state": status, "total_count": 1}
            return {"commit": {"verification": {"verified": verified, "reason": "valid" if verified else "unsigned"}}}

        return get_json

    def test_green_and_signed(self) -> None:
        from growth.update import commit_verdict

        self.assertTrue(commit_verdict("o/r", "abc", self.fake(True, ["success", "skipped"]))[0])
        self.assertFalse(commit_verdict("o/r", "abc", self.fake(False, ["success"]))[0])
        self.assertFalse(commit_verdict("o/r", "abc", self.fake(True, ["success", "failure"]))[0])
        self.assertFalse(commit_verdict("o/r", "abc", self.fake(True, []))[0])
        self.assertFalse(commit_verdict("o/r", "abc", self.fake(True, ["success"], status="pending"))[0])
