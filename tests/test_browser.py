from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from headrush_mx5.backup import backup_headrush_device
from headrush_mx5.browser import (
    ARCHIVE_ROOT,
    ArchiveMember,
    BrowserEntry,
    analyze_headrush_archive,
    analyze_headrush_folder,
    default_start_path,
    find_headrush_mount,
    inspect_archive,
    is_browsable_archive,
    list_archive_entries,
    list_browser_entries,
    list_root_entries,
)
from headrush_mx5.app import load_browser_state, save_browser_state
from headrush_mx5.transfer import (
    TransferOptions,
    TransferTarget,
    build_transfer_package,
    eject_transfer_target,
    execute_transfer,
    resolve_transfer_target,
    undo_transfer_operations,
)


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

    def test_find_headrush_mount_uses_posix_volume_name(self) -> None:
        fake_entries = [
            BrowserEntry(path=Path("/"), is_directory=True, display_name="System /"),
            BrowserEntry(path=Path("/Volumes/HeadRush"), is_directory=True, display_name="/Volumes/HeadRush"),
        ]

        with patch("headrush_mx5.browser.os.name", "posix"):
            with patch("headrush_mx5.browser._posix_roots", return_value=fake_entries):
                mount = find_headrush_mount()

        self.assertEqual(mount, Path("/Volumes/HeadRush"))

    def test_find_headrush_mount_uses_windows_volume_label(self) -> None:
        fake_entries = [
            BrowserEntry(path=Path("D:\\"), is_directory=True, display_name="D:\\"),
            BrowserEntry(path=Path("E:\\"), is_directory=True, display_name="E:\\"),
        ]

        with patch("headrush_mx5.browser.os.name", "nt"):
            with patch("headrush_mx5.browser._windows_roots", return_value=fake_entries):
                with patch("headrush_mx5.browser._get_windows_volume_label", side_effect=["Data", "HeadRush"]):
                    mount = find_headrush_mount()

        self.assertEqual(str(mount), "E:\\")

    def test_analyze_headrush_folder_collects_presets_and_irs_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Rigs").mkdir()
            (root / "IRs").mkdir()
            (root / "__MACOSX").mkdir()
            (root / "Rigs" / "Lead.rig").write_text("rig", encoding="utf-8")
            (root / "Rigs" / "._Lead.rig").write_text("appledouble", encoding="utf-8")
            (root / "Rigs" / "Rhythm.RIG").write_text("rig", encoding="utf-8")
            (root / "IRs" / "Cab.wav").write_text("wav", encoding="utf-8")
            (root / "IRs" / "._Cab.wav").write_text("appledouble", encoding="utf-8")
            (root / "__MACOSX" / "Ghost.wav").write_text("wav", encoding="utf-8")
            (root / "notes.txt").write_text("ignore", encoding="utf-8")

            analysis = analyze_headrush_folder(root)

            self.assertEqual(analysis.presets, ("Rigs/Lead.rig", "Rigs/Rhythm.RIG"))
            self.assertEqual(analysis.irs, ("IRs/Cab.wav",))
            self.assertTrue(analysis.has_headrush_content)

    def test_analyze_headrush_folder_handles_non_headrush_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.txt").write_text("ignore", encoding="utf-8")

            analysis = analyze_headrush_folder(root)

            self.assertEqual(analysis.presets, ())
            self.assertEqual(analysis.irs, ())
            self.assertFalse(analysis.has_headrush_content)
            self.assertIsNone(analysis.note)

    def test_is_browsable_archive_only_accepts_zip_and_rar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            zip_path = root / "Pack.zip"
            rar_path = root / "Pack.rar"
            seven_zip_path = root / "Pack.7z"
            zip_path.write_text("zip", encoding="utf-8")
            rar_path.write_text("rar", encoding="utf-8")
            seven_zip_path.write_text("7z", encoding="utf-8")

            self.assertTrue(is_browsable_archive(zip_path))
            self.assertTrue(is_browsable_archive(rar_path))
            self.assertFalse(is_browsable_archive(seven_zip_path))

    def test_list_archive_entries_adds_parent_and_children(self) -> None:
        members = (
            ArchiveMember(path=PurePosixPath("Rigs/Lead.rig"), is_directory=False),
            ArchiveMember(path=PurePosixPath("IRs/Cab.wav"), is_directory=False),
            ArchiveMember(path=PurePosixPath("README.rtf"), is_directory=False),
        )

        entries = list_archive_entries(ARCHIVE_ROOT, members)

        self.assertEqual([entry.label for entry in entries], ["../", "IRs", "Rigs", "README.rtf"])
        self.assertTrue(entries[0].is_parent_link)

    def test_analyze_headrush_archive_collects_rigs_and_wavs(self) -> None:
        members = (
            ArchiveMember(path=PurePosixPath("Rigs/Lead.rig"), is_directory=False),
            ArchiveMember(path=PurePosixPath("IRs/Cab.wav"), is_directory=False),
            ArchiveMember(path=PurePosixPath("README.rtf"), is_directory=False),
        )

        analysis = analyze_headrush_archive(members)

        self.assertEqual(analysis.presets, ("Rigs/Lead.rig",))
        self.assertEqual(analysis.irs, ("IRs/Cab.wav",))
        self.assertTrue(analysis.has_headrush_content)

    def test_inspect_archive_reads_zip_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "Pack.zip"

            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("Rigs/Lead.rig", "rig")
                archive.writestr("IRs/Cab.wav", "wav")
                archive.writestr("Docs/README.rtf", "doc")

            entries, analysis = inspect_archive(archive_path, ARCHIVE_ROOT)

            self.assertEqual([entry.label for entry in entries], ["../", "Docs", "IRs", "Rigs"])
            self.assertEqual(analysis.presets, ("Rigs/Lead.rig",))
            self.assertEqual(analysis.irs, ("IRs/Cab.wav",))

    def test_analyze_headrush_folder_skips_large_generic_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "deep" / "generic" / "folder"
            target.mkdir(parents=True)

            for index in range(45):
                (target / f"child-{index}").mkdir()

            analysis = analyze_headrush_folder(target)

            self.assertFalse(analysis.has_headrush_content)
            self.assertEqual(analysis.note, "Open a more specific folder to inspect HeadRush files.")

    def test_save_and_load_browser_state_restores_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            browser_path = project_root / "Presets"
            browser_path.mkdir()

            save_browser_state(
                project_root=project_root,
                selected_path=None,
                browser_path=browser_path,
                archive_path=None,
                archive_dir=ARCHIVE_ROOT,
            )

            restored_path, restored_archive_path, restored_archive_dir = load_browser_state(project_root)

            self.assertEqual(restored_path, browser_path.resolve())
            self.assertIsNone(restored_archive_path)
            self.assertEqual(restored_archive_dir, ARCHIVE_ROOT)

    def test_save_and_load_browser_state_restores_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            browser_path = project_root / "Downloads"
            browser_path.mkdir()
            archive_path = browser_path / "Pack.zip"
            archive_path.write_text("zip", encoding="utf-8")

            save_browser_state(
                project_root=project_root,
                selected_path=archive_path,
                browser_path=browser_path,
                archive_path=archive_path,
                archive_dir=PurePosixPath("Rigs"),
            )

            restored_path, restored_archive_path, restored_archive_dir = load_browser_state(project_root)

            self.assertEqual(restored_path, browser_path.resolve())
            self.assertEqual(restored_archive_path, archive_path.resolve())
            self.assertEqual(restored_archive_dir, PurePosixPath("Rigs"))

    def test_load_browser_state_falls_back_when_saved_paths_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            state_path = project_root / ".headrush-mx5-state.json"
            state_path.write_text(
                '{"browser_path": "/missing/folder", "archive_path": "/missing/archive.zip", "archive_dir": "Rigs"}',
                encoding="utf-8",
            )

            restored_path, restored_archive_path, restored_archive_dir = load_browser_state(project_root)

            self.assertIsNone(restored_path)
            self.assertIsNone(restored_archive_path)
            self.assertEqual(restored_archive_dir, ARCHIVE_ROOT)

    def test_resolve_transfer_target_prefers_connected_device(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            local_headrush = project_root / "HeadRush"
            (local_headrush / "Rigs").mkdir(parents=True)
            (local_headrush / "Setlists").mkdir()
            connected = project_root / "MountedHeadRush"
            (connected / "Rigs").mkdir(parents=True)
            (connected / "Setlists").mkdir()

            target = resolve_transfer_target(project_root, connected)

            self.assertEqual(target.root, connected)
            self.assertEqual(target.kind, "device")

    def test_execute_transfer_copies_rigs_irs_and_setlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Bon Jovi Pack"
            (source / "Rigs").mkdir(parents=True)
            (source / "IRs").mkdir()
            rig_path = source / "Rigs" / "Lead.rig"
            ir_path = source / "IRs" / "Cab.wav"
            ir_path.write_text("wav-data", encoding="utf-8")
            rig_path.write_text(
                json.dumps(
                    {
                        "author": "UserName",
                        "color": 9,
                        "content": json.dumps(
                            {
                                "data": {
                                    "Patch": {
                                        "children": {
                                            "Rig": {"children": {"PresetName": {"string": "Lead"}}},
                                            "IR": {"children": {"IR": {"string": "[directory]([USER])[name](Cab)"}}},
                                        }
                                    }
                                }
                            }
                        ),
                        "created_at": 1,
                        "id": "old-id",
                        "order": 1,
                        "prog_num": -1,
                        "readonly": False,
                    }
                ),
                encoding="utf-8",
            )

            target_root = root / "HeadRush"
            (target_root / "Rigs").mkdir(parents=True)
            (target_root / "Setlists").mkdir()
            (target_root / "Impulse Responses" / "USER").mkdir(parents=True)
            (target_root / "Rigs" / "270 +WDW-HARMONY.rig").write_text('{"order": 270}', encoding="utf-8")

            package = build_transfer_package(source, None, ARCHIVE_ROOT)
            result = execute_transfer(
                package,
                TransferOptions(copy_rigs=True, copy_irs=True, create_setlist=True, setlist_name="Bon Jovi"),
                resolve_transfer_target(root, None),
            )

            self.assertEqual(len(result.copied_rigs), 1)
            self.assertEqual(len(result.copied_irs), 1)
            self.assertIsNotNone(result.setlist_path)
            copied_rig = result.copied_rigs[0]
            copied_rig_payload = json.loads(copied_rig.target_path.read_text(encoding="utf-8"))
            self.assertEqual(copied_rig_payload["id"], "old-id")
            self.assertIn("300 ", copied_rig.target_path.name)
            self.assertIn("BON JOVI", copied_rig.target_path.name)
            self.assertEqual(result.copied_irs[0].name, "Cab.wav")
            self.assertIn("[directory]([USER])[name](Cab)", copied_rig_payload["content"])
            self.assertIn("[name](Cab)", copied_rig_payload["content"])

            setlist_payload = json.loads(result.setlist_path.read_text(encoding="utf-8"))
            self.assertEqual(result.setlist_path.stem, "BON JOVI")
            self.assertEqual(setlist_payload["rig_names"], [copied_rig.rig_name])
            self.assertEqual(setlist_payload["rigs"], [copied_rig.rig_id])

    def test_build_transfer_package_ignores_appledouble_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Pack"
            (source / "Rigs").mkdir(parents=True)
            (source / "IRs").mkdir()
            (source / "Rigs" / "Lead.rig").write_text("{}", encoding="utf-8")
            (source / "Rigs" / "._Lead.rig").write_text("appledouble", encoding="utf-8")
            (source / "IRs" / "Cab.wav").write_text("wav", encoding="utf-8")
            (source / "IRs" / "._Cab.wav").write_text("appledouble", encoding="utf-8")

            package = build_transfer_package(source, None, ARCHIVE_ROOT)

            self.assertEqual(tuple(item.relative_path.as_posix() for item in package.rigs), ("Rigs/Lead.rig",))
            self.assertEqual(tuple(item.relative_path.as_posix() for item in package.irs), ("IRs/Cab.wav",))

    def test_execute_transfer_skips_setlist_prefix_when_rig_names_share_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "ACDC Pack"
            (source / "Rigs").mkdir(parents=True)
            (source / "IRs").mkdir()
            for name in ("CT-ACDC CLEAN.rig", "CT-ACDC LEAD.rig"):
                (source / "Rigs" / name).write_text(
                    json.dumps(
                        {
                            "author": "UserName",
                            "color": 9,
                            "content": json.dumps(
                                {
                                    "data": {
                                        "Patch": {
                                            "children": {
                                                "Rig": {"children": {"PresetName": {"string": Path(name).stem}}},
                                            }
                                        }
                                    }
                                }
                            ),
                            "created_at": 1,
                            "id": f"id-{name}",
                            "order": 1,
                            "prog_num": -1,
                            "readonly": False,
                        }
                    ),
                    encoding="utf-8",
                )

            target_root = root / "HeadRush"
            (target_root / "Rigs").mkdir(parents=True)
            (target_root / "Setlists").mkdir()
            (target_root / "Impulse Responses" / "USER").mkdir(parents=True)

            package = build_transfer_package(source, None, ARCHIVE_ROOT)
            result = execute_transfer(
                package,
                TransferOptions(copy_rigs=True, copy_irs=False, create_setlist=True, setlist_name="ACDC"),
                resolve_transfer_target(root, None),
            )

            copied_names = [rig.target_path.stem for rig in result.copied_rigs]
            self.assertEqual(copied_names, ["300 CT-ACDC CLEAN", "301 CT-ACDC LEAD"])

    def test_execute_transfer_appends_rigs_to_existing_setlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Bon Jovi Pack"
            (source / "Rigs").mkdir(parents=True)
            (source / "Rigs" / "Lead.rig").write_text(
                json.dumps(
                    {
                        "author": "UserName",
                        "color": 9,
                        "content": json.dumps(
                            {
                                "data": {
                                    "Patch": {
                                        "children": {
                                            "Rig": {"children": {"PresetName": {"string": "Lead"}}},
                                        }
                                    }
                                }
                            }
                        ),
                        "created_at": 1,
                        "id": "new-rig-id",
                        "order": 1,
                        "prog_num": -1,
                        "readonly": False,
                    }
                ),
                encoding="utf-8",
            )

            target_root = root / "HeadRush"
            (target_root / "Rigs").mkdir(parents=True)
            (target_root / "Setlists").mkdir()
            (target_root / "Impulse Responses" / "USER").mkdir(parents=True)
            existing_setlist_path = target_root / "Setlists" / "BON JOVI.setlist"
            existing_setlist_path.write_text(
                json.dumps(
                    {
                        "author": "Existing User",
                        "created_at": 123,
                        "id": "existing-setlist-id",
                        "readonly": False,
                        "rig_names": ["300 EXISTING"],
                        "rigs": ["existing-rig-id"],
                        "version": "1.0.0",
                    }
                ),
                encoding="utf-8",
            )

            package = build_transfer_package(source, None, ARCHIVE_ROOT)
            result = execute_transfer(
                package,
                TransferOptions(copy_rigs=True, copy_irs=False, create_setlist=True, setlist_name="Bon Jovi"),
                resolve_transfer_target(root, None),
            )

            self.assertEqual(result.setlist_path, existing_setlist_path)
            setlist_payload = json.loads(existing_setlist_path.read_text(encoding="utf-8"))
            self.assertEqual(setlist_payload["author"], "Existing User")
            self.assertEqual(setlist_payload["created_at"], 123)
            self.assertEqual(setlist_payload["id"], "existing-setlist-id")
            self.assertEqual(
                setlist_payload["rig_names"],
                ["300 EXISTING", result.copied_rigs[0].rig_name],
            )
            self.assertEqual(
                setlist_payload["rigs"],
                ["existing-rig-id", result.copied_rigs[0].rig_id],
            )

    def test_undo_transfer_removes_created_files_and_new_setlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Bon Jovi Pack"
            (source / "Rigs").mkdir(parents=True)
            (source / "IRs").mkdir()
            (source / "Rigs" / "Lead.rig").write_text(
                json.dumps(
                    {
                        "author": "UserName",
                        "color": 9,
                        "content": json.dumps(
                            {
                                "data": {
                                    "Patch": {
                                        "children": {
                                            "Rig": {"children": {"PresetName": {"string": "Lead"}}},
                                            "IR": {"children": {"IR": {"string": "[directory]([USER])[name](Cab)"}}},
                                        }
                                    }
                                }
                            }
                        ),
                        "created_at": 1,
                        "id": "new-rig-id",
                        "order": 1,
                        "prog_num": -1,
                        "readonly": False,
                    }
                ),
                encoding="utf-8",
            )
            (source / "IRs" / "Cab.wav").write_text("wav-data", encoding="utf-8")

            target_root = root / "HeadRush"
            (target_root / "Rigs").mkdir(parents=True)
            (target_root / "Setlists").mkdir()
            (target_root / "Impulse Responses" / "USER").mkdir(parents=True)

            package = build_transfer_package(source, None, ARCHIVE_ROOT)
            result = execute_transfer(
                package,
                TransferOptions(copy_rigs=True, copy_irs=True, create_setlist=True, setlist_name="Bon Jovi"),
                resolve_transfer_target(root, None),
            )

            self.assertIsNotNone(result.undo_operation)
            undo_transfer_operations((result.undo_operation,))

            self.assertFalse(result.copied_rigs[0].target_path.exists())
            self.assertFalse(result.copied_irs[0].exists())
            self.assertIsNotNone(result.setlist_path)
            self.assertFalse(result.setlist_path.exists())

    def test_undo_transfer_restores_existing_setlist_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Bon Jovi Pack"
            (source / "Rigs").mkdir(parents=True)
            (source / "Rigs" / "Lead.rig").write_text(
                json.dumps(
                    {
                        "author": "UserName",
                        "color": 9,
                        "content": json.dumps(
                            {
                                "data": {
                                    "Patch": {
                                        "children": {
                                            "Rig": {"children": {"PresetName": {"string": "Lead"}}},
                                        }
                                    }
                                }
                            }
                        ),
                        "created_at": 1,
                        "id": "new-rig-id",
                        "order": 1,
                        "prog_num": -1,
                        "readonly": False,
                    }
                ),
                encoding="utf-8",
            )

            target_root = root / "HeadRush"
            (target_root / "Rigs").mkdir(parents=True)
            (target_root / "Setlists").mkdir()
            (target_root / "Impulse Responses" / "USER").mkdir(parents=True)
            existing_setlist_path = target_root / "Setlists" / "BON JOVI.setlist"
            original_payload = {
                "author": "Existing User",
                "created_at": 123,
                "id": "existing-setlist-id",
                "readonly": False,
                "rig_names": ["300 EXISTING"],
                "rigs": ["existing-rig-id"],
                "version": "1.0.0",
            }
            existing_setlist_path.write_text(json.dumps(original_payload), encoding="utf-8")

            package = build_transfer_package(source, None, ARCHIVE_ROOT)
            result = execute_transfer(
                package,
                TransferOptions(copy_rigs=True, copy_irs=False, create_setlist=True, setlist_name="Bon Jovi"),
                resolve_transfer_target(root, None),
            )

            self.assertIsNotNone(result.undo_operation)
            undo_transfer_operations((result.undo_operation,))

            restored_payload = json.loads(existing_setlist_path.read_text(encoding="utf-8"))
            self.assertEqual(restored_payload, original_payload)
            self.assertFalse(result.copied_rigs[0].target_path.exists())

    def test_execute_transfer_skips_existing_ir_with_same_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Pack"
            (source / "Rigs").mkdir(parents=True)
            (source / "IRs").mkdir()
            (source / "IRs" / "Cab.wav").write_text("new-wav", encoding="utf-8")
            (source / "Rigs" / "Lead.rig").write_text(
                json.dumps(
                    {
                        "author": "UserName",
                        "color": 9,
                        "content": json.dumps(
                            {
                                "data": {
                                    "Patch": {
                                        "children": {
                                            "Rig": {"children": {"PresetName": {"string": "Lead"}}},
                                            "IR": {"children": {"IR": {"string": "[directory]([USER])[name](Cab)"}}},
                                        }
                                    }
                                }
                            }
                        ),
                        "created_at": 1,
                        "id": "old-id",
                        "order": 1,
                        "prog_num": -1,
                        "readonly": False,
                    }
                ),
                encoding="utf-8",
            )

            target_root = root / "HeadRush"
            (target_root / "Rigs").mkdir(parents=True)
            (target_root / "Setlists").mkdir()
            existing_ir_dir = target_root / "Impulse Responses" / "USER"
            existing_ir_dir.mkdir(parents=True)
            existing_ir = existing_ir_dir / "Cab.wav"
            existing_ir.write_text("existing-wav", encoding="utf-8")

            package = build_transfer_package(source, None, ARCHIVE_ROOT)
            result = execute_transfer(
                package,
                TransferOptions(copy_rigs=True, copy_irs=True, create_setlist=False, setlist_name="Pack"),
                resolve_transfer_target(root, None),
            )

            self.assertEqual(result.copied_irs, ())
            self.assertEqual(existing_ir.read_text(encoding="utf-8"), "existing-wav")

    def test_execute_transfer_rewrites_non_user_ir_reference_to_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Pack"
            (source / "Rigs").mkdir(parents=True)
            (source / "IRs").mkdir()
            (source / "IRs" / "V30 3.wav").write_text("wav-data", encoding="utf-8")
            (source / "Rigs" / "Lead.rig").write_text(
                json.dumps(
                    {
                        "author": "UserName",
                        "color": 9,
                        "content": json.dumps(
                            {
                                "data": {
                                    "Patch": {
                                        "children": {
                                            "Rig": {"children": {"PresetName": {"string": "Lead"}}},
                                            "IR": {"children": {"IR": {"string": "[directory](Cab Liveplayrock)[name](V30 3)"}}},
                                        }
                                    }
                                }
                            }
                        ),
                        "created_at": 1,
                        "id": "old-id",
                        "order": 1,
                        "prog_num": -1,
                        "readonly": False,
                    }
                ),
                encoding="utf-8",
            )

            target_root = root / "HeadRush"
            (target_root / "Rigs").mkdir(parents=True)
            (target_root / "Setlists").mkdir()
            (target_root / "Impulse Responses" / "USER").mkdir(parents=True)

            package = build_transfer_package(source, None, ARCHIVE_ROOT)
            result = execute_transfer(
                package,
                TransferOptions(copy_rigs=True, copy_irs=True, create_setlist=False, setlist_name="Pack"),
                resolve_transfer_target(root, None),
            )

            copied_rig_payload = json.loads(result.copied_rigs[0].target_path.read_text(encoding="utf-8"))
            self.assertIn("[directory]([USER])[name](V30 3)", copied_rig_payload["content"])

    def test_execute_transfer_preserves_named_ir_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Pack"
            (source / "Rigs").mkdir(parents=True)
            (source / "IRs" / "MKBASS212").mkdir(parents=True)
            (source / "IRs" / "MKBASS212" / "MKBASS212_121_BALANCED.wav").write_text("wav-data", encoding="utf-8")
            (source / "Rigs" / "Lead.rig").write_text(
                json.dumps(
                    {
                        "author": "UserName",
                        "color": 9,
                        "content": json.dumps(
                            {
                                "data": {
                                    "Patch": {
                                        "children": {
                                            "Rig": {"children": {"PresetName": {"string": "Lead"}}},
                                            "IR": {
                                                "children": {
                                                    "IR": {
                                                        "string": "[directory](Cab Liveplayrock)[name](MKBASS212_121_BALANCED)"
                                                    }
                                                }
                                            },
                                        }
                                    }
                                }
                            }
                        ),
                        "created_at": 1,
                        "id": "old-id",
                        "order": 1,
                        "prog_num": -1,
                        "readonly": False,
                    }
                ),
                encoding="utf-8",
            )

            target_root = root / "HeadRush"
            (target_root / "Rigs").mkdir(parents=True)
            (target_root / "Setlists").mkdir()
            (target_root / "Impulse Responses" / "USER").mkdir(parents=True)

            package = build_transfer_package(source, None, ARCHIVE_ROOT)
            result = execute_transfer(
                package,
                TransferOptions(copy_rigs=True, copy_irs=True, create_setlist=False, setlist_name="Pack"),
                resolve_transfer_target(root, None),
            )

            copied_rig_payload = json.loads(result.copied_rigs[0].target_path.read_text(encoding="utf-8"))
            self.assertIn("[directory](MKBASS212)[name](MKBASS212_121_BALANCED)", copied_rig_payload["content"])
            self.assertTrue((target_root / "Impulse Responses" / "MKBASS212" / "MKBASS212_121_BALANCED.wav").exists())

    def test_execute_transfer_fails_when_required_ir_is_missing_and_ir_copy_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Pack"
            (source / "Rigs").mkdir(parents=True)
            (source / "IRs").mkdir()
            (source / "IRs" / "Cab.wav").write_text("wav-data", encoding="utf-8")
            (source / "Rigs" / "Lead.rig").write_text(
                json.dumps(
                    {
                        "author": "UserName",
                        "color": 9,
                        "content": json.dumps(
                            {
                                "data": {
                                    "Patch": {
                                        "children": {
                                            "Rig": {"children": {"PresetName": {"string": "Lead"}}},
                                            "IR": {"children": {"IR": {"string": "[directory]([USER])[name](Cab)"}}},
                                        }
                                    }
                                }
                            }
                        ),
                        "created_at": 1,
                        "id": "old-id",
                        "order": 1,
                        "prog_num": -1,
                        "readonly": False,
                    }
                ),
                encoding="utf-8",
            )

            target_root = root / "HeadRush"
            (target_root / "Rigs").mkdir(parents=True)
            (target_root / "Setlists").mkdir()
            (target_root / "Impulse Responses" / "USER").mkdir(parents=True)

            package = build_transfer_package(source, None, ARCHIVE_ROOT)

            with self.assertRaisesRegex(ValueError, "Missing IR files"):
                execute_transfer(
                    package,
                    TransferOptions(copy_rigs=True, copy_irs=False, create_setlist=False, setlist_name="Pack"),
                    resolve_transfer_target(root, None),
                )

    def test_execute_transfer_generates_new_rig_id_only_on_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Pack"
            (source / "Rigs").mkdir(parents=True)
            (source / "Rigs" / "Lead.rig").write_text(
                json.dumps(
                    {
                        "author": "UserName",
                        "color": 9,
                        "content": json.dumps(
                            {
                                "data": {
                                    "Patch": {
                                        "children": {
                                            "Rig": {"children": {"PresetName": {"string": "Lead"}}},
                                        }
                                    }
                                }
                            }
                        ),
                        "created_at": 1,
                        "id": "shared-id",
                        "order": 1,
                        "prog_num": -1,
                        "readonly": False,
                    }
                ),
                encoding="utf-8",
            )

            target_root = root / "HeadRush"
            (target_root / "Rigs").mkdir(parents=True)
            (target_root / "Setlists").mkdir()
            (target_root / "Impulse Responses" / "USER").mkdir(parents=True)
            (target_root / "Rigs" / "270 Existing.rig").write_text(
                json.dumps(
                    {
                        "author": "UserName",
                        "color": 9,
                        "content": json.dumps(
                            {"data": {"Patch": {"children": {"Rig": {"children": {"PresetName": {"string": "Existing"}}}}}}}
                        ),
                        "created_at": 1,
                        "id": "shared-id",
                        "order": 270,
                        "prog_num": -1,
                        "readonly": False,
                    }
                ),
                encoding="utf-8",
            )

            package = build_transfer_package(source, None, ARCHIVE_ROOT)
            result = execute_transfer(
                package,
                TransferOptions(copy_rigs=True, copy_irs=False, create_setlist=True, setlist_name="Pack"),
                resolve_transfer_target(root, None),
            )

            copied_rig = json.loads(result.copied_rigs[0].target_path.read_text(encoding="utf-8"))
            self.assertNotEqual(copied_rig["id"], "shared-id")

    def test_backup_headrush_device_mirrors_connected_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "MountedHeadRush"
            destination = root / "HeadRush"
            (source / "Rigs").mkdir(parents=True)
            (source / "Setlists").mkdir(parents=True)
            (source / "Rigs" / "300 Test.rig").write_text("rig", encoding="utf-8")
            (source / "Rigs" / "._300 Test.rig").write_text("appledouble", encoding="utf-8")
            (source / "Setlists" / "Test.setlist").write_text("setlist", encoding="utf-8")
            (source / "Setlists" / "._Test.setlist").write_text("appledouble", encoding="utf-8")
            destination.mkdir()
            (destination / "stale.txt").write_text("old", encoding="utf-8")

            progress_events: list[tuple[int, int, str]] = []
            copied_files = backup_headrush_device(
                source,
                destination,
                lambda copied, total, relative: progress_events.append((copied, total, relative)),
            )

            self.assertEqual(copied_files, 2)
            self.assertFalse((destination / "stale.txt").exists())
            self.assertEqual((destination / "Rigs" / "300 Test.rig").read_text(encoding="utf-8"), "rig")
            self.assertEqual((destination / "Setlists" / "Test.setlist").read_text(encoding="utf-8"), "setlist")
            self.assertFalse((destination / "Rigs" / "._300 Test.rig").exists())
            self.assertFalse((destination / "Setlists" / "._Test.setlist").exists())
            self.assertEqual(progress_events[-1], (2, 2, "Setlists/Test.setlist"))

    def test_eject_transfer_target_uses_diskutil_eject_on_macos(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "HeadRush"
            root.mkdir()
            target = TransferTarget(root=root, label="Connected HeadRush MX-5 device", kind="device")

            plist_output = (
                b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                b"<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
                b"\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">"
                b"<plist version=\"1.0\"><dict><key>ParentWholeDisk</key><string>disk4</string></dict></plist>"
            )

            with patch("headrush_mx5.transfer.Path.exists", return_value=True):
                with patch("headrush_mx5.transfer.subprocess.run") as mock_run:
                    mock_run.side_effect = [
                        type("Result", (), {"returncode": 0, "stdout": plist_output, "stderr": b""})(),
                        type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
                    ]
                    success, message = eject_transfer_target(target)

            self.assertTrue(success)
            self.assertIn("Press Sync", message)
            self.assertEqual(mock_run.call_args_list[0].args[0], ["/usr/sbin/diskutil", "info", "-plist", str(root)])
            self.assertEqual(mock_run.call_args_list[1].args[0], ["/usr/sbin/diskutil", "eject", "disk4"])


if __name__ == "__main__":
    unittest.main()
