"""Guarded file-server operations for an AppleRAID mirror and separate backup disk."""

from __future__ import annotations

import json
import os
import plistlib
import secrets
import socket
import stat
import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, cast

from .command import atomic_write, run
from .file_server_access import require_smb_storage_access

SOURCE_MOUNT = Path("/Volumes/FileServer")
BACKUP_MOUNT = Path("/Volumes/FileServerBackup")
SHARE_NAME = "FileServer"
SHARE_DIR = "Shared"
TIME_MACHINE_NAME = "TimeMachine"
TIME_MACHINE_DIR = "TimeMachine"
SMB_DAEMON = Path("/System/Library/LaunchDaemons/com.apple.smbd.plist")
REPOSITORY_DIR = "Kopia"
MAX_SNAPSHOT_AGE = timedelta(hours=36)
BACKUP_INTERVAL = timedelta(hours=24)


@dataclass(frozen=True)
class Volume:
    mount: Path
    uuid: str


@dataclass(frozen=True)
class Enrollment:
    source_uuid: str
    backup_uuid: str


def config_path(home: Path) -> Path:
    return home / ".config/mise-darwin/file-server.json"


def kopia_config_path(home: Path) -> Path:
    return home / ".config/mise-darwin/kopia.config"


def recovery_password_path(home: Path) -> Path:
    return home / ".config/mise-darwin/file-server-recovery-password.txt"


@contextmanager
def backup_lock(home: Path) -> Generator[None]:
    path = home / ".config/mise-darwin/file-server-backup.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise RuntimeError(f"expected an owner-only backup lock file: {path}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def kopia_backup_environment(home: Path) -> dict[str, str]:
    path = recovery_password_path(home)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError(f"expected an owner-only recovery password file: {path}")
    password = path.read_text(encoding="utf-8").strip()
    if not password:
        raise RuntimeError(f"empty recovery password file: {path}")
    return {**os.environ, "KOPIA_PASSWORD": password}


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected a JSON or plist object")
    raw = cast(dict[object, object], value)
    return {str(key): item for key, item in raw.items()}


def volume_info(mount: Path) -> Volume:
    result = run(["diskutil", "info", "-plist", mount], capture=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"volume is not mounted: {mount}")
    info = _object(plistlib.loads(result.stdout.encode("utf-8")))
    if info.get("MountPoint") != str(mount) or info.get("Internal") is not False:
        raise RuntimeError(f"expected an external volume mounted exactly at {mount}")
    uuid = info.get("VolumeUUID")
    if not isinstance(uuid, str) or not uuid:
        raise RuntimeError(f"volume has no UUID: {mount}")
    if info.get("WritableVolume") is not True:
        raise RuntimeError(f"volume is not writable: {mount}")
    return Volume(mount, uuid)


