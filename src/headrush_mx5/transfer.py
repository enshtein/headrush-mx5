from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import rarfile

from headrush_mx5.browser import (
    ACCEPTED_ARCHIVE_SUFFIXES,
    ARCHIVE_ROOT,
    IR_FILE_SUFFIX,
    PRESET_FILE_SUFFIX,
    read_archive_members,
)

RIGS_DIR = Path("Rigs")
IRS_DIR = Path("Impulse Responses") / "USER"
SETLISTS_DIR = Path("Setlists")
RIG_NUMBER_START = 300
MAX_NAME_LENGTH = 96
INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]+')
IR_REFERENCE_PATTERN = re.compile(r"\[directory\]\(\[(?P<directory>[^\]]+)\]\)\[name\]\((?P<name>[^)]+)\)")


@dataclass(frozen=True)
class SourceItem:
    relative_path: PurePosixPath
    stem: str
    extension: str
    filesystem_path: Path | None = None
    archive_path: Path | None = None
    archive_member_path: PurePosixPath | None = None


@dataclass(frozen=True)
class TransferPackage:
    source_name: str
    rigs: tuple[SourceItem, ...]
    irs: tuple[SourceItem, ...]
    origin_label: str


@dataclass(frozen=True)
class TransferOptions:
    copy_rigs: bool
    copy_irs: bool
    create_setlist: bool
    setlist_name: str


@dataclass(frozen=True)
class TransferTarget:
    root: Path
    label: str
    kind: str


@dataclass(frozen=True)
class ImportedRig:
    rig_id: str
    rig_name: str
    target_path: Path


@dataclass(frozen=True)
class TransferResult:
    target: TransferTarget
    copied_rigs: tuple[ImportedRig, ...]
    copied_irs: tuple[Path, ...]
    setlist_path: Path | None
    notes: tuple[str, ...]


def build_transfer_package(
    current_path: Path | None,
    current_archive_path: Path | None,
    current_archive_dir: PurePosixPath,
) -> TransferPackage:
    if current_archive_path is not None:
        return _build_archive_package(current_archive_path, current_archive_dir)
    if current_path is None:
        raise ValueError("Open a folder or archive before preparing a transfer.")
    return _build_folder_package(current_path)


def resolve_transfer_target(project_root: Path, connected_device_root: Path | None) -> TransferTarget | None:
    if connected_device_root is not None and _looks_like_headrush_root(connected_device_root):
        return TransferTarget(root=connected_device_root, label="Connected HeadRush MX-5 device", kind="device")

    local_root = project_root / "HeadRush"
    if _looks_like_headrush_root(local_root):
        return TransferTarget(root=local_root, label="Local HeadRush sample folder", kind="local")

    return None


