from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ACCEPTED_ARCHIVE_SUFFIXES = {".zip", ".rar", ".7z"}
IGNORED_NAMES = {"__MACOSX", ".DS_Store"}
STATE_FILE_NAME = ".headrush-mx5-state.json"


@dataclass(frozen=True)
class BrowserEntry:
    path: Path
    is_directory: bool
    display_name: str | None = None

    @property
    def is_selectable(self) -> bool:
        return self.is_directory or self.path.suffix.lower() in ACCEPTED_ARCHIVE_SUFFIXES

    @property
    def label(self) -> str:
        if self.display_name:
            return self.display_name
        return self.path.name or str(self.path)


def default_start_path() -> None:
    return None


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


def list_root_entries() -> list[BrowserEntry]:
    roots = _windows_roots() if os.name == "nt" else _posix_roots()
    deduped: dict[str, BrowserEntry] = {}
    for root in roots:
        deduped[str(root.path)] = root

    return sorted(deduped.values(), key=lambda entry: entry.label.casefold())


def _windows_roots() -> list[BrowserEntry]:
    entries: list[BrowserEntry] = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = Path(f"{letter}:\\")
        if drive.exists():
            entries.append(BrowserEntry(path=drive, is_directory=True, display_name=f"{letter}:\\"))
    return entries


def _posix_roots() -> list[BrowserEntry]:
    root_paths = [Path("/")]
    for mounts_dir, depth in ((Path("/Volumes"), 1), (Path("/mnt"), 1), (Path("/media"), 2), (Path("/run/media"), 2)):
        root_paths.extend(_discover_mounts(mounts_dir, depth))

    entries: list[BrowserEntry] = []
    for path in root_paths:
        if not path.exists() or not path.is_dir():
            continue
        label = "System /" if path == Path("/") else str(path)
        entries.append(BrowserEntry(path=path, is_directory=True, display_name=label))
    return entries


def _discover_mounts(parent: Path, depth: int) -> Iterable[Path]:
    if depth <= 0 or not parent.exists() or not parent.is_dir():
        return []

    discovered: list[Path] = []
    try:
        children = [child for child in parent.iterdir() if child.is_dir() and child.name not in IGNORED_NAMES]
    except PermissionError:
        return []

    if depth == 1:
        return children

    for child in children:
        discovered.extend(_discover_mounts(child, depth - 1))
    return discovered
