from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ACCEPTED_ARCHIVE_SUFFIXES = {".zip", ".rar", ".7z"}
IGNORED_NAMES = {"__MACOSX", ".DS_Store"}
STATE_FILE_NAME = ".headrush-mx5-state.json"


@dataclass(frozen=True)
class BrowserEntry:
    path: Path
    is_directory: bool

    @property
    def is_selectable(self) -> bool:
        return self.is_directory or self.path.suffix.lower() in ACCEPTED_ARCHIVE_SUFFIXES


def default_start_path(project_root: Path) -> Path:
    presets_dir = project_root / "Presets"
    if presets_dir.is_dir():
        return presets_dir
    return Path.home()


def is_supported_archive(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in ACCEPTED_ARCHIVE_SUFFIXES


def list_browser_entries(directory: Path) -> list[BrowserEntry]:
    entries: list[BrowserEntry] = []
    for child in directory.iterdir():
        if child.name in IGNORED_NAMES:
            continue
        if child.is_dir():
            entries.append(BrowserEntry(path=child, is_directory=True))
            continue
        if is_supported_archive(child):
            entries.append(BrowserEntry(path=child, is_directory=False))

    return sorted(
        entries,
        key=lambda entry: (
            not entry.is_directory,
            entry.path.name.casefold(),
        ),
    )