def read_enrollment(home: Path) -> Enrollment:
    path = config_path(home)
    try:
        data = _object(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as error:
        raise RuntimeError("file server is not enrolled; run file-server:enroll") from error
    if data.get("version") != 1:
        raise RuntimeError("unsupported file-server enrollment version")
    source, backup = data.get("source_volume_uuid"), data.get("backup_volume_uuid")
    if not isinstance(source, str) or not isinstance(backup, str) or not source or not backup:
        raise RuntimeError("invalid file-server volume UUIDs")
    if source == backup:
        raise RuntimeError("source and backup volume UUIDs are identical")
    return Enrollment(source, backup)


def require_volumes(home: Path) -> Enrollment:
    enrolled = read_enrollment(home)
    source = volume_info(SOURCE_MOUNT)
    backup = volume_info(BACKUP_MOUNT)
    if source.uuid != enrolled.source_uuid:
        raise RuntimeError(f"unexpected source volume at {SOURCE_MOUNT}")
    if backup.uuid != enrolled.backup_uuid:
        raise RuntimeError(f"unexpected backup volume at {BACKUP_MOUNT}")
    return enrolled


def require_online_mirror() -> None:
    source = _object(plistlib.loads(run(["diskutil", "info", "-plist", SOURCE_MOUNT], capture=True).stdout.encode("utf-8")))
    stores = source.get("APFSPhysicalStores")
    if not isinstance(stores, list):
        raise RuntimeError("source volume is not backed by one AppleRAID device")
    physical_stores = cast(list[object], stores)
    if len(physical_stores) != 1:
        raise RuntimeError("source volume is not backed by one AppleRAID device")
    store = _object(physical_stores[0]).get("APFSPhysicalStore")
    raid = _object(plistlib.loads(run(["diskutil", "appleRAID", "list", "-plist"], capture=True).stdout.encode("utf-8")))
    raw_sets = raid.get("AppleRAIDSets")
    if not isinstance(raw_sets, list):
        raise RuntimeError("diskutil did not return AppleRAID sets")
    matches: list[dict[str, object]] = []
    for item in cast(list[object], raw_sets):
        set_data = _object(item)
        if set_data.get("BSD Name") == store:
            matches.append(set_data)
    if len(matches) != 1:
        raise RuntimeError("source volume is not on the expected AppleRAID set")
    mirror = matches[0]
    if mirror.get("Name") != "FileServer" or mirror.get("Level") != "Mirror" or mirror.get("Status") != "Online":
        raise RuntimeError("FileServer RAID mirror is missing or degraded")
    raw_members = mirror.get("Members")
    if not isinstance(raw_members, list):
        raise RuntimeError("FileServer RAID mirror does not have two members")
    members = [_object(item) for item in cast(list[object], raw_members)]
    if len(members) != 2:
        raise RuntimeError("FileServer RAID mirror does not have two members")
    if any(member.get("MemberStatus") != "Online" for member in members):
        raise RuntimeError("FileServer RAID mirror has an offline member")


def enroll(home: Path) -> None:
    source, backup = volume_info(SOURCE_MOUNT), volume_info(BACKUP_MOUNT)
    if source.uuid == backup.uuid:
        raise RuntimeError("source and backup must be separate volumes")
    path = config_path(home)
    if path.exists():
        current = read_enrollment(home)
        if current != Enrollment(source.uuid, backup.uuid):
            raise RuntimeError(f"enrollment already exists at {path}; inspect it before replacing")
        print("file-server enrollment is current")
        return
    content = json.dumps(
        {"version": 1, "source_volume_uuid": source.uuid, "backup_volume_uuid": backup.uuid},
        indent=2,
    ) + "\n"
    atomic_write(path, content, mode=0o600)
    print(f"enrolled source {SOURCE_MOUNT} and backup {BACKUP_MOUNT}")


def repository_path() -> Path:
    return BACKUP_MOUNT / REPOSITORY_DIR


def share_path() -> Path:
    return SOURCE_MOUNT / SHARE_DIR


def time_machine_path() -> Path:
    return SOURCE_MOUNT / TIME_MACHINE_DIR


def require_local_directory(path: Path, parent: Path) -> None:
    if not path.is_dir() or path.is_symlink() or path.resolve() != path:
        raise RuntimeError(f"expected a real directory: {path}")
    if path.stat().st_dev != parent.stat().st_dev:
        raise RuntimeError(f"directory is on a different volume: {path}")


def repository_status(home: Path, *, env: dict[str, str] | None = None) -> dict[str, object]:
    result = run(
        [
            "kopia", "--config-file", kopia_config_path(home),
            *(["--no-use-keychain"] if env else []),
            "repository", "status", "--json",
        ],
        capture=True,
        check=False,
        env=env,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError("Kopia repository is not connected for this user")
    data = _object(json.loads(result.stdout))
    storage = _object(data.get("storage"))
    settings = _object(storage.get("config"))
    if storage.get("type") != "filesystem" or settings.get("path") != str(repository_path()):
        raise RuntimeError("Kopia is connected to a different repository")
    require_local_directory(repository_path(), BACKUP_MOUNT)
    return data


def _snapshot_details(home: Path, *, env: dict[str, str] | None = None) -> tuple[str, datetime, bool] | None:
    result = run(
        [
            "kopia", "--config-file", kopia_config_path(home),
            *(["--no-use-keychain"] if env else []),
            "snapshot", "list", "--json", "--max-results=1", share_path(),
        ],
        capture=True,
        env=env,
        timeout=60,
    )
    decoded: object = json.loads(result.stdout)
    if not isinstance(decoded, list):
        raise RuntimeError("Kopia returned invalid snapshot data")
    if not decoded:
        return None
    snapshots = cast(list[object], decoded)
    snapshot = _object(snapshots[0])
    source = _object(snapshot.get("source"))
    if source.get("path") != str(share_path()):
        raise RuntimeError("latest Kopia snapshot has the wrong source")
    stats = _object(snapshot.get("stats"))
    clean = stats.get("errorCount") == 0 and stats.get("ignoredErrorCount") == 0
    timestamp = snapshot.get("endTime")
    if not isinstance(timestamp, str):
        raise RuntimeError("latest Kopia snapshot has no completion time")
    completed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if completed.tzinfo is None:
        raise RuntimeError("latest Kopia snapshot has no timezone")
    snapshot_id = snapshot.get("id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise RuntimeError("latest Kopia snapshot has no ID")
    return snapshot_id, completed, clean


def latest_snapshot(home: Path, *, now: datetime | None = None) -> str:
    details = _snapshot_details(home)
    if details is None:
        raise RuntimeError("no Kopia snapshot exists for the file-server share")
    snapshot_id, completed, clean = details
    if not clean:
        raise RuntimeError("latest Kopia snapshot contains errors")
    current = now or datetime.now(timezone.utc)
    if completed > current or current - completed > MAX_SNAPSHOT_AGE:
        raise RuntimeError("latest Kopia snapshot is stale")
    return snapshot_id


def snapshot_due(home: Path, *, env: dict[str, str], now: datetime | None = None) -> bool:
    details = _snapshot_details(home, env=env)
    if details is None:
        return True
    _, completed, clean = details
    current = now or datetime.now(timezone.utc)
    if completed > current:
        raise RuntimeError("latest Kopia snapshot is in the future")
    return not clean or current - completed >= BACKUP_INTERVAL


def init_repository(home: Path) -> None:
    require_volumes(home)
    path = repository_path()
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"repository path already exists: {path}; connect to it instead")
    recovery_path = recovery_password_path(home)
    if recovery_path.exists() or recovery_path.is_symlink():
        raise RuntimeError(f"recovery password file already exists: {recovery_path}; inspect it before replacing")
    password = secrets.token_urlsafe(48)
    atomic_write(recovery_path, password + "\n", mode=0o600)
    run(
        [
            "kopia", "--config-file", kopia_config_path(home),
            "repository", "create", "filesystem", "--path", path,
            "--use-keychain", "--persist-credentials",
        ],
        env={**os.environ, "KOPIA_PASSWORD": password},
    )
    repository_status(home)
    print(f"copy the recovery password from {recovery_path} to a password manager")


def backup(home: Path) -> None:
    with backup_lock(home):
        _backup_locked(home)


def _backup_locked(home: Path, *, env: dict[str, str] | None = None) -> None:
    require_volumes(home)
    source = share_path()
    require_local_directory(source, SOURCE_MOUNT)
    environment = env or kopia_backup_environment(home)
    repository_status(home, env=environment)
    run(
        ["kopia", "--config-file", kopia_config_path(home), "--no-use-keychain", "snapshot", "create", source],
        env=environment,
    )


def shares() -> dict[str, object]:
    result = run(["sharing", "-l", "-f", "json"], capture=True)
    return _object(json.loads(result.stdout))


def managed_share(entries: dict[str, object], name: str, path: Path) -> tuple[str, dict[str, object]] | None:
    matches: list[tuple[str, dict[str, object]]] = []
    for record_name, value in entries.items():
        entry = _object(value)
        if entry.get("smb_name") == name or entry.get("path") == str(path):
            matches.append((record_name, entry))
    if len(matches) > 1:
        raise RuntimeError(f"conflicting {name} SMB share records")
    return matches[0] if matches else None


def require_private_share(entries: dict[str, object], name: str, path: Path) -> None:
    require_local_directory(path, SOURCE_MOUNT)
    match = managed_share(entries, name, path)
    settings = match[1] if match else {}
    if settings.get("path") != str(path) or settings.get("smb_name") != name or settings.get("smb_shared") != 1 or settings.get("smb_guest_access") != 0:
        raise RuntimeError(f"private SMB share {name} is missing or drifted")


def require_time_machine_destination(entries: dict[str, object]) -> None:
    """Check the share option that `sharing -l` does not report."""
    match = managed_share(entries, TIME_MACHINE_NAME, time_machine_path())
    if match is None:
        raise RuntimeError("Time Machine SMB share is missing")
    record_name, _ = match
    result = run(["dscl", ".", "-read", f"/SharePoints/{record_name}"], capture=True)
    if "dsAttrTypeNative:timeMachineBackup: 1" not in result.stdout.splitlines():
        raise RuntimeError("TimeMachine is not enabled as a Time Machine backup destination")


def reconcile(home: Path) -> None:
    """Check persistent SMB state and catch up a backup after disks return."""
    enrolled = read_enrollment(home)
    if volume_info(SOURCE_MOUNT).uuid != enrolled.source_uuid:
        raise RuntimeError(f"unexpected source volume at {SOURCE_MOUNT}")
    errors: list[str] = []
    try:
        with backup_lock(home):
            require_volumes(home)
            env = kopia_backup_environment(home)
            repository_status(home, env=env)
            if snapshot_due(home, env=env):
                print("file-server backup is due; creating a Kopia snapshot", flush=True)
                _backup_locked(home, env=env)
            else:
                print("file-server backup is current", flush=True)
    except (OSError, RuntimeError, ValueError) as error:
        errors.append(f"backup: {error}")
    try:
        entries = shares()
        require_private_share(entries, SHARE_NAME, share_path())
        require_private_share(entries, TIME_MACHINE_NAME, time_machine_path())
        require_time_machine_destination(entries)
        if not smb_service_listening():
            raise RuntimeError("macOS SMB service is not listening; run file-server:enable-smb with administrator access")
        require_smb_storage_access(SOURCE_MOUNT)
        print("file-server SMB configuration checked; no recent storage privacy refusals", flush=True)
    except (OSError, RuntimeError, ValueError) as error:
        errors.append(f"SMB: {error}")
    try:
        require_online_mirror()
    except (OSError, RuntimeError, ValueError) as error:
        errors.append(f"RAID: {error}")
    if errors:
        raise RuntimeError("; ".join(errors))


def ac_power_settings(output: str) -> dict[str, int]:
    """Extract AC settings without confusing them with battery settings."""
    settings: dict[str, int] = {}
    in_ac_section = False
    for line in output.splitlines():
        if line and not line[0].isspace() and line.endswith("Power:"):
            in_ac_section = line == "AC Power:"
            continue
        if not in_ac_section:
            continue
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            settings[parts[0]] = int(parts[1])
    return settings


def power() -> None:
    current = ac_power_settings(run(["pmset", "-g", "custom"], capture=True).stdout)
    desired = {"sleep": 0, "autorestart": 1, "womp": 1}
    changes = [item for key, value in desired.items() if current.get(key) != value for item in (key, str(value))]
    if changes:
        run(["sudo", "pmset", "-c", *changes])
    print("AC power settings: sleep 0, autorestart 1, wake-on-network 1")


def smb_service_listening() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 445), timeout=2):
            return True
    except OSError:
        return False


