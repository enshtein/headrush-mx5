from __future__ import annotations

import ctypes
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath
from typing import Iterable

import rarfile

ACCEPTED_ARCHIVE_SUFFIXES = {".zip", ".rar", ".7z"}
BROWSABLE_ARCHIVE_SUFFIXES = {".zip", ".rar"}
PRESET_FILE_SUFFIX = ".rig"
IR_FILE_SUFFIX = ".wav"
PACK_HINT_DIRECTORY_NAMES = {"rigs", "irs", "impulses", "impulse responses", "presets", "patches", "cab", "cabs"}
MAX_GENERIC_FOLDER_CHILDREN = 40
MAX_ANALYSIS_FILES = 4000
MAX_ANALYSIS_DEPTH = 6
IGNORED_NAMES = {"__MACOSX", ".DS_Store"}
STATE_FILE_NAME = ".headrush-mx5-state.json"
ARCHIVE_ROOT = PurePosixPath(".")
HEADRUSH_VOLUME_NAME = "headrush"


@dataclass(frozen=True)
class BrowserEntry:
    path: PurePath | Path
    is_directory: bool
    display_name: str | None = None
    is_parent_link: bool = False

    @property
    def is_selectable(self) -> bool:
        return self.is_directory or self.path.suffix.lower() in ACCEPTED_ARCHIVE_SUFFIXES

    @property
    def label(self) -> str:
        if self.display_name:
            return self.display_name
        return self.path.name or str(self.path)


@dataclass(frozen=True)
class FolderAnalysis:
    presets: tuple[str, ...]
    irs: tuple[str, ...]
    note: str | None = None

    @property
    def has_headrush_content(self) -> bool:
        return bool(self.presets or self.irs)


@dataclass(frozen=True)
class ArchiveMember:
    path: PurePosixPath
    is_directory: bool


def default_start_path() -> None:
    return None


