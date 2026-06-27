from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, ListItem, ListView, Static

from headrush_mx5.browser import (
    FolderAnalysis,
    BrowserEntry,
    ARCHIVE_ROOT,
    analyze_headrush_folder,
    STATE_FILE_NAME,
    default_start_path,
    find_headrush_mount,
    inspect_archive,
    is_browsable_archive,
    list_browser_entries,
    list_root_entries,
)
from headrush_mx5.transfer import (
    TransferOptions,
    TransferPackage,
    TransferResult,
    TransferTarget,
    build_transfer_package,
    eject_transfer_target,
    execute_transfer,
    resolve_transfer_target,
)


@dataclass(frozen=True)
class TransferModalResult:
    options: TransferOptions


class TransferOptionsModal(ModalScreen[TransferModalResult | None]):
    CSS = """
    TransferOptionsModal {
        align: center middle;
    }

    #transfer_modal {
        width: 72;
        max-width: 90%;
        height: auto;
        padding: 1 2;
        border: round #4f6f52;
        background: #111111;
    }

    .modal_title {
        text-style: bold;
        padding-bottom: 1;
    }

    .modal_text {
        color: #c8d7c9;
        padding-bottom: 1;
    }

    Checkbox {
        margin: 0 0 1 0;
    }

    Input {
        margin: 0 0 1 0;
    }

    #transfer_buttons {
        align: right middle;
        margin-top: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, package: TransferPackage, target: TransferTarget) -> None:
        super().__init__()
        self.package = package
        self.target = target

    def compose(self) -> ComposeResult:
        default_setlist_name = self.package.source_name
        with Container(id="transfer_modal"):
            yield Static("Transfer to HeadRush MX-5", classes="modal_title")
            yield Static(f"Source: {self.package.origin_label}", classes="modal_text")
            yield Static(f"Target: {self.target.label}", classes="modal_text")
            yield Checkbox("Copy preset RIG files to HeadRush", value=True, id="copy_rigs")
            yield Checkbox("Copy IR files to HeadRush", value=True, id="copy_irs")
            yield Checkbox("Create a setlist for imported presets", value=True, id="create_setlist")
            yield Input(value=default_setlist_name, placeholder="Setlist name", id="setlist_name")
            yield Static(
                "RIGs will be saved into HeadRush/Rigs with safe numbering and pack-prefixed names.\n"
                "IRs will be saved into HeadRush/Impulse Responses/USER.",
                classes="modal_text",
            )
            with Horizontal(id="transfer_buttons"):
                yield Button("Cancel", id="cancel_transfer")
                yield Button("Save to HeadRush MX-5", id="confirm_transfer", variant="success")

    def on_mount(self) -> None:
        self._sync_form_state()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "copy_rigs":
            self._sync_form_state()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel_transfer":
            self.dismiss(None)
            return
        if event.button.id != "confirm_transfer":
            return

        copy_rigs = self.query_one("#copy_rigs", Checkbox).value
        copy_irs = self.query_one("#copy_irs", Checkbox).value
        create_setlist = self.query_one("#create_setlist", Checkbox).value and copy_rigs
        setlist_name = self.query_one("#setlist_name", Input).value.strip() or self.package.source_name

        if not copy_rigs and not copy_irs:
            self.query_one(".modal_text", Static).update("Select at least one transfer option.")
            return

        self.dismiss(
            TransferModalResult(
                options=TransferOptions(
                    copy_rigs=copy_rigs,
                    copy_irs=copy_irs,
                    create_setlist=create_setlist,
                    setlist_name=setlist_name,
                )
            )
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _sync_form_state(self) -> None:
        copy_rigs = self.query_one("#copy_rigs", Checkbox).value
        create_setlist = self.query_one("#create_setlist", Checkbox)
        setlist_name = self.query_one("#setlist_name", Input)
        create_setlist.disabled = not copy_rigs
        if not copy_rigs:
            create_setlist.value = False
        setlist_name.disabled = not (copy_rigs and create_setlist.value)


class TransferCompleteModal(ModalScreen[None]):
    CSS = """
    TransferCompleteModal {
        align: center middle;
    }

    #transfer_complete {
        width: 72;
        max-width: 90%;
        height: auto;
        padding: 1 2;
        border: round #4f6f52;
        background: #111111;
    }

    .modal_title {
        text-style: bold;
        padding-bottom: 1;
    }

    .modal_text {
        color: #c8d7c9;
        padding-bottom: 1;
    }

    #complete_buttons {
        align: right middle;
        margin-top: 1;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, result: TransferResult) -> None:
        super().__init__()
        self.result = result
        self.summary_lines = [
            f"Target: {self.result.target.label}",
            f"Copied RIGs: {len(self.result.copied_rigs)}",
            f"Copied IRs: {len(self.result.copied_irs)}",
        ]
        if self.result.setlist_path is not None:
            self.summary_lines.append(f"Setlist: {self.result.setlist_path.name}")
        self.summary_lines.extend(self.result.notes)

    def compose(self) -> ComposeResult:
        with Container(id="transfer_complete"):
            yield Static("Transfer Complete", classes="modal_title")
            yield Static("\n".join(self.summary_lines), classes="modal_text", id="transfer_complete_text")
            if self.result.target.kind == "device":
                yield Static("Do not forget to press Sync on the MX-5 to finish the synchronization.", classes="modal_text")
            with Horizontal(id="complete_buttons"):
                if self.result.target.kind == "device":
                    yield Button("Eject the HeadRush device on your computer", id="eject_device", variant="warning")
                yield Button("Close", id="close_complete", variant="success")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close_complete":
            self.dismiss(None)
            return
        if event.button.id != "eject_device":
            return

        success, message = eject_transfer_target(self.result.target)
        text_widget = self.query_one("#transfer_complete_text", Static)
        self.summary_lines.append(message)
        text_widget.update("\n".join(self.summary_lines))
        event.button.disabled = success

    def action_close(self) -> None:
        self.dismiss(None)


