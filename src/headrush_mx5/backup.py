from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable


BackupProgressCallback = Callable[[int, int, str], None]


def backup_headrush_device(source_root: Path, destination_root: Path, progress_callback: BackupProgressCallback | None = None) -> int:
    if not source_root.is_dir():
        raise ValueError("The connected HeadRush device path is not available.")

    file_paths = sorted(
        (path for path in source_root.rglob("*") if path.is_file() and not _is_ignored_backup_name(path.name)),
        key=lambda path: path.as_posix().casefold(),
    )
    total_files = len(file_paths)

    if destination_root.exists():
        for child in destination_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        destination_root.mkdir(parents=True, exist_ok=True)

    for path in sorted(
        (path for path in source_root.rglob("*") if path.is_dir() and not _is_ignored_backup_name(path.name)),
        key=lambda path: path.as_posix().casefold(),
    ):
        relative_path = path.relative_to(source_root)
        (destination_root / relative_path).mkdir(parents=True, exist_ok=True)

    copied_files = 0
    for path in file_paths:
        relative_path = path.relative_to(source_root)
        target_path = destination_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target_path)
        copied_files += 1
        if progress_callback is not None:
            progress_callback(copied_files, total_files, relative_path.as_posix())

    if progress_callback is not None and total_files == 0:
        progress_callback(0, 0, "")

    return copied_files


def _is_ignored_backup_name(name: str) -> bool:
    return name in {".DS_Store", "__MACOSX"} or name.startswith("._")
