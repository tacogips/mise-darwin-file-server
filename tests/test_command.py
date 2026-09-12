from __future__ import annotations

import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.mise_darwin.command import CommandError, atomic_write, manifest_lines, run, sync_directory, sync_file


class CommandTests(unittest.TestCase):
    def test_run_reports_timeout_without_environment_values(self) -> None:
        with patch("scripts.mise_darwin.command.subprocess.run", side_effect=subprocess.TimeoutExpired(["tool"], 1)):
            with self.assertRaisesRegex(CommandError, "timed out after 1s: tool") as error:
                run(["tool"], env={"SECRET": "private-value"}, timeout=1)
        self.assertNotIn("private-value", str(error.exception))

    def test_sync_file_replaces_legacy_symlink_without_writing_through_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.txt"
            legacy = root / "legacy.txt"
            target = root / "target.txt"
            source.write_text("managed", encoding="utf-8")
            legacy.write_text("legacy", encoding="utf-8")
            legacy.chmod(0o444)
            target.symlink_to(legacy)

            sync_file(source, target)

            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "managed")
            self.assertEqual(legacy.read_text(encoding="utf-8"), "legacy")

    def test_sync_file_skips_matching_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("managed", encoding="utf-8")

            sync_file(source, target)
            inode = target.stat().st_ino
            sync_file(source, target)

            self.assertEqual(target.stat().st_ino, inode)

    def test_sync_directory_mirrors_managed_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            (source / "nested").mkdir(parents=True)
            (source / "nested/current.txt").write_text("current", encoding="utf-8")
            (target / "nested").mkdir(parents=True)
            (target / "nested/stale.txt").write_text("stale", encoding="utf-8")

            sync_directory(source, target)

            self.assertEqual((target / "nested/current.txt").read_text(), "current")
            self.assertFalse((target / "nested/stale.txt").exists())

    def test_atomic_write_replaces_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            atomic_write(path, "first\n")
            atomic_write(path, "second\n", mode=0o600)
            self.assertEqual(path.read_text(), "second\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_manifest_lines_ignores_comments_and_empty_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.txt"
            path.write_text("# comment\n\nfirst\n second \n", encoding="utf-8")
            self.assertEqual(list(manifest_lines(path)), ["first", "second"])


if __name__ == "__main__":
    unittest.main()
