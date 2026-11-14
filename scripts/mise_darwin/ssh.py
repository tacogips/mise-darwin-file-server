"""Reproduce a macOS SSH listener without modifying Apple's system plist."""

from __future__ import annotations

import os
import plistlib
import re
import socket
import stat
import sys
from pathlib import Path
from typing import cast

from .command import atomic_write, run

LABEL = "dev.mise.sshd"
TARGET = Path(f"/Library/LaunchDaemons/{LABEL}.plist")
SYSTEM_LABEL = "com.openssh.sshd"
SYSTEM_PLIST = Path("/System/Library/LaunchDaemons/ssh.plist")
OWNER_KEY = "MiseDarwinManagedSSH"


def parse_port(value: str) -> int:
    if not re.fullmatch(r"[0-9]+", value) or not 1 <= int(value) <= 65535:
        raise ValueError("SSH_PORT must be an integer between 1 and 65535")
    return int(value)


def daemon_content(port: int) -> str:
    parse_port(str(port))
    return plistlib.dumps({
        "Label": LABEL,
        OWNER_KEY: True,
        "Program": "/usr/libexec/sshd-keygen-wrapper",
        "ProgramArguments": ["sshd-keygen-wrapper"],
        "Sockets": {"Listeners": {"SockServiceName": str(port)}},
        "inetdCompatibility": {"Wait": False, "Instances": 42},
        "StandardErrorPath": "/dev/null",
        "POSIXSpawnType": "Interactive",
        "MaterializeDatalessFiles": True,
    }, sort_keys=True).decode("utf-8")


def read_managed(path: Path) -> str | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"refusing non-regular SSH daemon file: {path}")
    content = path.read_text(encoding="utf-8")
    data: object = plistlib.loads(content.encode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"refusing unmanaged SSH daemon file: {path}")
    settings = cast(dict[str, object], data)
    if settings.get(OWNER_KEY) is not True or settings.get("Label") != LABEL:
        raise RuntimeError(f"refusing unmanaged SSH daemon file: {path}")
    return content


def service(label: str) -> str | None:
    result = run(["/bin/launchctl", "print", f"system/{label}"], capture=True, check=False)
    if result.returncode == 0:
        return result.stdout
    if "Could not find service" not in result.stderr:
        raise RuntimeError(f"cannot inspect launchd service {label}: {result.stderr.strip()}")
    return None


def disabled(label: str) -> bool:
    output = run(["/bin/launchctl", "print-disabled", "system"], capture=True).stdout
    return re.search(rf'"{re.escape(label)}"\s*=>\s*disabled', output) is not None


def runtime_port(output: str | None) -> int | None:
    match = re.search(r"^\s*service name = ([0-9]+)\s*$", output or "", re.MULTILINE)
    return int(match[1]) if match else None


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
            connection.settimeout(3)
            return connection.recv(256).startswith(b"SSH-2.0-")
    except OSError:
        return False


def current(port: int) -> bool:
    return (
        read_managed(TARGET) == daemon_content(port)
        and TARGET.stat().st_uid == 0
        and stat.S_IMODE(TARGET.stat().st_mode) == 0o644
        and runtime_port(service(LABEL)) == port
        and not disabled(LABEL)
        and disabled(SYSTEM_LABEL)
        and service(SYSTEM_LABEL) is None
        and listening(port)
    )


def status(port: int) -> bool:
    ready = current(port)
    print(f"{'ok' if ready else 'ERR'} SSH on port {port}; managed service {LABEL}")
    return ready


def enable(port: int, *, dry_run: bool = False) -> None:
    parse_port(str(port))
    previous = read_managed(TARGET)
    if current(port):
        print(f"SSH on port {port} is already current")
        return
    if dry_run:
        print(f"Would install/update {TARGET} (root:wheel, mode 644)")
        print(f"Would replace the built-in SSH listener with {LABEL} on port {port}")
        print("Authentication and user access remain governed by macOS SSH settings")
        return
    if sys.platform != "darwin":
        raise RuntimeError("SSH provisioning requires macOS")
    if os.geteuid() != 0:
        run([
            "sudo", sys.executable, "-m", "scripts.mise_darwin", "ssh", "enable",
            "--port", str(port),
        ])
        return

    old_service = service(LABEL)
    native_service = service(SYSTEM_LABEL)
    native_disabled = disabled(SYSTEM_LABEL)
    managed_disabled = disabled(LABEL)
    # Generate only missing host keys, and reject invalid SSH configuration
    # before touching the current listener.
    run(["/usr/bin/ssh-keygen", "-A"])
    run(["/usr/sbin/sshd", "-t"])
    content = daemon_content(port)
    try:
        if previous != content or TARGET.stat().st_uid != 0 or stat.S_IMODE(TARGET.stat().st_mode) != 0o644:
            atomic_write(TARGET, content, mode=0o644)
            os.chown(TARGET, 0, 0)
        if old_service is not None:
            run(["/bin/launchctl", "bootout", f"system/{LABEL}"])
        if not native_disabled:
            run(["/bin/launchctl", "disable", f"system/{SYSTEM_LABEL}"])
        if native_service is not None:
            run(["/bin/launchctl", "bootout", f"system/{SYSTEM_LABEL}"])
        if managed_disabled:
            run(["/bin/launchctl", "enable", f"system/{LABEL}"])
        run(["/bin/launchctl", "bootstrap", "system", TARGET])
        if not current(port):
            raise RuntimeError(f"SSH did not converge on port {port}")
    except Exception:
        print("SSH apply failed; restoring the previous listener", file=sys.stderr)
        run(["/bin/launchctl", "bootout", f"system/{LABEL}"], check=False)
        if previous is not None:
            atomic_write(TARGET, previous, mode=0o644)
        else:
            TARGET.unlink(missing_ok=True)
        if managed_disabled:
            run(["/bin/launchctl", "disable", f"system/{LABEL}"])
        if old_service is not None:
            run(["/bin/launchctl", "bootstrap", "system", TARGET])
        if not native_disabled:
            run(["/bin/launchctl", "enable", f"system/{SYSTEM_LABEL}"])
        if native_service is not None and service(SYSTEM_LABEL) is None:
            run(["/bin/launchctl", "bootstrap", "system", SYSTEM_PLIST])
        raise
    print(f"SSH enabled on port {port}; connect with ssh -p {port} <user>@<tailscale-host>")
