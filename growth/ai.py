"""Headless Claude Code (`claude -p`) under the owner's subscription, never a paid API key.

The child process gets an environment without any Anthropic API key, token, base URL or cloud
provider switch, so Claude Code can only use its own subscription login. `--bare` is never passed:
it would force API-key authentication. Prompts go in on stdin; no tools are enabled, so an AI job can
only write text that the engine then checks and stores.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime

from .budget import Budget
from .config import AIConfig, forbidden_args
from .store import Store
from .util import utcnow

PAID_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "AWS_BEARER_TOKEN_BEDROCK",
)
_LIMIT = re.compile(r"usage limit|rate limit|limit reached|limit will reset|out of extra usage|429", re.I)


class AIUnavailable(Exception):
    """No AI run happened: no budget, or Claude reported a usage limit."""


class AIFailed(Exception):
    """Claude ran but returned an error or unusable output."""


@dataclass(frozen=True)
class AIResult:
    text: str
    raw: dict


def subscription_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for key in PAID_ENV:
        env.pop(key, None)
    return env


def build_command(cfg: AIConfig, system: str | None) -> list[str]:
    for arg in forbidden_args([*cfg.command, *cfg.extra_args]):
        raise ValueError(f"[ai] command and extra_args may not contain {arg}: it needs a paid API key")
    cmd = [*cfg.command, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence"]
    if system:
        cmd += ["--append-system-prompt", system]
    return cmd + cfg.extra_args


class ClaudeRunner:
    def __init__(
        self, cfg: AIConfig, store: Store, budget: Budget, scope: str, job: str, workdir: str, now: datetime | None = None
    ):
        self.now = now  # the tick's clock, so budget windows are judged at the scheduled time
        self.cfg = cfg
        self.store = store
        self.budget = budget
        self.scope = scope
        self.job = job
        self.workdir = workdir

    def run(self, prompt: str, *, system: str | None = None, now: datetime | None = None) -> AIResult:
        now = now or self.now or utcnow()
        reason = self.budget.blocked_reason(now, self.scope)
        if reason:
            raise AIUnavailable(reason)
        cmd = build_command(self.cfg, system)
        row = self.store.ai_started(self.scope, self.job)
        ok, limited, detail = False, False, "ended without a result"
        try:
            try:
                proc = subprocess.run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.cfg.timeout.total_seconds(),
                    env=subscription_env(),
                    cwd=self.workdir,
                )
            except subprocess.TimeoutExpired:
                detail = "timed out"
                raise AIFailed(f"claude -p ran longer than {self.cfg.timeout}") from None
            except OSError as exc:
                detail = str(exc)
                raise AIFailed(f"could not start {self.cfg.command[0]}: {exc}") from None

            out = proc.stdout.strip()
            try:
                data = json.loads(out) if out else {}
            except json.JSONDecodeError:
                data = {"result": out, "is_error": proc.returncode != 0}
            text = str(data.get("result", "") or "")
            failed = proc.returncode != 0 or bool(data.get("is_error"))
            detail = (text or proc.stderr)[:500]
            if failed and _LIMIT.search(text + "\n" + proc.stderr):
                limited = True
                until = self.budget.start_cooldown(utcnow())
                raise AIUnavailable(f"Claude usage limit reached; no AI runs until {until}")
            if failed or not text.strip():
                raise AIFailed(f"claude -p failed (exit {proc.returncode}): {(text or proc.stderr)[:300]}")
            ok, detail = True, f"{len(text)} chars"
            return AIResult(text=text, raw=data)
        finally:
            self.store.ai_finished(row, ok=ok, limited=limited, detail=detail)
