"""The control API the Mac app talks to: HTTP on 127.0.0.1 only, behind a login token.

It is never exposed to the network. On the GEEKOM it listens on 127.0.0.1:8765; the Mac app reaches
it through an SSH tunnel (`ssh -L ... geekom-wsl`), so the SSH key is the first lock and the token
the second. Requests must carry `Authorization: Bearer <token>` and a loopback Host header (against
DNS rebinding), and any request with an Origin header is refused (browsers send one; the app does
not), so a web page open on either machine cannot drive the engine.

    GET  /api/ping                       {"ok": true}; no token needed, used to tell "offline" from "locked"
    GET  /api/dashboard                  the snapshot (goal, jobs, queue, health)
    GET  /api/audit?limit=&scope=        the newest audit entries and whether the chain is intact
    POST /api/kill                       {"on": true|false, "reason": "..."}
    POST /api/run                        {"scope": "<project>|engine", "job": "<job id>"}
    POST /api/jobs                       {"scope", "job", "enabled"}
    POST /api/projects/<id>/pause        {"paused": true|false}
    POST /api/projects/<id>/channel      {"channel", "paused"?, "rate_limit"?}
    POST /api/projects/<id>/goal         {"target"?, "days"?, "start"?, "zone"?}
    POST /api/projects/<id>/settings     {"brand_name"?, "base_url"?, "domain_decided"?, "indexable"?, "launched"?, "keywords"?}
    POST /api/projects/<id>/draft        {"id", "status": "approved"|"rejected"|"posted"|"open"}
    POST /api/budget                     {"max_runs_per_day"?, "max_runs_per_week"?, "per_project_runs_per_week"?, "rate_limits"?}

Every POST is recorded in the audit log with the actor "captain" (the only login there is).
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import secrets as pysecrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .. import guard, secrets
from ..config import ConfigError
from ..policy import PolicyViolation
from . import ops
from .snapshot import refresh_stale_stats, snapshot

log = logging.getLogger("growth")
TOKEN_NAME = "APP_TOKEN"
DEMO_TOKEN = "demo"
ACTOR = "captain"
MAX_BODY = 64 * 1024
_PROJECT = re.compile(r"^/api/projects/([a-z0-9][a-z0-9-]*)/(pause|channel|goal|settings|draft)$")


def app_token(create: bool = True) -> str:
    """The control app's login token, kept in the encrypted secrets store."""
    token = secrets.load().get(TOKEN_NAME, "")
    if not token and create:
        token = pysecrets.token_urlsafe(32)
        secrets.put(TOKEN_NAME, token)
    return token


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, home: ops.Home, token: str, *, demo: bool = False):
        super().__init__(("127.0.0.1", port), Handler)
        self.home, self.token, self.demo = home, token, demo


class Handler(BaseHTTPRequestHandler):
    server: ControlServer
    server_version = "growth-control"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:  # no tokens or bodies in logs
        log.debug("control %s %s", self.command, self.path.split("?")[0])

    # ---- plumbing ---------------------------------------------------------------------------------

    def _send(self, status: int, body: Any) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _allowed(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ("127.0.0.1", "localhost", "::1"):
            self._send(421, {"error": "wrong host"})
            return False
        if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site"):
            self._send(403, {"error": "browsers may not use the control API"})
            return False
        return True

    def _authorized(self) -> bool:
        given = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        if self.server.token and hmac.compare_digest(given.encode(), expected.encode()):
            return True
        self._send(401, {"error": "not logged in"})
        return False

    def _body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._send(413, {"error": "request too large"})
            return None
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send(400, {"error": "body must be JSON"})
            return None
        if not isinstance(data, dict):
            self._send(400, {"error": "body must be a JSON object"})
            return None
        return data

    # ---- routes -----------------------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._allowed():
            return
        url = urlsplit(self.path)
        if url.path == "/api/ping":
            return self._send(200, {"ok": True, "demo": self.server.demo})
        if not self._authorized():
            return
        try:
            if url.path == "/api/dashboard":
                engine = self.server.home.engine()
                refresh_stale_stats(engine, guard_now())
                return self._send(200, snapshot(engine, demo=self.server.demo))
            if url.path == "/api/audit":
                query = parse_qs(url.query)
                limit = max(1, min(1000, int((query.get("limit") or ["200"])[0])))
                scope = (query.get("scope") or [None])[0]
                state = self.server.home.state()
                intact, entries, problem = guard.verify_audit(state)
                return self._send(200, {"intact": intact, "entries": entries, "problem": problem, "items": guard.read_audit(state, limit, scope=scope)})
        except ConfigError as exc:
            return self._send(500, {"error": "the engine configuration does not load", "details": exc.errors})
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._allowed() or not self._authorized():
            return
        body = self._body()
        if body is None:
            return
        home, path = self.server.home, urlsplit(self.path).path
        try:
            result: Any = {"ok": True}
            if path == "/api/kill":
                result = {"kill": ops.set_kill(home, ACTOR, body.get("on"), str(body.get("reason", "")))}
            elif path == "/api/run":
                result = {"pid": ops.run_now(home, ACTOR, str(body.get("scope", "")), str(body.get("job", "")))}
            elif path == "/api/jobs":
                ops.set_job(home, ACTOR, str(body.get("scope", "")), str(body.get("job", "")), body.get("enabled"))
            elif path == "/api/budget":
                ops.set_budget(home, ACTOR, body)
            elif match := _PROJECT.match(path):
                pid, what = match.groups()
                if what == "pause":
                    ops.set_paused(home, ACTOR, pid, body.get("paused"))
                elif what == "channel":
                    ops.set_channel(home, ACTOR, pid, str(body.get("channel", "")), paused=body.get("paused"), rate_limit=body.get("rate_limit"))
                elif what == "goal":
                    ops.set_goal(home, ACTOR, pid, body)
                elif what == "settings":
                    ops.set_settings(home, ACTOR, pid, body)
                else:
                    result = {"draft": ops.decide_draft(home, ACTOR, pid, str(body.get("id", "")), str(body.get("status", "")))}
            else:
                return self._send(404, {"error": "not found"})
        except (ops.ControlError, guard.Halted, PolicyViolation) as exc:
            return self._send(409, {"error": str(exc)})
        except ConfigError as exc:
            return self._send(500, {"error": "the engine configuration does not load", "details": exc.errors})
        self._send(200, result)


def guard_now():  # a seam for tests
    from ..util import utcnow

    return utcnow()


def serve(home: ops.Home, port: int, *, demo: bool = False) -> None:
    token = DEMO_TOKEN if demo else app_token()
    server = ControlServer(port, home, token, demo=demo)
    log.info("control API on http://127.0.0.1:%d (%s)", server.server_port, "demo data" if demo else str(home.root))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
