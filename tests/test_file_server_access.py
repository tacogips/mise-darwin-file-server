"""Regression coverage for storage privacy failures behind reachable SMB shares."""

from __future__ import annotations

import json
import plistlib
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.mise_darwin import file_server_access as access


class SmbStorageAccessTests(unittest.TestCase):
    def test_only_smb_storage_refusals_are_failures(self) -> None:
        message = (
            "Refusing TCCAccessRequest for service kTCCServiceSystemPolicyRemovableVolumes "
            "from client Sub:{/usr/sbin/smbd} in background session"
        )
        for candidate, expected in (
            (message, True),
            (message.replace("RemovableVolumes", "AllFiles"), True),
            (message.replace("/usr/sbin/smbd", "/usr/bin/other"), False),
            (message.replace("SystemPolicyRemovableVolumes", "AddressBook"), False),
            (message.replace("Refusing TCCAccessRequest", "AUTHREQ_SUBJECT"), False),
        ):
            with self.subTest(candidate=candidate):
                self.assertEqual(access.has_smb_privacy_denial(json.dumps([
                    {"eventMessage": candidate},
                ])), expected)
        self.assertFalse(access.has_smb_privacy_denial("[]"))

    def test_malformed_logs_cannot_report_healthy(self) -> None:
        for output in ("", "{}", "[null]", '[{"eventMessage": null}]'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                access.has_smb_privacy_denial(output)

    def test_ownership_must_be_enabled_on_the_expected_mount(self) -> None:
        mount = Path("/test/raid")
        for info in (
            {"MountPoint": str(mount), "GlobalPermissionsEnabled": False},
            {"MountPoint": str(mount)},
            {"MountPoint": "/test/other", "GlobalPermissionsEnabled": True},
        ):
            result = subprocess.CompletedProcess([], 0, plistlib.dumps(info).decode(), "")
            with patch.object(access, "run", return_value=result) as command:
                with self.assertRaises(RuntimeError):
                    access.require_smb_storage_access(mount)
                self.assertEqual(command.call_count, 1)

    def test_log_failure_and_refusal_are_actionable_without_printing_logs(self) -> None:
        mount = Path("/test/raid")
        info = subprocess.CompletedProcess([], 0, plistlib.dumps({
            "MountPoint": str(mount), "GlobalPermissionsEnabled": True,
        }).decode(), "")
        denial = json.dumps([{"eventMessage": (
            "Refusing TCCAccessRequest kTCCServiceSystemPolicyRemovableVolumes "
            "/usr/sbin/smbd sensitive-detail"
        )}])
        for result, expected in (
            (subprocess.CompletedProcess([], 1, "", "sensitive-detail"), "unavailable"),
            (subprocess.CompletedProcess([], 0, denial, ""), "Full Disk Access"),
        ):
            with patch.object(access, "run", side_effect=[info, result]):
                with self.assertRaisesRegex(RuntimeError, expected) as raised:
                    access.require_smb_storage_access(mount)
                self.assertNotIn("sensitive-detail", str(raised.exception))
        with patch.object(access, "run", side_effect=[
            info, subprocess.CompletedProcess([], 0, "[]", ""),
        ]) as command:
            access.require_smb_storage_access(mount)
            self.assertEqual(command.call_args.args[0][0], "/usr/bin/log")
            self.assertEqual(command.call_args.kwargs["timeout"], 15)
