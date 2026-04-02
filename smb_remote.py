"""SMB/CIFS file access without kernel mount (smbprotocol / smbclient)."""

from __future__ import annotations

import os
import threading

_lock = threading.Lock()


def unc_root(ip: str, share: str) -> str:
    ip = ip.strip()
    share = share.strip().lstrip("\\/")
    return rf"\\{ip}\{share}"


def unc_join(base: str, *parts: str) -> str:
    p = base.rstrip("\\")
    for part in parts:
        s = str(part).replace("/", "\\")
        for seg in s.split("\\"):
            seg = seg.strip()
            if not seg or seg in (".", ".."):
                continue
            p += "\\" + seg
    return p


def _format_username(username: str, domain: str) -> str | None:
    u = (username or "").strip()
    if not u:
        return None
    d = (domain or "").strip()
    if d and "\\" not in u and "@" not in u:
        return f"{d}\\{u}"
    return u


def register_smb(ip: str, username: str, password: str, domain: str = "") -> None:
    """Register SMB session for host. Idempotent; does not use kernel mount."""
    from smbclient import register_session

    u = _format_username(username, domain)
    pw = password if (password or "") != "" else None

    req = os.environ.get("ROUTERCUT_SMB_REQUIRE_SIGNING", "").lower() in (
        "1",
        "true",
        "yes",
    )

    with _lock:
        register_session(
            ip.strip(),
            username=u,
            password=pw,
            require_signing=req,
        )


def smb_listdir(root_unc: str) -> list[str]:
    from smbclient import listdir

    return sorted(listdir(root_unc))


def smb_read_bytes(path_unc: str) -> bytes:
    from smbclient import open_file

    with open_file(path_unc, mode="rb") as f:
        return f.read()


def smb_try_stat(path_unc: str) -> bool:
    from smbclient import stat

    try:
        stat(path_unc, follow_symlinks=False)
        return True
    except OSError:
        return False
