"""Unit tests for utils.reports.report_path. Run with: python -m unittest discover tests"""

import os
import tempfile
import unittest
from unittest import mock
from utils.reports import report_path


class ReportPathTests(unittest.TestCase):
    def test_current_folder_without_report_dir(self):
        with mock.patch.dict(os.environ, {"REPORT_DIR": ""}):
            self.assertEqual(report_path("x.html"), os.path.abspath("x.html"))

    def test_report_dir_is_created_and_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "out")
            with mock.patch.dict(os.environ, {"REPORT_DIR": folder}):
                self.assertEqual(report_path("x.html"), os.path.join(folder, "x.html"))
            self.assertTrue(os.path.isdir(folder))


if __name__ == "__main__":
    unittest.main()
