"""Read-only SMB storage checks; privacy grants remain a local macOS action."""

from __future__ import annotations

import json
import plistlib
from pathlib import Path
from typing import cast

from .command import run


def has_smb_privacy_denial(output: str) -> bool:
    records: object = json.loads(output)
    if not isinstance(records, list):
        raise ValueError("expected a JSON array from the macOS log reader")
    for record in cast(list[object], records):
        if not isinstance(record, dict):
            raise ValueError("invalid macOS log record")
        message = cast(dict[str, object], record).get("eventMessage")
        if not isinstance(message, str):
            raise ValueError("macOS log record has no eventMessage")
        if (
            "/usr/sbin/smbd" in message
            and "Refusing TCCAccessRequest" in message
            and any(service in message for service in (
                "kTCCServiceSystemPolicyRemovableVolumes",
                "kTCCServiceSystemPolicyAllFiles",
            ))
        ):
            return True
    return False


def require_smb_storage_access(mount: Path) -> None:
    result = run(["diskutil", "info", "-plist", mount], capture=True, timeout=15)
    info: object = plistlib.loads(result.stdout.encode("utf-8"))
    if not isinstance(info, dict):
        raise RuntimeError("cannot verify SMB volume ownership: invalid disk information")
    settings = cast(dict[str, object], info)
    if settings.get("MountPoint") != str(mount):
        raise RuntimeError("cannot verify SMB volume ownership: unexpected mount")
    if settings.get("GlobalPermissionsEnabled") is not True:
        raise RuntimeError(
            "RAID ownership is disabled or unknown; verify the enrolled volume and run "
            "sudo diskutil enableOwnership on its mount point (see README)"
        )
    result = run([
        "/usr/bin/log", "show", "--last", "10m", "--style", "json", "--predicate",
        'process == "tccd" AND eventMessage CONTAINS[c] "/usr/sbin/smbd"',
    ], capture=True, check=False, timeout=15)
    if result.returncode != 0:
        raise RuntimeError("SMB privacy check unavailable: macOS log reader failed; verify locally (see README)")
    if has_smb_privacy_denial(result.stdout):
        raise RuntimeError(
            "SMB storage access was refused by macOS privacy protection in the last 10 minutes; "
            "enable /usr/sbin/smbd in Privacy & Security > Full Disk Access, restart SMB, "
            "and retest from a client (see README); historical denials expire after 10 minutes"
        )