def execute_transfer(package: TransferPackage, options: TransferOptions, target: TransferTarget) -> TransferResult:
    rig_target_dir = target.root / RIGS_DIR
    ir_target_dir = target.root / IRS_DIR
    setlists_dir = target.root / SETLISTS_DIR

    rig_target_dir.mkdir(parents=True, exist_ok=True)
    ir_target_dir.mkdir(parents=True, exist_ok=True)
    setlists_dir.mkdir(parents=True, exist_ok=True)

    notes: list[str] = []
    imported_rigs: list[ImportedRig] = []
    copied_irs: list[Path] = []
    ir_name_map: dict[str, str] = {}

    pack_prefix = _derive_pack_prefix(package.source_name)
    source_ir_items = _deduplicate_ir_items(package.irs)
    duplicate_ir_names = _duplicate_stems(package.irs)
    if duplicate_ir_names:
        notes.append(
            "Skipped duplicate source IR names: " + ", ".join(sorted(duplicate_ir_names, key=str.casefold))
        )

    existing_ir_stems = _collect_existing_ir_stems(ir_target_dir)
    referenced_ir_names = _collect_referenced_ir_names(package.rigs)

    if options.copy_irs:
        for stem, ir_item in source_ir_items.items():
            existing_path = existing_ir_stems.get(stem.casefold())
            if existing_path is not None:
                ir_name_map[stem] = existing_path.stem
                continue

            target_stem = _sanitize_filename(stem) or "IR"
            target_name = f"{target_stem}{ir_item.extension}"
            target_path = ir_target_dir / target_name
            target_path.write_bytes(_read_source_item_bytes(ir_item))
            copied_irs.append(target_path)
            existing_ir_stems[target_stem.casefold()] = target_path
            ir_name_map[stem] = target_path.stem

    missing_ir_names = sorted(
        {
            ir_name
            for ir_name in referenced_ir_names
            if ir_name.casefold() not in existing_ir_stems and ir_name not in ir_name_map
        },
        key=str.casefold,
    )
    if missing_ir_names:
        raise ValueError(
            "Missing IR files for imported rigs: "
            + ", ".join(missing_ir_names)
            + ". Enable IR copy or add these IRs to HeadRush/Impulse Responses/USER first."
        )

    if options.copy_rigs:
        existing_rig_names = {path.name.casefold() for path in rig_target_dir.iterdir() if path.is_file()}
        used_numbers = _collect_used_rig_numbers(rig_target_dir)
        next_order = _next_rig_order(rig_target_dir)
        for rig_item in package.rigs:
            rig_number = _next_available_rig_number(used_numbers)
            original_rig_name = _extract_rig_name(_read_source_item_text(rig_item), rig_item.stem)
            rig_display_name = _build_rig_display_name(rig_number, pack_prefix, original_rig_name)
            target_filename = _allocate_unique_filename(rig_display_name, PRESET_FILE_SUFFIX, existing_rig_names)
            target_path = rig_target_dir / target_filename
            new_rig_id = str(uuid.uuid4())
            updated_bytes = _rewrite_rig_file(
                source_text=_read_source_item_text(rig_item),
                new_rig_name=Path(target_filename).stem,
                new_rig_id=new_rig_id,
                new_order=next_order,
                ir_name_map=ir_name_map,
            )
            target_path.write_bytes(updated_bytes)
            imported_rigs.append(ImportedRig(rig_id=new_rig_id, rig_name=Path(target_filename).stem, target_path=target_path))
            existing_rig_names.add(target_filename.casefold())
            used_numbers.add(rig_number)
            next_order += 1

    setlist_path: Path | None = None
    if options.create_setlist and imported_rigs:
        setlist_filename = _allocate_unique_filename(_sanitize_filename(options.setlist_name) or package.source_name, ".setlist", {path.name.casefold() for path in setlists_dir.iterdir() if path.is_file()})
        setlist_path = setlists_dir / setlist_filename
        setlist_path.write_text(
            json.dumps(
                {
                    "author": "UserName",
                    "created_at": int(time.time()),
                    "id": str(uuid.uuid4()),
                    "readonly": False,
                    "rig_names": [rig.rig_name for rig in imported_rigs],
                    "rigs": [rig.rig_id for rig in imported_rigs],
                    "version": "1.0.0",
                }
            ),
            encoding="utf-8",
        )

    if target.kind == "local":
        notes.append("Saved to the local HeadRush sample folder.")
    else:
        notes.append("Press Sync on the MX-5 after ejecting the device to finish the transfer.")

    return TransferResult(
        target=target,
        copied_rigs=tuple(imported_rigs),
        copied_irs=tuple(copied_irs),
        setlist_path=setlist_path,
        notes=tuple(notes),
    )


