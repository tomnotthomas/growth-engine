"""Secrets encrypted at rest: API tokens, the stats token, the control app's login token.

The store is one file, `secrets.enc` in the engine's config folder (`~/.config/growth-engine`, or
`$GROWTH_CONFIG_DIR`). Its contents are encrypted with AES-256-CTR (the `openssl` command, since the
standard library has no AES) and authenticated with HMAC-SHA256 (encrypt-then-MAC), under keys derived
from a random 32-byte master key. The master key never sits next to the store in the clear when the
platform can hold it:

- `dpapi` (Ubuntu on WSL, the default there): the key is wrapped with Windows DPAPI for the Windows
  user, so a copy of the WSL disk alone cannot open the store;
- `file` (plain Linux, macOS, tests): the key sits in `master.key`, mode 0600, in the config folder.
  This only keeps secrets out of the repository, the private home, the environment file and backups
  of them; anyone who can read the user's files can read the key. SECURITY.md says so.

`get(name)` reads the store first and falls back to the process environment, so CI and tests can pass
values without a store. Values never appear in logs, the audit log or the control app.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
from pathlib import Path

from .util import write_atomic

NAME_OK = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


class SecretsError(Exception):
    pass


def config_dir() -> Path:
    return Path(os.environ.get("GROWTH_CONFIG_DIR", "~/.config/growth-engine")).expanduser()


POWERSHELL_FALLBACK = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


def _powershell() -> str | None:
    found = shutil.which("powershell.exe")
    return found or (POWERSHELL_FALLBACK if os.access(POWERSHELL_FALLBACK, os.X_OK) else None)


def backend() -> str:
    """The key backend: as chosen, else the one the existing key was made with, else the platform's best."""
    chosen = os.environ.get("GROWTH_KEY_BACKEND", "")
    if chosen:
        if chosen not in ("dpapi", "file"):
            raise SecretsError(f"GROWTH_KEY_BACKEND must be 'dpapi' or 'file', not {chosen!r}")
        return chosen
    if (config_dir() / "master.key.dpapi").is_file():
        return "dpapi"
    if (config_dir() / "master.key").is_file():
        return "file"
    return "dpapi" if _powershell() else "file"


def check_name(name: str) -> str:
    if not name or not set(name) <= NAME_OK or name[0].isdigit():
        raise SecretsError(f"secret names are upper-case letters, digits and underscores: {name!r}")
    return name


# ---- the master key -----------------------------------------------------------------------------

_PS_PROTECT = (
    "Add-Type -AssemblyName System.Security; $in = [Console]::In.ReadToEnd().Trim(); "
    "$b = [Convert]::FromBase64String($in); "
    "$o = [Security.Cryptography.ProtectedData]::{op}($b, $null, 'CurrentUser'); "
    "[Console]::Out.Write([Convert]::ToBase64String($o))"
)


def _dpapi(op: str, data: bytes) -> bytes:
    powershell = _powershell()
    if not powershell:
        raise SecretsError("the secrets key is wrapped with Windows DPAPI, but powershell.exe is not reachable (WSL interop off?)")
    proc = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", _PS_PROTECT.format(op=op)],
        input=base64.b64encode(data).decode(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise SecretsError(f"Windows DPAPI {op} failed: {proc.stderr.strip()[:300]}")
    return base64.b64decode(proc.stdout.strip())


def _master_key(create: bool) -> bytes:
    folder = config_dir()
    kind = backend()
    path = folder / ("master.key.dpapi" if kind == "dpapi" else "master.key")
    if path.is_file():
        if kind == "file" and path.stat().st_mode & 0o077:
            raise SecretsError(f"{path} must be readable only by its owner (chmod 600)")
        raw = base64.b64decode(path.read_text().strip())
        return _dpapi("Unprotect", raw) if kind == "dpapi" else raw
    if not create:
        raise SecretsError("no secrets store yet: add one with `growth secret set NAME`")
    key = os.urandom(32)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    stored = _dpapi("Protect", key) if kind == "dpapi" else key
    old = os.umask(0o077)
    try:
        write_atomic(path, base64.b64encode(stored).decode() + "\n")
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    return key


def _subkeys(master: bytes) -> tuple[bytes, bytes]:
    return (
        hmac.new(master, b"growth-engine secrets: encryption", hashlib.sha256).digest(),
        hmac.new(master, b"growth-engine secrets: authentication", hashlib.sha256).digest(),
    )


# ---- encryption -----------------------------------------------------------------------------------


def _aes_ctr(key: bytes, iv: bytes, data: bytes) -> bytes:
    openssl = shutil.which("openssl")
    if not openssl:
        raise SecretsError("the secrets store needs the openssl command (sudo apt install openssl)")
    proc = subprocess.run(
        [openssl, "enc", "-aes-256-ctr", "-nosalt", "-K", key.hex(), "-iv", iv.hex()], input=data, capture_output=True, timeout=30
    )
    if proc.returncode != 0:
        raise SecretsError(f"openssl failed: {proc.stderr.decode(errors='replace').strip()[:300]}")
    return proc.stdout


def seal(master: bytes, values: dict[str, str]) -> str:
    enc, mac = _subkeys(master)
    iv = os.urandom(16)
    body = _aes_ctr(enc, iv, json.dumps(values, sort_keys=True).encode("utf-8"))
    tag = hmac.new(mac, b"v1" + iv + body, hashlib.sha256).digest()
    return json.dumps({"v": 1, "iv": iv.hex(), "data": base64.b64encode(body).decode(), "tag": tag.hex()}) + "\n"


def unseal(master: bytes, text: str) -> dict[str, str]:
    enc, mac = _subkeys(master)
    try:
        box = json.loads(text)
        iv, body, tag = bytes.fromhex(box["iv"]), base64.b64decode(box["data"]), bytes.fromhex(box["tag"])
    except (ValueError, KeyError, TypeError) as exc:
        raise SecretsError(f"the secrets store is damaged: {exc}") from None
    if box.get("v") != 1 or not hmac.compare_digest(tag, hmac.new(mac, b"v1" + iv + body, hashlib.sha256).digest()):
        raise SecretsError("the secrets store failed its integrity check (changed on disk, or a different master key)")
    return json.loads(_aes_ctr(enc, iv, body).decode("utf-8"))


# ---- the store ------------------------------------------------------------------------------------


def _store_path() -> Path:
    return config_dir() / "secrets.enc"


def load() -> dict[str, str]:
    path = _store_path()
    if not path.is_file():
        return {}
    return unseal(_master_key(create=False), path.read_text())


def _save(values: dict[str, str]) -> None:
    master = _master_key(create=True)
    old = os.umask(0o077)
    try:
        write_atomic(_store_path(), seal(master, values))
    finally:
        os.umask(old)
    os.chmod(_store_path(), 0o600)
    forget_cache()


def put(name: str, value: str) -> None:
    values = load()
    values[check_name(name)] = value
    _save(values)


def delete(name: str) -> bool:
    values = load()
    if values.pop(check_name(name), None) is None:
        return False
    _save(values)
    return True


def names() -> list[str]:
    return sorted(load())


_cache: dict[str, str] | None = None


def get(name: str, default: str = "") -> str:
    """The secret from the encrypted store, else from the environment (CI, tests), else `default`."""
    global _cache
    if not name:
        return default
    if _cache is None:
        try:
            _cache = load()
        except SecretsError:
            _cache = {}
    return _cache.get(name) or os.environ.get(name, default)


def forget_cache() -> None:
    global _cache
    _cache = None
