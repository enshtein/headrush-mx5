from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from headrush_mx5.browser import BrowserEntry, default_start_path, list_browser_entries, list_root_entries


class BrowserTests(unittest.TestCase):
    def test_default_start_path_opens_root_chooser(self) -> None:
        self.assertIsNone(default_start_path())

    def test_list_browser_entries_returns_dirs_and_supported_archives_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Rigs").mkdir()
            (root / "Pack.zip").write_text("zip", encoding="utf-8")
            (root / "Pack.rar").write_text("rar", encoding="utf-8")
            (root / "notes.txt").write_text("ignore", encoding="utf-8")
            (root / "__MACOSX").mkdir()

            entries = list_browser_entries(root)

            self.assertEqual([entry.label for entry in entries], ["../", "Rigs", "Pack.rar", "Pack.zip"])
            self.assertTrue(entries[0].is_parent_link)
            self.assertEqual(entries[0].path, root)

    def test_list_root_entries_uses_posix_roots(self) -> None:
        fake_entries = [
            BrowserEntry(path=Path("/"), is_directory=True, display_name="System /"),
            BrowserEntry(path=Path("/Volumes/External"), is_directory=True, display_name="/Volumes/External"),
        ]

        with patch("headrush_mx5.browser.os.name", "posix"):
            with patch("headrush_mx5.browser._posix_roots", return_value=fake_entries):
                entries = list_root_entries()

        self.assertEqual([entry.label for entry in entries], ["/Volumes/External", "System /"])

    def test_list_root_entries_uses_windows_roots(self) -> None:
        fake_entries = [BrowserEntry(path=Path("C:\\"), is_directory=True, display_name="C:\\")]

        with patch("headrush_mx5.browser.os.name", "nt"):
            with patch("headrush_mx5.browser._windows_roots", return_value=fake_entries):
                entries = list_root_entries()

        self.assertEqual([entry.label for entry in entries], ["C:\\"])


if __name__ == "__main__":
    unittest.main()