def is_supported_archive(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in ACCEPTED_ARCHIVE_SUFFIXES


def is_browsable_archive(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in BROWSABLE_ARCHIVE_SUFFIXES


def analyze_headrush_folder(directory: Path) -> FolderAnalysis:
    direct_children = _safe_list_directory(directory)
    if _should_skip_deep_folder_analysis(directory, direct_children):
        return FolderAnalysis(
            presets=(),
            irs=(),
            note="Open a more specific folder to inspect HeadRush files.",
        )

    presets: list[str] = []
    irs: list[str] = []
    scanned_files = 0

    for root, dirnames, filenames in os.walk(directory):
        dirnames[:] = [dirname for dirname in dirnames if not _is_ignored_name(dirname)]
        root_path = Path(root)
        relative_root = root_path.relative_to(directory)

        if len(relative_root.parts) > MAX_ANALYSIS_DEPTH:
            dirnames[:] = []
            continue

        for filename in filenames:
            if _is_ignored_name(filename):
                continue

            scanned_files += 1
            if scanned_files > MAX_ANALYSIS_FILES:
                return FolderAnalysis(
                    presets=tuple(sorted(presets, key=str.casefold)),
                    irs=tuple(sorted(irs, key=str.casefold)),
                    note="Analysis stopped early. Open a more specific folder for a complete result.",
                )

            file_path = root_path / filename
            relative_path = file_path.relative_to(directory).as_posix()
            suffix = file_path.suffix.lower()

            if suffix == PRESET_FILE_SUFFIX:
                presets.append(relative_path)
            elif suffix == IR_FILE_SUFFIX:
                irs.append(relative_path)

    return FolderAnalysis(
        presets=tuple(sorted(presets, key=str.casefold)),
        irs=tuple(sorted(irs, key=str.casefold)),
    )


def inspect_archive(archive_path: Path, current_dir: PurePosixPath) -> tuple[list[BrowserEntry], FolderAnalysis]:
    members = read_archive_members(archive_path)
    return list_archive_entries(current_dir, members), analyze_headrush_archive(members)


def read_archive_members(archive_path: Path) -> tuple[ArchiveMember, ...]:
    suffix = archive_path.suffix.lower()
    members: list[ArchiveMember] = []

    if suffix == ".zip":
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                member = _archive_member_from_name(info.filename, info.is_dir())
                if member is not None:
                    members.append(member)
    elif suffix == ".rar":
        with rarfile.RarFile(archive_path) as archive:
            for info in archive.infolist():
                member = _archive_member_from_name(info.filename, info.is_dir())
                if member is not None:
                    members.append(member)
    else:
        raise ValueError(f"Unsupported archive type: {archive_path.suffix}")

    return tuple(members)


def list_archive_entries(current_dir: PurePosixPath, members: tuple[ArchiveMember, ...]) -> list[BrowserEntry]:
    children: dict[str, BrowserEntry] = {}

    for member in members:
        if current_dir == ARCHIVE_ROOT:
            relative_path = member.path
        else:
            if not _is_archive_relative_to(member.path, current_dir):
                continue
            relative_path = member.path.relative_to(current_dir)

        if relative_path == ARCHIVE_ROOT or not relative_path.parts:
            continue

        child_name = relative_path.parts[0]
        child_path = PurePosixPath(child_name) if current_dir == ARCHIVE_ROOT else current_dir / child_name
        is_directory = len(relative_path.parts) > 1 or member.is_directory
        child_key = child_path.as_posix()

        existing = children.get(child_key)
        if existing is not None:
            if is_directory and not existing.is_directory:
                children[child_key] = BrowserEntry(path=child_path, is_directory=True)
            continue

        children[child_key] = BrowserEntry(path=child_path, is_directory=is_directory)

    sorted_entries = sorted(
        children.values(),
        key=lambda entry: (
            not entry.is_directory,
            entry.label.casefold(),
        ),
    )

    return [
        BrowserEntry(path=current_dir, is_directory=True, display_name="../", is_parent_link=True),
        *sorted_entries,
    ]


def analyze_headrush_archive(members: tuple[ArchiveMember, ...]) -> FolderAnalysis:
    presets = [member.path.as_posix() for member in members if not member.is_directory and member.path.suffix.lower() == PRESET_FILE_SUFFIX]
    irs = [member.path.as_posix() for member in members if not member.is_directory and member.path.suffix.lower() == IR_FILE_SUFFIX]
    return FolderAnalysis(
        presets=tuple(sorted(presets, key=str.casefold)),
        irs=tuple(sorted(irs, key=str.casefold)),
    )


def list_browser_entries(directory: Path) -> list[BrowserEntry]:
    entries: list[BrowserEntry] = []
    for child in directory.iterdir():
        if _is_ignored_name(child.name):
            continue
        if child.is_dir():
            entries.append(BrowserEntry(path=child, is_directory=True))
            continue
        if is_supported_archive(child):
            entries.append(BrowserEntry(path=child, is_directory=False))

    sorted_entries = sorted(
        entries,
        key=lambda entry: (
            not entry.is_directory,
            entry.path.name.casefold(),
        ),
    )

    return [BrowserEntry(path=directory, is_directory=True, display_name="../", is_parent_link=True), *sorted_entries]


def list_root_entries() -> list[BrowserEntry]:
    roots = _windows_roots() if os.name == "nt" else _posix_roots()
    deduped: dict[str, BrowserEntry] = {}
    for root in roots:
        deduped[str(root.path)] = root

    return sorted(deduped.values(), key=lambda entry: entry.label.casefold())


def find_headrush_mount() -> Path | None:
    if os.name == "nt":
        for root in _windows_roots():
            label = _get_windows_volume_label(Path(root.path))
            if label is not None and label.casefold() == HEADRUSH_VOLUME_NAME:
                return Path(root.path)
        return None

    for root in _posix_roots():
        candidate = Path(root.path)
        if candidate.name.casefold() == HEADRUSH_VOLUME_NAME:
            return candidate
    return None


def _windows_roots() -> list[BrowserEntry]:
    entries: list[BrowserEntry] = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = Path(f"{letter}:\\")
        if drive.exists():
            entries.append(BrowserEntry(path=drive, is_directory=True, display_name=f"{letter}:\\"))
    return entries


def _get_windows_volume_label(drive: Path) -> str | None:
    if os.name != "nt":
        return None

    volume_name_buffer = ctypes.create_unicode_buffer(1024)
    filesystem_name_buffer = ctypes.create_unicode_buffer(1024)
    serial_number = ctypes.c_uint()
    max_component_length = ctypes.c_uint()
    filesystem_flags = ctypes.c_uint()

    result = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(str(drive)),
        volume_name_buffer,
        len(volume_name_buffer),
        ctypes.byref(serial_number),
        ctypes.byref(max_component_length),
        ctypes.byref(filesystem_flags),
        filesystem_name_buffer,
        len(filesystem_name_buffer),
    )
    if result == 0:
        return None
    return volume_name_buffer.value or None


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
        children = [child for child in parent.iterdir() if child.is_dir() and not _is_ignored_name(child.name)]
    except PermissionError:
        return []

    if depth == 1:
        return children

    for child in children:
        discovered.extend(_discover_mounts(child, depth - 1))
    return discovered


def _safe_list_directory(directory: Path) -> tuple[Path, ...]:
    try:
        return tuple(child for child in directory.iterdir() if not _is_ignored_name(child.name))
    except (PermissionError, FileNotFoundError, OSError):
        return ()


def _should_skip_deep_folder_analysis(directory: Path, direct_children: tuple[Path, ...]) -> bool:
    if directory.parent == directory:
        return True

    if _has_pack_hints(direct_children):
        return False

    if len(directory.parts) <= 3:
        return True

    return len(direct_children) > MAX_GENERIC_FOLDER_CHILDREN


def _has_pack_hints(children: tuple[Path, ...]) -> bool:
    for child in children:
        child_name = child.name.casefold()
        if child.is_dir() and child_name in PACK_HINT_DIRECTORY_NAMES:
            return True
        if child.is_file():
            suffix = child.suffix.lower()
            if suffix in {PRESET_FILE_SUFFIX, IR_FILE_SUFFIX, *ACCEPTED_ARCHIVE_SUFFIXES}:
                return True
    return False


def _archive_member_from_name(filename: str, is_directory: bool) -> ArchiveMember | None:
    normalized = filename.replace("\\", "/").strip("/")
    if not normalized:
        return None

    path = PurePosixPath(normalized)
    if any(_is_ignored_name(part) for part in path.parts):
        return None

    return ArchiveMember(path=path, is_directory=is_directory)


def _is_archive_relative_to(path: PurePosixPath, parent: PurePosixPath) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_ignored_name(name: str) -> bool:
    return name in IGNORED_NAMES or name.startswith("._")