class BrowserListItem(ListItem):
    def __init__(self, entry: BrowserEntry) -> None:
        if entry.is_parent_link:
            icon = "[UP]"
        elif entry.is_directory:
            icon = "[DIR]"
        else:
            suffix = entry.path.suffix.lower()
            if suffix == ".rig":
                icon = "[RIG]"
            elif suffix == ".wav":
                icon = "[IR]"
            elif suffix in {".zip", ".rar", ".7z"}:
                icon = "[ARC]"
            else:
                icon = "[FILE]"
        label = Label(f"{icon} {entry.label}")
        super().__init__(label)
        self.entry = entry


class PresetSourceBrowser(App[Path | None]):
    TITLE = "Headrush MX-5 Preset Loader"
    SUB_TITLE = "Choose a preset pack folder or archive"
    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        height: 1fr;
    }

    #sidebar {
        width: 52;
        min-width: 38;
        padding: 1 2;
        border: solid #4f6f52;
        background: #111111;
    }

    #browser {
        width: 1fr;
        padding: 1 2;
        border: solid #4f6f52;
    }

    #browser_header {
        height: 1;
        margin-bottom: 1;
    }

    #path {
        width: 1fr;
        text-style: bold;
    }

    #footer_status_bar {
        height: 1;
        background: #0d0f3f;
    }

    #footer_status_spacer {
        width: 1fr;
    }

    #device_footer_status {
        height: 1;
        padding: 0 1;
        background: #0d0f3f;
        color: #96a397;
        text-style: bold;
    }

    #device_footer_status.-connected {
        color: #79e59a;
    }

    #device_footer_status.-disconnected {
        color: #96a397;
    }
    #status {
        padding-top: 1;
        color: #c8d7c9;
    }

    .analysis_title {
        padding-top: 1;
        text-style: bold;
    }

    .analysis_pane {
        height: 1fr;
        border: round #355e3b;
        padding: 0 1;
        background: #0f140f;
    }

    .analysis_body {
        padding: 1 0;
        color: #d7e1d7;
    }

    #help {
        color: #c8d7c9;
    }

    #open_transfer {
        margin-top: 1;
        width: 100%;
    }

    ListView {
        height: 1fr;
        border: round #355e3b;
        background: #0f140f;
    }

    ListItem {
        padding: 0 1;
    }

    ListItem.--highlight {
        background: #214f2c;
        color: #f5fff5;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("backspace", "go_parent", "Up"),
        Binding("home", "go_roots", "Disks"),
        Binding("space", "select_source", "Select Source"),
        Binding("r", "refresh", "Refresh"),
    ]

    current_path = reactive(None)

    def __init__(
        self,
        start_path: Path | None,
        start_archive_path: Path | None = None,
        start_archive_dir: PurePosixPath = ARCHIVE_ROOT,
    ) -> None:
        super().__init__()
        self.current_path = start_path.resolve() if start_path is not None else None
        self.current_archive_path: Path | None = start_archive_path.resolve() if start_archive_path is not None else None
        self.current_archive_dir = start_archive_dir
        self.selected_path: Path | None = None
        self.headrush_mount: Path | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("Source", id="title")
                yield Static(
                    "Browse the file system and choose a preset pack folder or archive.",
                    id="help",
                )
                yield Static("", id="status")
                yield Static("", id="rigs_title", classes="analysis_title")
                with VerticalScroll(id="rigs_pane", classes="analysis_pane"):
                    yield Static("", id="rigs_analysis", classes="analysis_body")
                yield Static("", id="irs_title", classes="analysis_title")
                with VerticalScroll(id="irs_pane", classes="analysis_pane"):
                    yield Static("", id="irs_analysis", classes="analysis_body")
                yield Button("Transfer to HeadRush MX-5", id="open_transfer", variant="success")
            with Vertical(id="browser"):
                with Horizontal(id="browser_header"):
                    yield Static("", id="path")
                yield ListView(id="entries")
        with Horizontal(id="footer_status_bar"):
            yield Static("", id="footer_status_spacer")
            yield Static("", id="device_footer_status", classes="-disconnected")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_headrush_status()
        self.set_interval(2.0, self._poll_system_state)
        self._refresh_entries()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, BrowserListItem):
            return
        entry = item.entry
        if entry.is_parent_link:
            self.action_go_parent()
            return
        if self.current_archive_path is not None:
            if entry.is_directory:
                self.current_archive_dir = PurePosixPath(entry.path.as_posix())
                self._refresh_entries()
            return
        if entry.is_directory:
            self.current_path = entry.path
            self._refresh_entries()
            return
        if is_browsable_archive(entry.path):
            self._enter_archive(entry.path)
            return
        self._finish_with_selection(entry.path)

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            return
        if event.key == "left":
            self.action_go_parent()

    def action_go_parent(self) -> None:
        if self.current_archive_path is not None:
            if self.current_archive_dir == ARCHIVE_ROOT:
                self.current_archive_path = None
                self.current_archive_dir = ARCHIVE_ROOT
            else:
                self.current_archive_dir = self.current_archive_dir.parent
            self._refresh_entries()
            return
        if self.current_path is None:
            return
        parent = self.current_path.parent
        if parent == self.current_path:
            self.current_path = None
        else:
            self.current_path = parent
        self._refresh_entries()

    def action_go_roots(self) -> None:
        self.current_archive_path = None
        self.current_archive_dir = ARCHIVE_ROOT
        self.current_path = None
        self._refresh_entries()

    def action_select_source(self) -> None:
        if self.current_archive_path is not None:
            self._finish_with_selection(self.current_archive_path)
            return

        highlighted_entry = self._get_highlighted_entry()
        if highlighted_entry is not None and not highlighted_entry.is_directory and not highlighted_entry.is_parent_link:
            if highlighted_entry.path.suffix.lower() in {".zip", ".rar", ".7z"}:
                self._finish_with_selection(highlighted_entry.path)
                return

        if self.current_path is not None:
            self._finish_with_selection(self.current_path)

    def action_refresh(self) -> None:
        self._refresh_headrush_status()
        self._refresh_entries()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "open_transfer":
            return

        try:
            package = build_transfer_package(self.current_path, self.current_archive_path, self.current_archive_dir)
        except ValueError as exc:
            self.notify(str(exc), severity="warning")
            return

        if not package.rigs and not package.irs:
            self.notify("The current source does not contain any .rig or .wav files.", severity="warning")
            return

        target = resolve_transfer_target(Path.cwd(), self.headrush_mount)
        if target is None:
            self.notify("No HeadRush target folder was found.", severity="error")
            return

        self.push_screen(
            TransferOptionsModal(package, target),
            callback=lambda result: self._handle_transfer_options(result, package, target),
        )

    def _refresh_entries(self) -> None:
        path_widget = self.query_one("#path", Static)
        status_widget = self.query_one("#status", Static)
        rigs_title_widget = self.query_one("#rigs_title", Static)
        rigs_analysis_widget = self.query_one("#rigs_analysis", Static)
        irs_title_widget = self.query_one("#irs_title", Static)
        irs_analysis_widget = self.query_one("#irs_analysis", Static)
        list_view = self.query_one("#entries", ListView)

        if self.current_archive_path is not None:
            path_widget.update(self._format_archive_location())
            try:
                entries, analysis = inspect_archive(self.current_archive_path, self.current_archive_dir)
            except Exception as exc:
                status_widget.update("Unable to open this archive.")
                self._update_analysis_widgets(
                    rigs_title_widget,
                    rigs_analysis_widget,
                    irs_title_widget,
                    irs_analysis_widget,
                    FolderAnalysis(presets=(), irs=(), note=f"Archive error: {exc}"),
                )
                return
            self._update_analysis_widgets(
                rigs_title_widget, rigs_analysis_widget, irs_title_widget, irs_analysis_widget, analysis
            )
        elif self.current_path is None:
            path_widget.update("Computer")
            entries = list_root_entries()
            self._update_analysis_widgets(
                rigs_title_widget,
                rigs_analysis_widget,
                irs_title_widget,
                irs_analysis_widget,
                FolderAnalysis(
                    presets=(),
                    irs=(),
                    note="Open a folder or archive to inspect HeadRush presets and impulse responses.",
                ),
            )
        else:
            path_widget.update(str(self.current_path))
            try:
                entries = list_browser_entries(self.current_path)
            except PermissionError:
                status_widget.update("Permission denied for this folder.")
                self._update_analysis_widgets(
                    rigs_title_widget,
                    rigs_analysis_widget,
                    irs_title_widget,
                    irs_analysis_widget,
                    FolderAnalysis(presets=(), irs=(), note="Unable to inspect this folder because access is denied."),
                )
                return
            except FileNotFoundError:
                status_widget.update("Folder no longer exists.")
                self._update_analysis_widgets(
                    rigs_title_widget,
                    rigs_analysis_widget,
                    irs_title_widget,
                    irs_analysis_widget,
                    FolderAnalysis(presets=(), irs=(), note="Unable to inspect this folder because it no longer exists."),
                )
                return
            self._update_analysis_widgets(
                rigs_title_widget,
                rigs_analysis_widget,
                irs_title_widget,
                irs_analysis_widget,
                analyze_headrush_folder(self.current_path),
            )

        list_view.clear()
        for entry in entries:
            list_view.append(BrowserListItem(entry))

        dirs = sum(1 for entry in entries if entry.is_directory and not entry.is_parent_link)
        archives = sum(1 for entry in entries if not entry.is_directory)
        if self.current_archive_path is not None:
            status_widget.update(
                f"Folders: {dirs}\nFiles: {archives}\n\nEnter: open folder\nSpace: select current archive\n../ or Backspace: go up"
            )
        elif self.current_path is None:
            status_widget.update(f"Disks: {dirs}\n\nEnter: open disk\nHome: return here")
        else:
            status_widget.update(
                f"Folders: {dirs}\nArchives: {archives}\n\nEnter: open folder/archive\nSpace: select current folder or highlighted archive\n../ or Backspace: go up"
            )

        if entries:
            list_view.index = 0

    def _update_analysis_widgets(
        self,
        rigs_title_widget: Static,
        rigs_analysis_widget: Static,
        irs_title_widget: Static,
        irs_analysis_widget: Static,
        analysis: FolderAnalysis,
    ) -> None:
        rigs_title_widget.update(f"Rigs (Presets): {len(analysis.presets)}")
        irs_title_widget.update(f"IRs (Impulse Responses): {len(analysis.irs)}")
        rigs_analysis_widget.update(self._format_analysis_list(analysis.presets, "No presets detected.", analysis.note))
        irs_analysis_widget.update(self._format_analysis_list(analysis.irs, "No impulse responses detected.", analysis.note))

    def _format_analysis_list(self, items: tuple[str, ...], empty_message: str, note: str | None) -> Text:
        if items:
            lines = [f"- {item}" for item in items]
            if note:
                lines.extend(["", note])
            return Text("\n".join(lines))

        if note:
            return Text(note)

        return Text(empty_message)

    def _finish_with_selection(self, path: Path) -> None:
        self.selected_path = path.resolve()
        self.exit(self.selected_path)

    def _enter_archive(self, archive_path: Path) -> None:
        self.current_archive_path = archive_path
        self.current_archive_dir = ARCHIVE_ROOT
        self._refresh_entries()

    def _format_archive_location(self) -> str:
        if self.current_archive_dir == ARCHIVE_ROOT:
            return f"{self.current_archive_path}::/"
        return f"{self.current_archive_path}::/{self.current_archive_dir.as_posix()}"

    def _get_highlighted_entry(self) -> BrowserEntry | None:
        list_view = self.query_one("#entries", ListView)
        item = list_view.highlighted_child
        if isinstance(item, BrowserListItem):
            return item.entry
        return None

    def _poll_system_state(self) -> None:
        previous_mount = self.headrush_mount
        self._refresh_headrush_status()
        if previous_mount != self.headrush_mount and self.current_path is None and self.current_archive_path is None:
            self._refresh_entries()

    def _refresh_headrush_status(self) -> None:
        self.headrush_mount = find_headrush_mount()
        device_status = self.query_one("#device_footer_status", Static)

        if self.headrush_mount is not None:
            device_status.remove_class("-disconnected")
            device_status.add_class("-connected")
            device_status.update("● HeadRush MX-5 USB Transfer Sync")
        else:
            device_status.remove_class("-connected")
            device_status.add_class("-disconnected")
            device_status.update("○ HeadRush MX-5 USB Transfer Sync")

    def _handle_transfer_options(
        self,
        result: TransferModalResult | None,
        package: TransferPackage,
        target: TransferTarget,
    ) -> None:
        if result is None:
            return

        try:
            transfer_result = execute_transfer(package, result.options, target)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        except Exception as exc:
            self.notify(f"Transfer failed: {exc}", severity="error")
            return

        self.push_screen(TransferCompleteModal(transfer_result))


