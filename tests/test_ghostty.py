from __future__ import annotations

import subprocess
import unittest
from unittest.mock import Mock, call, patch

from scripts.mise_darwin.command import CommandError
from scripts.mise_darwin.ghostty import reload_config


class GhosttyReloadTests(unittest.TestCase):
    @patch("scripts.mise_darwin.ghostty.run")
    def test_reload_signals_only_matching_processes(self, run: Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "123\n456\n", "")

        reload_config()

        self.assertEqual(
            run.call_args_list,
            [
                call(["pgrep", "-x", "ghostty"], capture=True, check=False),
                call(["kill", "-USR2", "123"]),
                call(["kill", "-USR2", "456"]),
            ],
        )

    @patch("scripts.mise_darwin.ghostty.run")
    def test_reload_skips_absent_app(self, run: Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 1, "", "")

        reload_config()

        run.assert_called_once_with(["pgrep", "-x", "ghostty"], capture=True, check=False)

    @patch("scripts.mise_darwin.ghostty.run")
    def test_reload_rejects_unexpected_process_id(self, run: Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "123 other", "")

        with self.assertRaises(CommandError):
            reload_config()

        run.assert_called_once()
