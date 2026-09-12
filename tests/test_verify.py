"""Checks for the nested mise verification environment."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.mise_darwin import verify


class MiseDoctorCheckTests(unittest.TestCase):
    def test_prefers_the_mise_path_used_by_shims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "bin/mise"
            binary.parent.mkdir()
            binary.touch()
            with patch.object(verify, "run", return_value=subprocess.CompletedProcess([], 0, "No problems found\n", "")) as command:
                result = verify.mise_doctor_check(binary)()
            self.assertTrue(result.ok)
            self.assertEqual(command.call_args.args[0], ["mise", "doctor"])
            environment = command.call_args.kwargs["env"]
            self.assertEqual(environment["PATH"].split(os.pathsep)[0], str(binary.parent))


if __name__ == "__main__":
    unittest.main()
