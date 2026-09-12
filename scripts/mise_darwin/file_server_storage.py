"""Read-only inventory and exact-target plan for file-server storage."""

from __future__ import annotations

import plistlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from . import file_server
from .command import atomic_write, run

DISK_ID = re.compile(r"disk[0-9]+\Z")


@dataclass(frozen=True)
class Disk:
    identifier: str
    size: int
    media_name: str
    bus: str
    partitions: tuple[str, ...]


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected a diskutil property-list dictionary")
    raw = cast(dict[object, object], value)
    return {str(key): item for key, item in raw.items()}


def _plist(*args: str) -> dict[str, object]:
    output = run(["diskutil", *args], capture=True).stdout
    return _object(plistlib.loads(output.encode("utf-8")))


def external_whole_disk_ids() -> list[str]:
    value = _plist("list", "-plist", "external").get("WholeDisks")
    if not isinstance(value, list):
        raise RuntimeError("diskutil did not return the external whole-disk list")
    disks = cast(list[object], value)
    if any(not isinstance(item, str) or not DISK_ID.fullmatch(item) for item in disks):
        raise RuntimeError("diskutil returned an invalid external disk identifier")
    return sorted(cast(list[str], disks), key=lambda item: int(item[4:]))


def inspect_disk(identifier: str, external_ids: set[str]) -> Disk:
    if not DISK_ID.fullmatch(identifier) or identifier not in external_ids:
        raise ValueError(f"not a current external whole disk: {identifier}")
    info = _plist("info", "-plist", identifier)
    if (
        info.get("DeviceIdentifier") != identifier
        or info.get("ParentWholeDisk") != identifier
        or info.get("WholeDisk") is not True
        or info.get("Internal") is not False
        or info.get("VirtualOrPhysical") != "Physical"
        or info.get("Writable") is not True
        or info.get("RAIDMaster") is not False
        or info.get("RAIDSlice") is not False
    ):
        raise RuntimeError(f"disk no longer meets the external physical target rules: {identifier}")
    size = info.get("Size")
    if not isinstance(size, int) or size <= 0:
        raise RuntimeError(f"disk has no usable size: {identifier}")
    layout = _plist("list", "-plist", identifier)
    raw_layout = layout.get("AllDisksAndPartitions")
    if not isinstance(raw_layout, list):
        raise RuntimeError(f"unexpected partition layout for {identifier}")
    roots = cast(list[object], raw_layout)
    if len(roots) != 1:
        raise RuntimeError(f"unexpected partition layout for {identifier}")
    root = _object(roots[0])
    if root.get("DeviceIdentifier") != identifier:
        raise RuntimeError(f"partition layout changed while inspecting {identifier}")
    raw_partitions = root.get("Partitions", [])
    if not isinstance(raw_partitions, list):
        raise RuntimeError(f"invalid partition list for {identifier}")
    partitions: list[str] = []
    for raw in cast(list[object], raw_partitions):
        partition = _object(raw)
        node = partition.get("DeviceIdentifier")
        kind = partition.get("Content")
        name = partition.get("VolumeName") or partition.get("Name") or ""
        amount = partition.get("Size")
        partitions.append(f"{node}: {kind} {name} ({amount} bytes)")
    return Disk(
        identifier,
        size,
        str(info.get("MediaName") or "unknown"),
        str(info.get("BusProtocol") or "unknown"),
        tuple(partitions),
    )


def inventory() -> list[Disk]:
    ids = external_whole_disk_ids()
    return [inspect_disk(identifier, set(ids)) for identifier in ids]


def print_inventory(disks: list[Disk]) -> None:
    if not disks:
        print("No external whole disks detected by macOS.")
        return
    for disk in disks:
        print(f"{disk.identifier}: {disk.media_name}; {disk.size} bytes; {disk.bus}")
        for partition in disk.partitions:
            print(f"  {partition}")


def plan(dock1: str, dock2: str, backup: str) -> None:
    selected = (dock1, dock2, backup)
    if len(set(selected)) != 3:
        raise ValueError("Dock 1, Dock 2, and backup must be three distinct disks")
    ids = set(external_whole_disk_ids())
    disks = [inspect_disk(identifier, ids) for identifier in selected]
    print("Exact live targets (all existing data on these disks would be erased):")
    for role, disk in zip(("Dock 1", "Dock 2", "Backup"), disks, strict=True):
        print(f"{role}: {disk.identifier} — {disk.media_name}, {disk.size} bytes, {disk.bus}")
        for partition in disk.partitions:
            print(f"  existing: {partition}")
    print("Review-only commands; this plan makes no changes:")
    print(f"diskutil AppleRAID create mirror FileServer APFS {dock1} {dock2}")
    print(f"diskutil eraseDisk APFS FileServerBackup GPT {backup}")


