# AI Agent Guide (AGENTS.md)

## Project overview
This repository contains a KDE Plasma 6 widget (plasmoid) that displays data from Tuya Cloud devices:
- Up to 3 thermometer/hygrometer sensors (temperature, humidity, battery)
- Optional smart plug (power, voltage, energy)

The widget UI is implemented in QML and periodically calls a local Python script that talks to the Tuya Cloud API via `tinytuya`.

## Repository layout
- `metadata.json`
  - Plasma plugin metadata (plasmoid id: `org.kde.plasma.tuya`).
- `contents/ui/main.qml`
  - Main widget UI.
  - Creates timers and executes the Python script via Plasma’s `executable` data engine.
- `contents/ui/configGeneral.qml`
  - Widget configuration UI (intervals and background opacity).
- `contents/config/config.qml`
  - Plasma config model (wires config pages).
- `contents/config/main.xml`
  - KConfig schema; includes default `scriptPath`.
- `tuya_client.py`
  - Main Python entrypoint that fetches Tuya data and prints JSON (also writes `data.json`).
  - Implements token caching, device name caching, and batch status calls.
- `install.sh`
  - Creates `venv/` (if missing), installs `tinytuya`, copies widget files to the user’s plasmoid directory, and rewrites paths in `main.xml`.
- `config.json.example`
  - Template for local credentials and device ids.
- `requirements.txt`
  - Python dependency list (`tinytuya`).
- `list_devices.py`, `test_region.py`, `test_statistics.py`
  - Helper scripts for manual testing and diagnostics.

## How the widget works (data flow)
- QML `Timer`s in `contents/ui/main.qml` trigger `updateThermometers()` and `updateSocket()`.
- The widget runs a command through `Plasma5Support.DataSource` (engine: `executable`).
- The command executes `tuya_client.py` with argument:
  - `thermometers` (only sensors)
  - `socket` (only plug)
  - no argument / `all` (everything)
- `tuya_client.py`:
  - Loads `config.json` from the repository directory.
  - Creates a `tinytuya.Cloud` client.
  - Uses cached token (`token_cache.json`) when possible.
  - Uses cached device name map (`device_names_cache.json`) for up to 24h.
  - Fetches statuses in batch via `/v1.0/iot-03/devices/status?device_ids=...`.
  - Falls back to shadow properties `/v2.0/cloud/thing/{device_id}/shadow/properties` if needed.
  - Prints JSON to stdout (QML parses it) and writes the same JSON to `data.json`.

## Local configuration and secrets
- **DO NOT commit** `config.json`.
- `config.json` contains:
  - `region`, `client_id`, `client_secret`, `device_id`
  - `devices`: list of 3 sensor ids
  - optional `socket`: plug id

Files created/used at runtime in the repo directory:
- `token_cache.json`
- `device_names_cache.json`
- `api_calls.log`
- `data.json`

## Installation / running
Recommended install (per `README.md`):
- `cp config.json.example config.json` and fill credentials
- `chmod +x install.sh`
- `./install.sh`

Manual Python testing (expected to be run from the repo root):
- `./venv/bin/python3 tuya_client.py`
- `./venv/bin/python3 tuya_client.py thermometers`
- `./venv/bin/python3 tuya_client.py socket`

## Important invariant: script path rewriting
The QML currently contains hardcoded paths (author’s machine) and `contents/config/main.xml` has a default `scriptPath`.

`install.sh` rewrites `/home/charoyan/projects/tuya` to the current repo directory inside the *installed* plasmoid’s `main.xml` using `sed`.

When modifying execution logic, keep in mind:
- The plasmoid runs in the user’s environment, not in this git checkout.
- Any hardcoded paths should be avoided or made configurable.

## Suggested agent workflow for changes
When an AI agent is asked to implement a change:
- First locate whether it’s a UI/UX change (QML) or data/API change (Python).
- Prefer minimal diffs.
- Avoid introducing new dependencies unless necessary.
- Keep stdout JSON stable unless you also update the QML parser.

## Common change locations
- **UI layout / styling**: `contents/ui/main.qml`
- **Config UI**: `contents/ui/configGeneral.qml` and `contents/config/main.xml`
- **Tuya API / caching / parsing**: `tuya_client.py`
- **Install behavior**: `install.sh`

## Troubleshooting notes
- Battery-powered sensors may not provide real-time status; shadow properties are used as fallback.
- If device names are stale, delete `device_names_cache.json` (cache is 24h).
- If token issues occur, delete `token_cache.json`.

## Non-goals / guardrails for agents
- Do not add or modify user secrets.
- Do not change the plasmoid id (`org.kde.plasma.tuya`) unless explicitly required.
- Avoid changes that assume a specific absolute path on the user’s system.
