"""Dynamic SMB (CIFS) mount into ephemeral directories — no /mnt/<ip> paths."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path

_locks: dict[int, threading.Lock] = {}
_refcount: dict[int, int] = {}
_mounted: set[int] = set()

MNT_BASE = Path(os.environ.get("ROUTERCUT_MNT_BASE", tempfile.gettempdir())) / "routercut-wrk"


def _sudo_mount() -> bool:
    return os.environ.get("ROUTERCUT_MOUNT_USE_SUDO", "").lower() in (
        "1",
        "true",
        "yes",
    )


def _priv_cmd(argv: list[str]) -> list[str]:
    if _sudo_mount():
        return ["sudo", "-n", *argv]
    return argv


def mount_point(host_id: int) -> Path:
    return MNT_BASE / f"h{host_id}"


def _lock(host_id: int) -> threading.Lock:
    if host_id not in _locks:
        _locks[host_id] = threading.Lock()
    return _locks[host_id]


def _smb_share(host_row) -> str:
    v = host_row["smb_share"]
    return (v or "").strip() if v is not None else ""


def ensure_mounted(host_row) -> tuple[bool, str]:
    """Increment refcount and mount //ip/share if needed. No-op for non-SMB hosts."""
    share = _smb_share(host_row)
    if not share:
        return True, ""

    host_id = int(host_row["id"])
    mp = mount_point(host_id)

    with _lock(host_id):
        _refcount[host_id] = _refcount.get(host_id, 0) + 1
        if host_id in _mounted:
            return True, ""

        mp.mkdir(parents=True, exist_ok=True)
        ip = str(host_row["ip"]).strip()
        unc = f"//{ip}/{share.lstrip('/')}"

        uid = os.getuid()
        gid = os.getgid()
        domain = os.environ.get("ROUTERCUT_SMB_DOMAIN", "").strip()
        user = os.environ.get("ROUTERCUT_SMB_USER", "").strip()
        password = os.environ.get("ROUTERCUT_SMB_PASSWORD", "")

        opt_parts: list[str] = [f"uid={uid}", f"gid={gid}", "iocharset=utf8", "vers=3.0"]
        if domain:
            opt_parts.append(f"domain={domain}")

        env = os.environ.copy()
        fd: int | None = None
        cred_path: str | None = None
        try:
            if user or password:
                cred_path = os.path.join(
                    tempfile.gettempdir(), f".routercut-smb-{host_id}-{os.getpid()}.cred"
                )
                fd = os.open(cred_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as cf:
                    fd = None
                    cf.write(f"username={user or 'guest'}\n")
                    cf.write(f"password={password}\n")
                opt_parts.append(f"credentials={cred_path}")
            else:
                opt_parts.append("guest")

            opts = ",".join(opt_parts)
            cmd = _priv_cmd(["mount", "-t", "cifs", unc, str(mp), "-o", opts])
            r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
            if r.returncode != 0:
                _refcount[host_id] = max(0, _refcount[host_id] - 1)
                msg = (r.stderr or r.stdout or "").strip() or "mount failed"
                low = msg.lower()
                if _sudo_mount() and (
                    "password" in low or "a terminal is required" in low or "interactive" in low
                ):
                    msg += (
                        " — sudo 비밀번호 없이 mount 허용 필요: "
                        "NOPASSWD 로 /bin/mount, /bin/umount (배포판에 따라 /usr/bin/… 경로)"
                    )
                return False, msg
            _mounted.add(host_id)
            return True, ""
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if cred_path and os.path.isfile(cred_path):
                try:
                    os.unlink(cred_path)
                except OSError:
                    pass


def release_mounted(host_id: int) -> None:
    """Decrement refcount; umount when it reaches zero."""
    with _lock(host_id):
        if host_id not in _mounted:
            return
        c = max(0, _refcount.get(host_id, 0) - 1)
        _refcount[host_id] = c
        if c == 0:
            mp = mount_point(host_id)
            subprocess.run(
                _priv_cmd(["umount", str(mp)]),
                capture_output=True,
                text=True,
                timeout=60,
            )
            _mounted.discard(host_id)


def force_umount_host(host_id: int) -> None:
    """Used when deleting a host: drop mount regardless of refcount."""
    with _lock(host_id):
        _refcount[host_id] = 0
        if host_id in _mounted:
            mp = mount_point(host_id)
            subprocess.run(
                _priv_cmd(["umount", str(mp)]),
                capture_output=True,
                text=True,
                timeout=60,
            )
            _mounted.discard(host_id)


def umount_all() -> None:
    for hid in list(_mounted):
        force_umount_host(hid)