def _single_store(mount: Path) -> str:
    stores = _plist("info", "-plist", str(mount)).get("APFSPhysicalStores")
    if not isinstance(stores, list):
        raise RuntimeError(f"expected one APFS physical store under {mount}")
    physical_stores = cast(list[object], stores)
    if len(physical_stores) != 1:
        raise RuntimeError(f"expected one APFS physical store under {mount}")
    store = _object(physical_stores[0]).get("APFSPhysicalStore")
    if not isinstance(store, str):
        raise RuntimeError(f"invalid APFS physical store under {mount}")
    return store


def current_role_record(dock1: str, dock2: str, backup: str, home: Path) -> dict[str, object]:
    """Identify the three live physical disks by their enrolled volume topology."""
    selected = (dock1, dock2, backup)
    if len(set(selected)) != 3:
        raise ValueError("Dock 1, Dock 2, and backup must be three distinct disks")
    enrolled = file_server.require_volumes(home)
    file_server.require_online_mirror()
    ids = set(external_whole_disk_ids())
    disks = [inspect_disk(identifier, ids) for identifier in selected]
    if len({disk.media_name for disk in disks}) != 3:
        raise RuntimeError("physical disks do not have distinct media names; record stronger identifiers first")
    source_store = _single_store(file_server.SOURCE_MOUNT)
    backup_store = _single_store(file_server.BACKUP_MOUNT)
    sets = _plist("appleRAID", "list", "-plist").get("AppleRAIDSets")
    if not isinstance(sets, list):
        raise RuntimeError("diskutil did not return AppleRAID sets")
    raid_sets = [_object(item) for item in cast(list[object], sets)]
    matches = [item for item in raid_sets if item.get("BSD Name") == source_store]
    if len(matches) != 1:
        raise RuntimeError("source volume does not match exactly one RAID set")
    mirror = matches[0]
    if mirror.get("Name") != "FileServer" or mirror.get("Level") != "Mirror":
        raise RuntimeError("source volume is not the FileServer mirror")
    members_raw = mirror.get("Members")
    if not isinstance(members_raw, list):
        raise RuntimeError("FileServer mirror does not have two members")
    raw_members = cast(list[object], members_raw)
    if len(raw_members) != 2:
        raise RuntimeError("FileServer mirror does not have two members")
    members = [_object(item) for item in raw_members]
    roles: list[dict[str, object]] = []
    for role, disk in zip(("raid_dock_1", "raid_dock_2"), disks[:2], strict=True):
        found = [member for member in members if re.fullmatch(rf"{disk.identifier}s[0-9]+", str(member.get("BSD Name")))]
        if len(found) != 1 or found[0].get("MemberStatus") != "Online":
            raise RuntimeError(f"{disk.identifier} is not one online member of the FileServer mirror")
        member_uuid = found[0].get("AppleRAIDMemberUUID")
        if not isinstance(member_uuid, str) or not member_uuid:
            raise RuntimeError(f"{disk.identifier} has no RAID member UUID")
        roles.append({"role": role, "media_name": disk.media_name, "size_bytes": disk.size, "raid_member_uuid": member_uuid})
    backup_disk = disks[2]
    if not re.fullmatch(rf"{backup_disk.identifier}s[0-9]+", backup_store):
        raise RuntimeError(f"{backup_disk.identifier} is not the FileServerBackup physical store")
    roles.append({"role": "backup", "media_name": backup_disk.media_name, "size_bytes": backup_disk.size, "volume_uuid": enrolled.backup_uuid})
    raid_uuid = mirror.get("AppleRAIDSetUUID")
    if not isinstance(raid_uuid, str) or not raid_uuid:
        raise RuntimeError("FileServer mirror has no set UUID")
    return {"version": 1, "source_volume_uuid": enrolled.source_uuid, "raid_set_uuid": raid_uuid, "roles": roles}


def record_roles(dock1: str, dock2: str, backup: str, home: Path, *, path: Path | None = None) -> None:
    record = current_role_record(dock1, dock2, backup, home)
    path = path or Path(__file__).resolve().parents[2] / ".mise/file-server-disks.json"
    if path.parent.is_symlink() or (path.exists() and path.is_symlink()):
        raise RuntimeError(f"refusing a symlinked inventory path: {path}")
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != record:
            raise RuntimeError(f"disk roles changed; inspect the existing record before replacing {path}")
        print(f"disk roles are current: {path}")
        return
    path.parent.mkdir(mode=0o700, exist_ok=True)
    atomic_write(path, json.dumps(record, indent=2) + "\n", mode=0o600)
    print(f"recorded disk roles: {path}")
