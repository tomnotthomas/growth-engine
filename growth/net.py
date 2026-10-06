"""Outbound HTTP for jobs. Every request passes the URL policy (no Google Indexing API)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import __version__
from .guard import check_outbound
from .policy import check_url

USER_AGENT = f"growth-engine/{__version__} (+static site generator)"


class HttpError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to a URL the policy allows, the same check as the first request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        check_url(newurl)
        check_outbound(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.hostname or ''}"


def request(
    method: str,
    url: str,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    retries: int = 0,
    backoff: float = 5,
    follow_redirects: bool = True,
) -> tuple[int, bytes]:
    check_url(url)
    check_outbound(url)
    data = None
    sent = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        sent["Content-Type"] = "application/json; charset=utf-8"
    sent.update(headers or {})
    # urllib keeps the Authorization header on a redirect, so a keyed request never follows one.
    if any(k.lower() == "authorization" for k in sent):
        follow_redirects = False
    opener = urllib.request.build_opener(_CheckedRedirect if follow_redirects else _NoRedirect)
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=sent)
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400 and not follow_redirects:
                exc.close()
                raise HttpError(f"{method} {origin(url)} answered {exc.code} (redirects are not followed)") from None
            if exc.code < 500 and exc.code != 429:
                return exc.code, exc.read()
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    raise HttpError(f"{method} {origin(url)} failed: {last}")


def get_json(url: str, **kwargs: Any) -> Any:
    status, raw = request("GET", url, **kwargs)
    if status != 200:
        raise HttpError(f"GET {origin(url)} answered {status}")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpError(f"GET {origin(url)} did not return JSON: {exc}") from None
