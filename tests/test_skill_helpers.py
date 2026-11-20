"""
Unit tests for the helper scripts used by the Claude skills.
Run with: python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import env_list  # noqa: E402
from pm_query import check_sql  # noqa: E402


class ReadOnlySqlTests(unittest.TestCase):
    def test_select_allowed(self):
        self.assertEqual(check_sql("SELECT id FROM User WHERE name = 'x';"), "SELECT id FROM User WHERE name = 'x'")

    def test_writes_refused(self):
        for sql in ("DELETE FROM User", "UPDATE Project SET name='x'", "SELECT 1; DROP TABLE User",
                    "WITH x AS (SELECT 1) DELETE FROM User", "SELECT * FROM User INTO OUTFILE '/tmp/x'; "):
            with self.assertRaises(ValueError, msg=sql):
                check_sql(sql)

    def test_keyword_inside_text_is_fine(self):
        check_sql("SELECT id FROM Task WHERE name LIKE '%update docs%'")


class EnvListTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".env")
        with os.fdopen(fd, "w") as f:
            f.write("CLIENT_SECRET=abc\nIGNORE_ID=1,2\n")
        self.patch = mock.patch.object(env_list, "ENV_FILE", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        os.remove(self.path)

    def call(self, *args):
        with mock.patch.object(sys, "argv", ["env_list.py", *args]):
            env_list.main()

    def test_add_and_remove_ids(self):
        self.call("add", "IGNORE_ID", "113,112")
        self.call("remove", "IGNORE_ID", "1")
        self.assertIn("IGNORE_ID=2,113,112", open(self.path).read())

    def test_secret_keys_refused(self):
        with self.assertRaises(SystemExit):
            self.call("get", "CLIENT_SECRET")


if __name__ == "__main__":
    unittest.main()