def save_state(project_root: Path, selected_path: Path) -> None:
    state_path = project_root / STATE_FILE_NAME
    payload = {"selected_source": str(selected_path)}
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def save_browser_state(
    project_root: Path,
    selected_path: Path | None,
    browser_path: Path | None,
    archive_path: Path | None,
    archive_dir: PurePosixPath,
) -> None:
    state_path = project_root / STATE_FILE_NAME
    payload: dict[str, str] = {}

    if selected_path is not None:
        payload["selected_source"] = str(selected_path)
    if browser_path is not None:
        payload["browser_path"] = str(browser_path)
    if archive_path is not None:
        payload["archive_path"] = str(archive_path)
        payload["archive_dir"] = archive_dir.as_posix()

    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_browser_state(project_root: Path) -> tuple[Path | None, Path | None, PurePosixPath]:
    state_path = project_root / STATE_FILE_NAME
    if not state_path.is_file():
        return default_start_path(), None, ARCHIVE_ROOT

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_start_path(), None, ARCHIVE_ROOT

    browser_path_value = payload.get("browser_path")
    archive_path_value = payload.get("archive_path")
    archive_dir_value = payload.get("archive_dir")

    browser_path = Path(browser_path_value).expanduser() if isinstance(browser_path_value, str) else None
    archive_path = Path(archive_path_value).expanduser() if isinstance(archive_path_value, str) else None
    archive_dir = PurePosixPath(archive_dir_value) if isinstance(archive_dir_value, str) and archive_dir_value else ARCHIVE_ROOT

    if archive_path is not None and archive_path.is_file():
        fallback_browser_path = browser_path if browser_path is not None and browser_path.is_dir() else archive_path.parent
        return fallback_browser_path.resolve(), archive_path.resolve(), archive_dir

    if browser_path is not None and browser_path.is_dir():
        return browser_path.resolve(), None, ARCHIVE_ROOT

    return default_start_path(), None, ARCHIVE_ROOT


def main() -> int:
    project_root = Path.cwd()
    start_path, start_archive_path, start_archive_dir = load_browser_state(project_root)
    app = PresetSourceBrowser(
        start_path=start_path,
        start_archive_path=start_archive_path,
        start_archive_dir=start_archive_dir,
    )
    selected_path = app.run()

    save_browser_state(
        project_root=project_root,
        selected_path=selected_path,
        browser_path=app.current_path,
        archive_path=app.current_archive_path,
        archive_dir=app.current_archive_dir,
    )

    if selected_path is None:
        print("No preset source selected.")
        return 0

    print(f"Selected preset source: {selected_path}")
    return 0
