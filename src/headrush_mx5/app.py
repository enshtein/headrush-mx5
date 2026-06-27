from __future__ import annotations

import json
from pathlib import Path

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from headrush_mx5.browser import (
    BrowserEntry,
    STATE_FILE_NAME,
    default_start_path,
    list_browser_entries,
    list_root_entries,
)


class BrowserListItem(ListItem):
    def __init__(self, entry: BrowserEntry) -> None:
        icon = "[DIR]" if entry.is_directory else "[ARC]"
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
        width: 36;
        min-width: 28;
        padding: 1 2;
        border: solid #4f6f52;
        background: #111111;
    }

    #browser {
        width: 1fr;
        padding: 1 2;
        border: solid #4f6f52;
    }

    #path {
        padding-bottom: 1;
        text-style: bold;
    }

    #status {
        padding-top: 1;
        color: #c8d7c9;
    }

    #help {
        color: #c8d7c9;
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
        Binding("space", "select_current_directory", "Select Folder"),
        Binding("r", "refresh", "Refresh"),
    ]

    current_path = reactive(None)

    def __init__(self, start_path: Path | None) -> None:
        super().__init__()
        self.current_path = start_path.resolve() if start_path is not None else None
        self.selected_path: Path | None = None

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
            with Vertical(id="browser"):
                yield Static("", id="path")
                yield ListView(id="entries")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_entries()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, BrowserListItem):
            return
        entry = item.entry
        if entry.is_directory:
            self.current_path = entry.path
            self._refresh_entries()
            return
        self._finish_with_selection(entry.path)

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            return
        if event.key == "left":
            self.action_go_parent()

    def action_go_parent(self) -> None:
        if self.current_path is None:
            return
        parent = self.current_path.parent
        if parent == self.current_path:
            self.current_path = None
        else:
            self.current_path = parent
        self._refresh_entries()

    def action_go_roots(self) -> None:
        self.current_path = None
        self._refresh_entries()

    def action_select_current_directory(self) -> None:
        if self.current_path is None:
            return
        self._finish_with_selection(self.current_path)

    def action_refresh(self) -> None:
        self._refresh_entries()

    def _refresh_entries(self) -> None:
        path_widget = self.query_one("#path", Static)
        status_widget = self.query_one("#status", Static)
        list_view = self.query_one("#entries", ListView)

        if self.current_path is None:
            path_widget.update("Computer")
            entries = list_root_entries()
        else:
            path_widget.update(str(self.current_path))
            try:
                entries = list_browser_entries(self.current_path)
            except PermissionError:
                status_widget.update("Permission denied for this folder.")
                return
            except FileNotFoundError:
                status_widget.update("Folder no longer exists.")
                return

        list_view.clear()
        for entry in entries:
            list_view.append(BrowserListItem(entry))

        dirs = sum(1 for entry in entries if entry.is_directory)
        archives = len(entries) - dirs
        if self.current_path is None:
            status_widget.update(f"Disks: {dirs}\n\nEnter: open disk\nHome: return here")
        else:
            status_widget.update(
                f"Folders: {dirs}\nArchives: {archives}\n\nEnter: open/select archive\nSpace: select current folder"
            )

        if entries:
            list_view.index = 0

    def _finish_with_selection(self, path: Path) -> None:
        self.selected_path = path.resolve()
        self.exit(self.selected_path)


def save_state(project_root: Path, selected_path: Path) -> None:
    state_path = project_root / STATE_FILE_NAME
    payload = {"selected_source": str(selected_path)}
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    project_root = Path.cwd()
    start_path = default_start_path()
    app = PresetSourceBrowser(start_path=start_path)
    selected_path = app.run()

    if selected_path is None:
        print("No preset source selected.")
        return 0

    save_state(project_root, selected_path)
    print(f"Selected preset source: {selected_path}")
    return 0