def enable_smb() -> None:
    if smb_service_listening():
        print("macOS SMB service is already listening")
        return
    run(["sudo", "launchctl", "enable", "system/com.apple.smbd"])
    current = run(["launchctl", "print", "system/com.apple.smbd"], capture=True, check=False)
    if current.returncode != 0:
        run(["sudo", "launchctl", "bootstrap", "system", SMB_DAEMON])
    if not smb_service_listening():
        raise RuntimeError("macOS SMB service did not start on TCP port 445")
    print("macOS SMB service is listening on TCP port 445")


def converge_private_share(source: Path, name: str) -> None:
    if source.is_symlink() or (source.exists() and not source.is_dir()):
        raise RuntimeError(f"cannot use conflicting share path: {source}")
    source.mkdir(exist_ok=True)
    require_local_directory(source, SOURCE_MOUNT)
    existing = managed_share(shares(), name, source)
    if existing is None:
        run(["sudo", "sharing", "-a", source, "-n", name, "-S", name, "-s", "001", "-g", "000"])
    else:
        record_name, entry = existing
        if entry.get("path") != str(source):
            raise RuntimeError(f"share name {name} belongs to another directory")
        if entry.get("smb_name") != name or entry.get("smb_shared") != 1 or entry.get("smb_guest_access") != 0:
            run(["sudo", "sharing", "-e", record_name, "-S", name, "-s", "001", "-g", "000"])
    print(f"private SMB share: {name} -> {source}")


