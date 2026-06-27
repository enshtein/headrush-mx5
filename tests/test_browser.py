from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from headrush_mx5.browser import default_start_path, list_browser_entries


class BrowserTests(unittest.TestCase):
    def test_default_start_path_prefers_project_presets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            presets_dir = project_root / "Presets"
            presets_dir.mkdir()

            self.assertEqual(default_start_path(project_root), presets_dir)

    def test_list_browser_entries_returns_dirs_and_supported_archives_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Rigs").mkdir()
            (root / "Pack.zip").write_text("zip", encoding="utf-8")
            (root / "Pack.rar").write_text("rar", encoding="utf-8")
            (root / "notes.txt").write_text("ignore", encoding="utf-8")
            (root / "__MACOSX").mkdir()

            entries = list_browser_entries(root)

            self.assertEqual([entry.path.name for entry in entries], ["Rigs", "Pack.rar", "Pack.zip"])


if __name__ == "__main__":
    unittest.main()