def eject_transfer_target(target: TransferTarget) -> tuple[bool, str]:
    if target.kind != "device":
        return False, "Eject is only available for a connected HeadRush device."

    try:
        if target.root.exists() and target.root.anchor.startswith("/"):
            if Path("/usr/sbin/diskutil").exists():
                result = subprocess.run(
                    ["/usr/sbin/diskutil", "unmount", str(target.root)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    return True, "HeadRush device unmounted. Press Sync on the MX-5."
                return False, (result.stderr or result.stdout or "Unable to eject the HeadRush device.").strip()

            result = subprocess.run(["umount", str(target.root)], capture_output=True, text=True, check=False)
            if result.returncode == 0:
                return True, "HeadRush device unmounted. Press Sync on the MX-5."
            return False, (result.stderr or result.stdout or "Unable to eject the HeadRush device.").strip()

        drive = str(target.root)
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$drive='{drive.rstrip('\\\\')}'; $shell=New-Object -ComObject Shell.Application; $shell.Namespace(17).ParseName($drive).InvokeVerb('Eject')",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return True, "HeadRush device eject command sent. Press Sync on the MX-5."
        return False, (result.stderr or result.stdout or "Unable to eject the HeadRush device.").strip()
    except OSError as exc:
        return False, str(exc)


def _build_folder_package(folder_path: Path) -> TransferPackage:
    rigs, irs = _scan_folder_source_items(folder_path)
    return TransferPackage(
        source_name=folder_path.name,
        rigs=rigs,
        irs=irs,
        origin_label=str(folder_path),
    )


def _build_archive_package(archive_path: Path, archive_dir: PurePosixPath) -> TransferPackage:
    rigs, irs = _scan_archive_source_items(archive_path, archive_dir)
    source_name = archive_dir.name if archive_dir != ARCHIVE_ROOT and archive_dir.name else archive_path.stem
    origin_suffix = archive_dir.as_posix() if archive_dir != ARCHIVE_ROOT else "/"
    return TransferPackage(
        source_name=source_name,
        rigs=rigs,
        irs=irs,
        origin_label=f"{archive_path}::{origin_suffix}",
    )


def _scan_folder_source_items(folder_path: Path) -> tuple[tuple[SourceItem, ...], tuple[SourceItem, ...]]:
    rigs: list[SourceItem] = []
    irs: list[SourceItem] = []
    for path in sorted(folder_path.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in {PRESET_FILE_SUFFIX, IR_FILE_SUFFIX}:
            continue
        relative_path = PurePosixPath(path.relative_to(folder_path).as_posix())
        item = SourceItem(
            relative_path=relative_path,
            stem=path.stem,
            extension=path.suffix,
            filesystem_path=path,
        )
        if suffix == PRESET_FILE_SUFFIX:
            rigs.append(item)
        else:
            irs.append(item)
    return tuple(rigs), tuple(irs)


def _scan_archive_source_items(
    archive_path: Path,
    archive_dir: PurePosixPath,
) -> tuple[tuple[SourceItem, ...], tuple[SourceItem, ...]]:
    rigs: list[SourceItem] = []
    irs: list[SourceItem] = []
    for member in read_archive_members(archive_path):
        if member.is_directory:
            continue
        if archive_dir != ARCHIVE_ROOT:
            try:
                relative_path = member.path.relative_to(archive_dir)
            except ValueError:
                continue
        else:
            relative_path = member.path
        suffix = relative_path.suffix.lower()
        if suffix not in {PRESET_FILE_SUFFIX, IR_FILE_SUFFIX}:
            continue
        item = SourceItem(
            relative_path=relative_path,
            stem=relative_path.stem,
            extension=relative_path.suffix,
            archive_path=archive_path,
            archive_member_path=member.path,
        )
        if suffix == PRESET_FILE_SUFFIX:
            rigs.append(item)
        else:
            irs.append(item)
    rigs.sort(key=lambda item: item.relative_path.as_posix().casefold())
    irs.sort(key=lambda item: item.relative_path.as_posix().casefold())
    return tuple(rigs), tuple(irs)


def _looks_like_headrush_root(root: Path) -> bool:
    return root.is_dir() and (root / RIGS_DIR).is_dir() and (root / SETLISTS_DIR).is_dir()


def _derive_pack_prefix(source_name: str) -> str:
    cleaned = _sanitize_filename(source_name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > 28:
        cleaned = cleaned[:28].rstrip()
    return cleaned or "Imported Pack"


def _sanitize_filename(name: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub(" ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:MAX_NAME_LENGTH].rstrip()


def _collect_used_rig_numbers(rigs_dir: Path) -> set[int]:
    used_numbers: set[int] = set()
    for path in rigs_dir.iterdir():
        if not path.is_file():
            continue
        match = re.match(r"^(\d+)\s+", path.stem)
        if match:
            used_numbers.add(int(match.group(1)))
    return used_numbers


def _next_available_rig_number(used_numbers: set[int]) -> int:
    number = RIG_NUMBER_START
    while number in used_numbers:
        number += 1
    return number


def _next_rig_order(rigs_dir: Path) -> int:
    max_order = 0
    for path in rigs_dir.iterdir():
        if not path.is_file() or path.suffix.lower() != PRESET_FILE_SUFFIX:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        order = payload.get("order")
        if isinstance(order, int):
            max_order = max(max_order, order)
    return max_order + 1


def _build_rig_display_name(number: int, pack_prefix: str, original_name: str) -> str:
    base_name = _sanitize_filename(original_name) or "Preset"
    prefix = pack_prefix
    if prefix.casefold() in base_name.casefold():
        candidate = f"{number:03d} {base_name}"
    else:
        candidate = f"{number:03d} {prefix} - {base_name}"
    return candidate[:MAX_NAME_LENGTH].rstrip()


def _allocate_unique_filename(base_name: str, suffix: str, existing_names: set[str]) -> str:
    candidate = f"{base_name}{suffix}"
    counter = 1
    while candidate.casefold() in existing_names:
        candidate = f"{base_name}_{counter}{suffix}"
        counter += 1
    return candidate


def _extract_rig_name(source_text: str, fallback_name: str) -> str:
    try:
        payload = json.loads(source_text)
        content = json.loads(payload["content"])
        return (
            content["data"]["Patch"]["children"]["Rig"]["children"]["PresetName"]["string"]
            or fallback_name
        )
    except (KeyError, TypeError, json.JSONDecodeError):
        return fallback_name


def _rewrite_rig_file(
    source_text: str,
    new_rig_name: str,
    new_rig_id: str,
    new_order: int,
    ir_name_map: dict[str, str],
) -> bytes:
    payload = json.loads(source_text)
    payload["id"] = new_rig_id
    payload["created_at"] = int(time.time())
    payload["readonly"] = False
    payload["order"] = new_order

    content_text = payload["content"]
    content = json.loads(content_text)
    try:
        content["data"]["Patch"]["children"]["Rig"]["children"]["PresetName"]["string"] = new_rig_name
    except KeyError:
        pass

    updated_content_text = json.dumps(content, separators=(",", ":"))
    if ir_name_map:
        updated_content_text = _replace_ir_references(updated_content_text, ir_name_map)
    payload["content"] = updated_content_text
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _replace_ir_references(content_text: str, ir_name_map: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        original_name = match.group("name")
        replacement = ir_name_map.get(original_name)
        if replacement is None:
            return match.group(0)
        return f"[directory]([{match.group('directory')}])[name]({replacement})"

    return IR_REFERENCE_PATTERN.sub(_replace, content_text)


def _duplicate_stems(items: tuple[SourceItem, ...]) -> set[str]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.stem] = counts.get(item.stem, 0) + 1
    return {stem for stem, count in counts.items() if count > 1}


def _deduplicate_ir_items(items: tuple[SourceItem, ...]) -> dict[str, SourceItem]:
    deduped: dict[str, SourceItem] = {}
    for item in items:
        deduped.setdefault(item.stem, item)
    return deduped


def _collect_existing_ir_stems(ir_target_dir: Path) -> dict[str, Path]:
    stems: dict[str, Path] = {}
    for path in sorted(ir_target_dir.rglob(f"*{IR_FILE_SUFFIX}"), key=lambda item: item.as_posix().casefold()):
        stems.setdefault(path.stem.casefold(), path)
    return stems


def _collect_referenced_ir_names(rigs: tuple[SourceItem, ...]) -> set[str]:
    referenced: set[str] = set()
    for rig_item in rigs:
        referenced.update(_extract_referenced_ir_names(_read_source_item_text(rig_item)))
    return referenced


def _extract_referenced_ir_names(source_text: str) -> set[str]:
    try:
        payload = json.loads(source_text)
        content_text = payload["content"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return set()

    referenced: set[str] = set()
    for match in IR_REFERENCE_PATTERN.finditer(content_text):
        if match.group("directory").casefold() == "user":
            referenced.add(match.group("name"))
    return referenced


def _read_source_item_bytes(item: SourceItem) -> bytes:
    if item.filesystem_path is not None:
        return item.filesystem_path.read_bytes()
    if item.archive_path is None or item.archive_member_path is None:
        raise ValueError("Source item is missing backing storage.")

    if item.archive_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(item.archive_path) as archive:
            return archive.read(item.archive_member_path.as_posix())
    if item.archive_path.suffix.lower() == ".rar":
        with rarfile.RarFile(item.archive_path) as archive:
            return archive.read(item.archive_member_path.as_posix())
    raise ValueError(f"Unsupported archive type: {item.archive_path.suffix}")


def _read_source_item_text(item: SourceItem) -> str:
    return _read_source_item_bytes(item).decode("utf-8")