def share(home: Path) -> None:
    require_volumes(home)
    converge_private_share(share_path(), SHARE_NAME)


def time_machine_share(home: Path) -> None:
    require_volumes(home)
    require_online_mirror()
    converge_private_share(time_machine_path(), TIME_MACHINE_NAME)
    print("enable Time Machine destination in this share's Advanced Options")


def status(home: Path) -> bool:
    try:
        require_volumes(home)
        print("ok  enrolled source and backup volumes")
        require_online_mirror()
        print("ok  two online RAID mirror members")
        repository_status(home)
        print("ok  Kopia repository on backup volume")
        latest_snapshot(home)
        print("ok  recent error-free Kopia snapshot")
        require_local_directory(share_path(), SOURCE_MOUNT)
        entries = shares()
        match = managed_share(entries, SHARE_NAME, share_path())
        share_settings = match[1] if match else {}
        if share_settings.get("path") != str(share_path()) or share_settings.get("smb_name") != SHARE_NAME or share_settings.get("smb_shared") != 1 or share_settings.get("smb_guest_access") != 0:
            raise RuntimeError("private SMB share is missing or drifted")
        print("ok  private SMB share")
        require_private_share(entries, TIME_MACHINE_NAME, time_machine_path())
        require_time_machine_destination(entries)
        print("ok  Time Machine backup destination")
        if not smb_service_listening():
            raise RuntimeError("macOS File Sharing is not listening on TCP port 445")
        print("ok  SMB service listening")
        require_smb_storage_access(SOURCE_MOUNT)
        print("ok  RAID ownership enabled; no recent SMB storage privacy refusals")
        print("note  client authentication and file access still require an actual SMB test")
        return True
    except (RuntimeError, ValueError, json.JSONDecodeError, plistlib.InvalidFileException) as error:
        print(f"ERR file server: {error}")
        return False
