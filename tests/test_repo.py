"""The repository is public: it may hold the engine and the fictional example, nothing private."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def tracked_and_new_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=REPO, capture_output=True, text=True, check=True
    )
    files = proc.stdout.splitlines()
    assert files, "git lists no files"
    return files


class PublicRepo(unittest.TestCase):
    def test_project_configs_live_only_in_the_example_home(self) -> None:
        for name in tracked_and_new_files():
            if name.rsplit("/", 1)[-1] in ("project.toml", "engine.toml"):
                self.assertTrue(name.startswith("examples/home/"), f"{name}: real configs belong in the private engine home")
            self.assertFalse(name.startswith(("state/", "dist/", "home/")), name)

    def test_no_env_files_or_keys(self) -> None:
        for name in tracked_and_new_files():
            base = name.rsplit("/", 1)[-1]
            self.assertFalse(base.startswith(".env") or base.endswith((".pem", ".key")) or base == ".dev.vars", name)
