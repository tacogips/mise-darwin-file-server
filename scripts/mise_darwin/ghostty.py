"""Reload a running Ghostty after mise applies its managed configuration."""

from __future__ import annotations

from .command import CommandError, run


def reload_config() -> None:
    result = run(["pgrep", "-x", "ghostty"], capture=True, check=False)
    if result.returncode == 1:
        print("Ghostty is not running; configuration will load at next launch")
        return
    if result.returncode != 0:
        raise CommandError(f"could not find Ghostty processes (pgrep exited {result.returncode})")

    for pid in result.stdout.splitlines():
        if not pid.isdecimal():
            raise CommandError(f"unexpected Ghostty process ID: {pid!r}")
        run(["kill", "-USR2", pid])
    print("Reloaded Ghostty configuration")
