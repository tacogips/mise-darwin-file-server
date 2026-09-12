"""Exact-target checks for the read-only storage plan."""

from __future__ import annotations

import io
import plistlib
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from scripts.mise_darwin import file_server_storage as storage


def result(value: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, plistlib.dumps(cast(Any, value)).decode("utf-8"), "")


class StoragePlanTests(unittest.TestCase):
    def test_inspect_disk_rejects_internal_or_partition_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "external whole disk"):
            storage.inspect_disk("disk0s2", {"disk0"})
        with patch.object(storage, "run", return_value=result({
            "DeviceIdentifier": "disk0", "ParentWholeDisk": "disk0",
            "WholeDisk": True, "Internal": True, "VirtualOrPhysical": "Physical", "Writable": True,
            "RAIDMaster": False, "RAIDSlice": False, "Size": 1000,
        })):
            with self.assertRaisesRegex(RuntimeError, "target rules"):
                storage.inspect_disk("disk0", {"disk0"})

    def test_inspect_disk_includes_existing_partitions(self) -> None:
        info = {
            "DeviceIdentifier": "disk7", "ParentWholeDisk": "disk7",
            "WholeDisk": True, "Internal": False, "VirtualOrPhysical": "Physical", "Writable": True,
            "RAIDMaster": False, "RAIDSlice": False, "Size": 2000,
            "MediaName": "Test HDD", "BusProtocol": "USB",
        }
        layout = {"AllDisksAndPartitions": [{
            "DeviceIdentifier": "disk7",
            "Partitions": [{"DeviceIdentifier": "disk7s1", "Content": "Apple_APFS", "VolumeName": "OldData", "Size": 1800}],
        }]}
        with patch.object(storage, "run", side_effect=[result(info), result(layout)]):
            disk = storage.inspect_disk("disk7", {"disk7"})
        self.assertIn("OldData", disk.partitions[0])
        self.assertEqual(disk.size, 2000)

    def test_plan_rejects_duplicate_roles_without_inspecting_disks(self) -> None:
        with patch.object(storage, "run") as command:
            with self.assertRaisesRegex(ValueError, "three distinct"):
                storage.plan("disk15", "disk15", "disk16")
            command.assert_not_called()

    def test_plan_only_prints_commands_for_three_validated_targets(self) -> None:
        disks = [storage.Disk(f"disk{index}", 1000, "HDD", "USB", ()) for index in (15, 16, 17)]
        output = io.StringIO()
        with patch.object(storage, "external_whole_disk_ids", return_value=["disk15", "disk16", "disk17"]), patch.object(
            storage, "inspect_disk", side_effect=disks,
        ) as inspected, redirect_stdout(output):
            storage.plan("disk15", "disk16", "disk17")
        self.assertEqual(inspected.call_count, 3)
        self.assertIn("diskutil AppleRAID create mirror FileServer APFS disk15 disk16", output.getvalue())
        self.assertIn("diskutil eraseDisk APFS FileServerBackup GPT disk17", output.getvalue())

    def test_role_record_matches_live_raid_and_backup_topology(self) -> None:
        disks = [storage.Disk(f"disk{node}", 8000, f"USB DISK{label}", "USB", ()) for node, label in ((15, "A"), (16, "B"), (14, "C"))]
        raid = {"AppleRAIDSets": [{
            "BSD Name": "disk17", "Name": "FileServer", "Level": "Mirror", "AppleRAIDSetUUID": "raid-id",
            "Members": [
                {"BSD Name": "disk15s2", "MemberStatus": "Online", "AppleRAIDMemberUUID": "member-A"},
                {"BSD Name": "disk16s2", "MemberStatus": "Online", "AppleRAIDMemberUUID": "member-B"},
            ],
        }]}
        with patch.object(storage.file_server, "require_volumes", return_value=storage.file_server.Enrollment("source-id", "backup-id")), patch.object(
            storage.file_server, "require_online_mirror",
        ), patch.object(storage, "external_whole_disk_ids", return_value=["disk14", "disk15", "disk16"]), patch.object(
            storage, "inspect_disk", side_effect=disks,
        ), patch.object(storage, "_single_store", side_effect=["disk17", "disk14s2"]), patch.object(
            storage, "_plist", return_value=raid,
        ):
            record = storage.current_role_record("disk15", "disk16", "disk14", Path.home())
        self.assertEqual(record["raid_set_uuid"], "raid-id")
        roles = cast(list[dict[str, object]], record["roles"])
        self.assertEqual([role["role"] for role in roles], ["raid_dock_1", "raid_dock_2", "backup"])
        self.assertEqual(roles[0]["raid_member_uuid"], "member-A")
        self.assertEqual(roles[2]["volume_uuid"], "backup-id")

    def test_record_roles_preserves_existing_record_and_refuses_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".mise/file-server-disks.json"
            with patch.object(storage, "current_role_record", return_value={"version": 1, "roles": []}):
                storage.record_roles("disk15", "disk16", "disk14", Path.home(), path=path)
                original = path.read_bytes()
                storage.record_roles("disk15", "disk16", "disk14", Path.home(), path=path)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with patch.object(storage, "current_role_record", return_value={"version": 1, "roles": ["changed"]}):
                with self.assertRaisesRegex(RuntimeError, "disk roles changed"):
                    storage.record_roles("disk15", "disk16", "disk14", Path.home(), path=path)


if __name__ == "__main__":
    unittest.main()
