# headrush-mx5

Initial console application for working with HeadRush MX-5 preset packs.

The current version covers the first step: it opens a fullscreen terminal file browser and lets the user choose:

- a disk or system volume for navigation;
- a preset pack folder;
- a preset pack archive (`.zip`, `.rar`, `.7z`).

## Quick Start

### macOS

Open `run-macos.command`.

### Linux

```bash
./run-linux.sh
```

### Windows

Open `run-windows.bat`.

The launch scripts create a local `.venv`, install dependencies from `requirements.txt`, and start the application.

## Controls

- `↑` / `↓` - move through the list
- `Enter` - open a folder or select an archive
- `Space` - select the current folder
- `Backspace` - go up one level
- `Home` - return to the disks/roots screen
- `r` - refresh the current view
- `q` - quit

## Current Behavior

- On startup, the app shows the filesystem roots screen.
- On macOS and Linux, the screen includes the system root `/` and detected mounted volumes.
- On Windows, the screen shows available drives.
- After selection, the chosen path is saved to `.headrush-mx5-state.json`.
- After exit, the selected path is printed to the terminal.

## Repository Structure

- `src/headrush_mx5/` - application code
- `tests/` - tests for the browser support logic
- `run-macos.command`, `run-linux.sh`, `run-windows.bat` - startup scripts
