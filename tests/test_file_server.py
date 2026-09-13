"""Safety checks around enrolling volumes and running file-server backups."""

from __future__ import annotations

import json
import plistlib
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.mise_darwin import file_server


class FileServerTests(unittest.TestCase):
    def test_ac_power_settings_ignores_battery_section(self) -> None:
        output = "Battery Power:\n sleep 5\n autorestart 0\nAC Power:\n sleep 0\n autorestart 1\n womp 1\n"
        self.assertEqual(file_server.ac_power_settings(output), {"sleep": 0, "autorestart": 1, "womp": 1})

    def test_power_only_changes_drifted_settings(self) -> None:
        output = "AC Power:\n sleep 1\n autorestart 0\n womp 1\n"
        with patch.object(file_server, "run", side_effect=[
            subprocess.CompletedProcess([], 0, output, ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]) as command:
            file_server.power()
            self.assertEqual(command.call_args_list[1].args[0], [
                "sudo", "pmset", "-c", "sleep", "0", "autorestart", "1",
            ])

    def test_enable_smb_skips_work_when_listening(self) -> None:
        with patch.object(file_server, "smb_service_listening", return_value=True), patch.object(
            file_server, "run",
        ) as command:
            file_server.enable_smb()
            command.assert_not_called()

    def test_enable_smb_bootstraps_only_when_service_is_unloaded(self) -> None:
        with patch.object(file_server, "smb_service_listening", side_effect=[False, True]), patch.object(
            file_server, "run", side_effect=[
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 1, "", ""),
                subprocess.CompletedProcess([], 0, "", ""),
            ],
        ) as command:
            file_server.enable_smb()
            self.assertEqual(command.call_args_list[0].args[0], [
                "sudo", "launchctl", "enable", "system/com.apple.smbd",
            ])
            self.assertEqual(command.call_args_list[2].args[0], [
                "sudo", "launchctl", "bootstrap", "system", file_server.SMB_DAEMON,
            ])

    def test_volume_info_requires_exact_external_writable_mount(self) -> None:
        mount = Path("/Volumes/FileServer")
        record = {
            "MountPoint": str(mount), "Internal": False,
            "VolumeUUID": "source-uuid", "WritableVolume": True,
        }
        output = plistlib.dumps(record).decode("utf-8")
        with patch.object(file_server, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
            self.assertEqual(file_server.volume_info(mount), file_server.Volume(mount, "source-uuid"))

        for change in ({"MountPoint": "/Volumes/Other"}, {"Internal": True}, {"WritableVolume": False}):
            changed = {**record, **change}
            output = plistlib.dumps(changed).decode("utf-8")
            with patch.object(file_server, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                with self.assertRaises(RuntimeError):
                    file_server.volume_info(mount)

    def test_raid_status_requires_two_online_members_under_source(self) -> None:
        source = plistlib.dumps({"APFSPhysicalStores": [{"APFSPhysicalStore": "disk7"}]}).decode("utf-8")
        mirror: dict[str, object] = {
            "BSD Name": "disk7", "Name": "FileServer", "Level": "Mirror", "Status": "Online",
            "Members": [{"MemberStatus": "Online"}, {"MemberStatus": "Online"}],
        }

        def check(set_data: dict[str, object]) -> None:
            raid = plistlib.dumps({"AppleRAIDSets": [set_data]}).decode("utf-8")
            with patch.object(file_server, "run", side_effect=[
                subprocess.CompletedProcess([], 0, source, ""),
                subprocess.CompletedProcess([], 0, raid, ""),
            ]):
                file_server.require_online_mirror()

        check(mirror)
        for changed in (
            {**mirror, "Status": "Degraded"},
            {**mirror, "Members": [{"MemberStatus": "Online"}, {"MemberStatus": "Offline"}]},
            {**mirror, "BSD Name": "disk99"},
        ):
            with self.assertRaises(RuntimeError):
                check(changed)

    def test_enroll_refuses_to_replace_other_volume_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = file_server.config_path(home)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "version": 1, "source_volume_uuid": "old-source", "backup_volume_uuid": "old-backup",
            }), encoding="utf-8")
            original = path.read_bytes()
            with patch.object(file_server, "volume_info", side_effect=[
                file_server.Volume(file_server.SOURCE_MOUNT, "new-source"),
                file_server.Volume(file_server.BACKUP_MOUNT, "new-backup"),
            ]):
                with self.assertRaisesRegex(RuntimeError, "already exists"):
                    file_server.enroll(home)
            self.assertEqual(path.read_bytes(), original)

    def test_backup_refuses_missing_or_swapped_mount_before_kopia(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = file_server.config_path(home)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "version": 1, "source_volume_uuid": "source", "backup_volume_uuid": "backup",
            }), encoding="utf-8")
            with patch.object(file_server, "volume_info", side_effect=[
                file_server.Volume(file_server.SOURCE_MOUNT, "backup"),
                file_server.Volume(file_server.BACKUP_MOUNT, "source"),
            ]), patch.object(file_server, "run") as command:
                with self.assertRaisesRegex(RuntimeError, "unexpected source"):
                    file_server.backup(home)
                command.assert_not_called()

    def test_repository_status_rejects_wrong_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = json.dumps({"storage": {"type": "filesystem", "config": {"path": "/tmp/other"}}})
            with patch.object(file_server, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                with self.assertRaisesRegex(RuntimeError, "different repository"):
                    file_server.repository_status(Path(directory))

    def test_share_lookup_uses_smb_name_not_record_name(self) -> None:
        entry = {
            "path": str(file_server.share_path()), "smb_name": "FileServer",
            "smb_shared": 1, "smb_guest_access": 0,
        }
        self.assertEqual(
            file_server.managed_share({"Shared": entry}, "FileServer", file_server.share_path()),
            ("Shared", entry),
        )
        with self.assertRaisesRegex(RuntimeError, "conflicting"):
            file_server.managed_share(
                {"Shared": entry, "Other": {**entry, "path": "/tmp/other"}},
                "FileServer", file_server.share_path(),
            )

    def test_time_machine_share_is_idempotent_for_existing_private_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "TimeMachine"
            target.mkdir()
            entry = {
                "path": str(target), "smb_name": "TimeMachine",
                "smb_shared": 1, "smb_guest_access": 0,
            }
            with patch.object(file_server, "SOURCE_MOUNT", root), patch.object(
                file_server, "shares", return_value={"TimeMachine": entry},
            ), patch.object(file_server, "run") as command:
                file_server.converge_private_share(target, "TimeMachine")
                command.assert_not_called()

    def test_time_machine_destination_detection(self) -> None:
        entry = {"path": str(file_server.time_machine_path()), "smb_name": "TimeMachine"}
        records: dict[str, object] = {"TimeMachine": entry}
        enabled = subprocess.CompletedProcess([], 0, "dsAttrTypeNative:timeMachineBackup: 1\n", "")
        disabled = subprocess.CompletedProcess([], 0, "dsAttrTypeNative:timeMachineBackup: 0\n", "")
        with patch.object(file_server, "run", return_value=enabled) as command:
            file_server.require_time_machine_destination(records)
            command.assert_called_once_with(
                ["dscl", ".", "-read", "/SharePoints/TimeMachine"], capture=True,
            )
        with patch.object(file_server, "run", return_value=disabled):
            with self.assertRaisesRegex(RuntimeError, "not enabled"):
                file_server.require_time_machine_destination(records)

    def test_init_repository_preserves_recovery_password_and_uses_keychain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            repository = home / "Kopia"
            with patch.object(file_server, "require_volumes"), patch.object(
                file_server, "repository_path", return_value=repository,
            ), patch.object(file_server, "repository_status"), patch.object(file_server, "run") as command:
                file_server.init_repository(home)
                recovery = file_server.recovery_password_path(home)
                password = recovery.read_text(encoding="utf-8").strip()
                self.assertGreaterEqual(len(password), 48)
                self.assertEqual(recovery.stat().st_mode & 0o777, 0o600)
                arguments = command.call_args.args[0]
                self.assertIn("--use-keychain", arguments)
                self.assertIn("--persist-credentials", arguments)
                self.assertEqual(command.call_args.kwargs["env"]["KOPIA_PASSWORD"], password)
                with self.assertRaisesRegex(RuntimeError, "recovery password file already exists"):
                    file_server.init_repository(home)
                self.assertEqual(recovery.read_text(encoding="utf-8").strip(), password)
                self.assertEqual(command.call_count, 1)

    def test_background_password_rejects_symlink_or_loose_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            recovery = file_server.recovery_password_path(home)
            recovery.parent.mkdir(parents=True)
            recovery.write_text("test-password\n", encoding="utf-8")
            recovery.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "owner-only"):
                file_server.kopia_backup_environment(home)
            recovery.chmod(0o600)
            self.assertEqual(file_server.kopia_backup_environment(home)["KOPIA_PASSWORD"], "test-password")
            other = home / "other"
            recovery.rename(other)
            recovery.symlink_to(other)
            with self.assertRaisesRegex(RuntimeError, "owner-only"):
                file_server.kopia_backup_environment(home)

    def test_require_local_directory_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target = parent / "real"
            target.mkdir()
            link = parent / "link"
            link.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "real directory"):
                file_server.require_local_directory(link, parent)

    def test_latest_snapshot_requires_recent_error_free_source(self) -> None:
        now = datetime(2026, 9, 12, 8, tzinfo=timezone.utc)
        snapshot = {
            "id": "snapshot-id",
            "source": {"path": str(file_server.share_path())},
            "stats": {"errorCount": 0, "ignoredErrorCount": 0},
            "endTime": (now - timedelta(hours=2)).isoformat(),
        }
        with patch.object(file_server, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps([snapshot]), "")):
            self.assertEqual(file_server.latest_snapshot(Path.home(), now=now), "snapshot-id")
        for change in (
            {"source": {"path": "/tmp/other"}},
            {"stats": {"errorCount": 1, "ignoredErrorCount": 0}},
            {"endTime": (now - timedelta(days=3)).isoformat()},
        ):
            modified = {**snapshot, **change}
            with patch.object(file_server, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps([modified]), "")):
                with self.assertRaises(RuntimeError):
                    file_server.latest_snapshot(Path.home(), now=now)

    def test_snapshot_due_catches_missing_stale_and_failed_backups(self) -> None:
        now = datetime(2026, 9, 12, 8, tzinfo=timezone.utc)
        snapshot = {
            "id": "snapshot-id", "source": {"path": str(file_server.share_path())},
            "stats": {"errorCount": 0, "ignoredErrorCount": 0},
            "endTime": (now - timedelta(hours=23)).isoformat(),
        }
        environment = {"KOPIA_PASSWORD": "test"}
        for data, expected in (
            ([], True),
            ([snapshot], False),
            ([{**snapshot, "endTime": (now - timedelta(hours=24)).isoformat()}], True),
            ([{**snapshot, "stats": {"errorCount": 1, "ignoredErrorCount": 0}}], True),
        ):
            with patch.object(file_server, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(data), "")) as command:
                self.assertEqual(file_server.snapshot_due(Path.home(), env=environment, now=now), expected)
                self.assertIn("--no-use-keychain", command.call_args.args[0])

    def test_reconcile_never_backs_up_wrong_or_unmounted_volumes(self) -> None:
        with patch.object(file_server, "read_enrollment", return_value=file_server.Enrollment("source", "backup")), patch.object(
            file_server, "volume_info", return_value=file_server.Volume(file_server.SOURCE_MOUNT, "wrong"),
        ), patch.object(file_server, "shares") as shares, patch.object(file_server, "_backup_locked") as backup:
            with self.assertRaisesRegex(RuntimeError, "unexpected source"):
                file_server.reconcile(Path.home())
            shares.assert_not_called()
            backup.assert_not_called()

    def test_reconcile_catches_up_only_when_due(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".config/mise-darwin").mkdir(parents=True)
            with patch.object(file_server, "read_enrollment", return_value=file_server.Enrollment("source", "backup")), patch.object(
                file_server, "volume_info", return_value=file_server.Volume(file_server.SOURCE_MOUNT, "source"),
            ), patch.object(file_server, "shares", return_value={}), patch.object(
                file_server, "require_private_share",
            ), patch.object(file_server, "require_time_machine_destination"), patch.object(
                file_server, "smb_service_listening", return_value=True,
            ), patch.object(
                file_server, "require_volumes",
            ), patch.object(file_server, "kopia_backup_environment", return_value={"KOPIA_PASSWORD": "test"}), patch.object(
                file_server, "repository_status",
            ), patch.object(file_server, "snapshot_due", side_effect=[False, True, True]), patch.object(
                file_server, "_backup_locked",
            ) as backup, patch.object(file_server, "require_online_mirror"), patch.object(
                file_server, "require_smb_storage_access",
            ) as privacy:
                file_server.reconcile(home)
                backup.assert_not_called()
                file_server.reconcile(home)
                backup.assert_called_once()
                privacy.side_effect = RuntimeError("SMB privacy refusal")
                with self.assertRaisesRegex(RuntimeError, "SMB: SMB privacy refusal"):
                    file_server.reconcile(home)
                self.assertEqual(backup.call_count, 2)

    def test_reconcile_reports_smb_failure_after_catching_up_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".config/mise-darwin").mkdir(parents=True)
            with patch.object(file_server, "read_enrollment", return_value=file_server.Enrollment("source", "backup")), patch.object(
                file_server, "volume_info", return_value=file_server.Volume(file_server.SOURCE_MOUNT, "source"),
            ), patch.object(file_server, "require_volumes"), patch.object(
                file_server, "kopia_backup_environment", return_value={"KOPIA_PASSWORD": "test"},
            ), patch.object(file_server, "repository_status"), patch.object(
                file_server, "snapshot_due", return_value=True,
            ), patch.object(file_server, "_backup_locked") as backup, patch.object(
                file_server, "shares", side_effect=RuntimeError("share records unavailable"),
            ), patch.object(file_server, "require_online_mirror"):
                with self.assertRaisesRegex(RuntimeError, "SMB: share records unavailable"):
                    file_server.reconcile(home)
                backup.assert_called_once()

    def test_backup_lock_refuses_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            folder = home / ".config/mise-darwin"
            folder.mkdir(parents=True)
            (folder / "file-server-backup.lock").symlink_to(home / "unrelated")
            with self.assertRaises(OSError):
                with file_server.backup_lock(home):
                    pass


if __name__ == "__main__":
    unittest.main()
