from __future__ import annotations

import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.mise_darwin import ssh
from scripts.mise_darwin.__main__ import parser


class SSHTests(unittest.TestCase):
    def test_port_rejects_out_of_range_and_injection(self) -> None:
        for value in ("", "0", "65536", "-22", "22;true", "22\n", "22.0", " 22", "２２"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ssh.parse_port(value)
        self.assertEqual(ssh.parse_port("2222"), 2222)
        self.assertEqual(ssh.parse_port("65535"), 65535)

    def test_cli_uses_environment_and_explicit_port_survives_sudo(self) -> None:
        with patch.dict(os.environ, {"SSH_PORT": "2222"}):
            self.assertEqual(parser().parse_args(["ssh", "enable"]).port, 2222)
            self.assertEqual(parser().parse_args(["ssh", "enable", "--port", "2200"]).port, 2200)

    def test_cli_rejects_missing_environment_without_a_fallback(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("sys.stderr"):
            with self.assertRaises(SystemExit) as error:
                parser().parse_args(["ssh", "enable"])
        self.assertEqual(error.exception.code, 2)

    def test_launchd_socket_controls_port(self) -> None:
        data = plistlib.loads(ssh.daemon_content(2222).encode())
        self.assertEqual(data["Sockets"]["Listeners"]["SockServiceName"], "2222")
        self.assertEqual(data["Program"], "/usr/libexec/sshd-keygen-wrapper")
        self.assertFalse(data["inetdCompatibility"]["Wait"])
        self.assertNotEqual(data["Label"], ssh.SYSTEM_LABEL)

    def test_read_refuses_unmanaged_files_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daemon.plist"
            path.write_bytes(plistlib.dumps({"Label": "another.service"}))
            with self.assertRaisesRegex(RuntimeError, "unmanaged"):
                ssh.read_managed(path)
            link = Path(directory) / "link.plist"
            link.symlink_to(path)
            with self.assertRaisesRegex(RuntimeError, "non-regular"):
                ssh.read_managed(link)
            self.assertIsNone(ssh.read_managed(Path(directory) / "missing"))

    def test_dry_run_never_writes_or_requests_sudo(self) -> None:
        with patch.object(ssh, "read_managed", return_value=None), patch.object(ssh, "current", return_value=False), patch.object(ssh, "run") as run, patch.object(ssh, "atomic_write") as write:
            ssh.enable(2222, dry_run=True)
        run.assert_not_called()
        write.assert_not_called()

    def test_current_enable_does_not_restart_service(self) -> None:
        with patch.object(ssh, "read_managed", return_value=ssh.daemon_content(2222)), patch.object(ssh, "current", return_value=True), patch.object(ssh, "run") as run, patch.object(ssh, "atomic_write") as write:
            ssh.enable(2222)
        run.assert_not_called()
        write.assert_not_called()

    def test_permission_elevation_passes_validated_port_as_argument(self) -> None:
        with patch.object(ssh, "read_managed", return_value=None), patch.object(ssh, "current", return_value=False), patch.object(ssh.sys, "platform", "darwin"), patch.object(ssh.os, "geteuid", return_value=501), patch.object(ssh, "run") as run:
            ssh.enable(2222)
        self.assertEqual(run.call_args.args[0][-4:], ["ssh", "enable", "--port", "2222"])
        self.assertEqual(run.call_args.args[0][0], "sudo")

    def test_failed_bootstrap_restores_native_listener_and_removes_new_file(self) -> None:
        commands: list[list[str]] = []

        def execute(arguments: list[str | Path], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            command = [str(item) for item in arguments]
            commands.append(command)
            if command[:3] == ["/bin/launchctl", "bootstrap", "system"] and command[-1] == str(ssh.TARGET):
                raise RuntimeError("port in use")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "daemon.plist"
            with patch.object(ssh, "TARGET", target), patch.object(ssh, "current", return_value=False), patch.object(ssh, "service", side_effect=[None, "native listener", None]), patch.object(ssh, "disabled", return_value=False), patch.object(ssh.sys, "platform", "darwin"), patch.object(ssh.os, "geteuid", return_value=0), patch.object(ssh.os, "chown"), patch.object(ssh, "run", side_effect=execute):
                with self.assertRaisesRegex(RuntimeError, "port in use"):
                    ssh.enable(2222)
            self.assertFalse(target.exists())
        self.assertIn(["/bin/launchctl", "enable", f"system/{ssh.SYSTEM_LABEL}"], commands)
        self.assertIn(["/bin/launchctl", "bootstrap", "system", str(ssh.SYSTEM_PLIST)], commands)

    def test_runtime_port_does_not_confuse_unrelated_numbers(self) -> None:
        self.assertEqual(ssh.runtime_port("runs = 2222\n\tservice name = 2200\n"), 2200)
        self.assertIsNone(ssh.runtime_port("runs = 2222"))

    def test_drifted_port_reloads_only_managed_listener(self) -> None:
        commands: list[list[str]] = []

        def execute(arguments: list[str | Path], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            command = [str(item) for item in arguments]
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "daemon.plist"
            target.write_text(ssh.daemon_content(2222))
            with patch.object(ssh, "TARGET", target), patch.object(ssh, "current", side_effect=[False, True]), patch.object(ssh, "service", side_effect=["old listener", None]), patch.object(ssh, "disabled", return_value=True), patch.object(ssh.sys, "platform", "darwin"), patch.object(ssh.os, "geteuid", return_value=0), patch.object(ssh.os, "chown"), patch.object(ssh, "run", side_effect=execute):
                ssh.enable(2200)
            self.assertEqual(target.read_text(), ssh.daemon_content(2200))
        self.assertIn(["/bin/launchctl", "bootout", f"system/{ssh.LABEL}"], commands)
        self.assertNotIn(["/bin/launchctl", "bootout", f"system/{ssh.SYSTEM_LABEL}"], commands)


if __name__ == "__main__":
    unittest.main()
