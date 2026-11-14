"""Small subprocess and filesystem primitives shared by provisioning commands."""

from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path


class CommandError(RuntimeError):
    """Raised when an external command fails."""


def run(
    arguments: Sequence[str | os.PathLike[str]],
    *,
    capture: bool = False,
    check: bool = True,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    quiet: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [os.fspath(argument) for argument in arguments]
    try:
        result = subprocess.run(
            command,
            check=False,
            env=env,
            input=input_text,
            stdout=subprocess.PIPE if capture or quiet else None,
            stderr=subprocess.PIPE if capture or quiet else None,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise CommandError(f"command timed out after {timeout}s: {' '.join(command)}") from error
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise CommandError(f"command failed ({result.returncode}): {' '.join(command)}{suffix}")
    return result


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def sync_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_dir() and not target.is_symlink():
        raise IsADirectoryError(target)
    if (
        not target.is_symlink()
        and target.is_file()
        and filecmp.cmp(source, target, shallow=False)
        and source.stat().st_mode & 0o777 == target.stat().st_mode & 0o777
    ):
        return

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    os.close(descriptor)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def sync_directory(source: Path, target: Path) -> None:
    """Mirror one managed directory without touching neighboring directories."""
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise NotADirectoryError(target)
    target.mkdir(parents=True, exist_ok=True)

    source_names = {child.name for child in source.iterdir()}
    for existing in target.iterdir():
        if existing.name not in source_names:
            remove_path(existing)

    for child in source.iterdir():
        destination = target / child.name
        if child.is_symlink():
            expected = os.readlink(child)
            if not destination.is_symlink() or os.readlink(destination) != expected:
                remove_path(destination)
                destination.symlink_to(expected, target_is_directory=child.is_dir())
        elif child.is_dir():
            sync_directory(child, destination)
        else:
            if destination.is_dir() and not destination.is_symlink():
                remove_path(destination)
            sync_file(child, destination)


def atomic_write(path: Path, content: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def manifest_lines(path: Path) -> Iterable[str]:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            yield line
