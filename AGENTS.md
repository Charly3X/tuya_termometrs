# AI Agent Guide (AGENTS.md)

## Project overview
This repository contains a KDE Plasma 6 widget (plasmoid) that displays data from Tuya devices:
- Up to 3 thermometer/hygrometer sensors (temperature, humidity, battery)
- Optional smart plug (power, voltage, energy)

The widget supports two connection modes:
- **Cloud API mode**: Uses Tuya Cloud API
- **Local Network mode**: Direct communication with devices on local network via `tinytuya.Device`

Cloud mode has two backends:
- **Smart Life sharing (default)**: `tuya_sharing_api.py`, QR login against the app
  account. No IoT Core subscription required.
- **IoT Core (legacy fallback)**: `tinytuya.Cloud` in `tuya_client.py`. Breaks with
  error `28841002` when the developer trial expires.

The widget UI is implemented in QML and periodically calls a local Python script.

## Repository layout
- `metadata.json`
  - Plasma plugin metadata (plasmoid id: `org.kde.plasma.tuya`).
- `contents/ui/main.qml`
  - Main widget UI.
  - Creates timers and executes the Python script via Plasma's `executable` data engine.
  - Passes connection mode parameter to the script.
- `contents/ui/configGeneral.qml`
  - Widget configuration UI (connection mode, intervals, background opacity).
- `contents/config/config.qml`
  - Plasma config model (wires config pages).
- `contents/config/main.xml`
  - KConfig schema; includes default `scriptPath` and `connectionMode`.
- `tuya_client.py`
  - Main Python entrypoint that fetches Tuya data and prints JSON (also writes `data.json`).
  - Supports both cloud and local modes based on command-line argument.
  - Cloud mode: token caching, device name caching, batch status calls.
  - Local mode: delegates to `tuya_local.py` for direct device communication.
- `tuya_local.py`
  - Local network communication module.
  - Handles direct device connections using `tinytuya.Device`.
  - Parses DPS (Data Point) values for thermometers and smart plugs.
- `tuya_sharing_api.py`
  - Smart Life cloud backend built on `tuya-device-sharing-sdk`.
  - Session storage (`sharing_token.json`), automatic token refresh, QR login helpers.
  - Queries `/v1.0/m/life/ha/devices/detail` directly (one request per refresh)
    instead of `Manager.update_device_cache()`, which would issue four extra
    enrichment calls per device.
  - Falls back to `/v1.0/m/life/ha/{id}/shadow/properties` per device when the
    detail endpoint returns `status: null`.
  - Forces IPv4 via `prefer_ipv4()`; see the troubleshooting notes.
- `tuya_auth.py`
  - One-time QR login CLI. Prints the QR as ASCII, then lists the account's
    devices with their ids and status codes.
- `get_local_keys.py`
  - Helper script to retrieve local keys from cloud API for local mode setup.
- `install.sh`
  - Creates `venv/` (if missing), installs `tinytuya`, copies widget files to the user's plasmoid directory, and rewrites paths in `main.xml`.
- `config.json.example`
  - Template for credentials, device ids, and local network configuration.
- `requirements.txt`
  - Python dependency list (`tinytuya`).
- `list_devices.py`, `test_region.py`, `test_statistics.py`
  - Helper scripts for manual testing and diagnostics.

## How the widget works (data flow)

### Cloud API Mode
- QML `Timer`s in `contents/ui/main.qml` trigger `updateThermometers()` and `updateSocket()`.
- The widget runs a command through `Plasma5Support.DataSource` (engine: `executable`).
- The command executes `tuya_client.py` with arguments:
  - First arg: `thermometers`, `socket`, or `all`
  - Second arg: `cloud` (connection mode)
  - Optional: `--log` flag
- `tuya_client.py`:
  - Loads `config.json` from the repository directory.
  - Creates a `tinytuya.Cloud` client.
  - Uses cached token (`token_cache.json`) when possible.
  - Uses cached device name map (`device_names_cache.json`) for up to 24h.
  - Fetches statuses in batch via `/v1.0/iot-03/devices/status?device_ids=...`.
  - Falls back to shadow properties `/v2.0/cloud/thing/{device_id}/shadow/properties` if needed.
  - Prints JSON to stdout (QML parses it) and writes the same JSON to `data.json`.

### Local Network Mode
- Same QML timer triggers, but passes `local` as second argument.
- `tuya_client.py` delegates to `tuya_local.py` functions.
- `tuya_local.py`:
  - Reads `local_devices` and `local_socket` from `config.json`.
  - Creates `tinytuya.Device` instances for each device.
  - Connects directly to devices on local network using IP, local_key, and protocol version.
  - Parses DPS (Data Point System) values to extract temperature, humidity, battery, power, etc.
  - Returns same JSON format as cloud mode for compatibility.

## Local configuration and secrets
- **DO NOT commit** `config.json`.
- `config.json` contains:
  - Cloud mode: `region`, `client_id`, `client_secret`, `device_id`, `devices`, optional `socket`
  - Local mode: `local_devices` array (id, name, ip, local_key, version), optional `local_socket` object

Files created/used at runtime in the repo directory:
- `sharing_token.json` (Smart Life backend; contains access/refresh tokens, mode 600)
- `token_cache.json` (IoT Core backend only)
- `device_names_cache.json` (cloud mode only)
- `api_calls.log`
- `data.json`

## Installation / running
Recommended install (per `README.md`):
- `cp config.json.example config.json` and fill credentials
- For local mode: run `./venv/bin/python3 get_local_keys.py` to get local keys
- `chmod +x install.sh`
- `./install.sh`

Manual Python testing (expected to be run from the repo root):
- Cloud mode:
  - `./venv/bin/python3 tuya_client.py`
  - `./venv/bin/python3 tuya_client.py thermometers cloud`
  - `./venv/bin/python3 tuya_client.py socket cloud`
- Local mode:
  - `./venv/bin/python3 tuya_client.py thermometers local`
  - `./venv/bin/python3 tuya_client.py socket local`
  - `./venv/bin/python3 tuya_client.py all local`

## Important invariant: script path rewriting
The QML currently contains hardcoded paths (author's machine) and `contents/config/main.xml` has a default `scriptPath`.

`install.sh` rewrites `/home/charoyan/projects/tuya` to the current repo directory inside the *installed* plasmoid's `main.xml` using `sed`.

When modifying execution logic, keep in mind:
- The plasmoid runs in the user's environment, not in this git checkout.
- Any hardcoded paths should be avoided or made configurable.

## Suggested agent workflow for changes
When an AI agent is asked to implement a change:
- First locate whether it's a UI/UX change (QML) or data/API change (Python).
- Prefer minimal diffs.
- Avoid introducing new dependencies unless necessary.
- Keep stdout JSON stable unless you also update the QML parser.
- For local mode changes, modify `tuya_local.py`.
- For cloud mode changes, modify `tuya_client.py`.

## Common change locations
- **UI layout / styling**: `contents/ui/main.qml`
- **Config UI**: `contents/ui/configGeneral.qml` and `contents/config/main.xml`
- **Cloud API / caching / parsing**: `tuya_client.py`
- **Local network communication**: `tuya_local.py`
- **Install behavior**: `install.sh`

## Troubleshooting notes
- **Battery-powered Wi-Fi thermometers cannot be polled locally at all.** They sleep
  and only push to the cloud: they do not answer on port 6668 and do not respond to
  `tinytuya.deviceScan` broadcasts. Any "make the sensors work locally" request is
  impossible with this hardware — only the smart plugs are locally reachable.
- **Do not remove `prefer_ipv4()` from `tuya_sharing_api.py`.** `apigw.tuyaeu.com`
  publishes AAAA records that are black-holed on this connection. urllib3 tries the
  resolved addresses sequentially with the SDK's 60s timeout, so every cloud request
  took ~180s (3 dead IPv6 addresses) before falling back to IPv4, which answers in
  ~0.2s. curl hides this because it does Happy Eyeballs; Python does not.
- **"Спальня" (`bfac5ed4`) and "Детская" (`bf19c499`) always return `status: null`**
  from the device detail endpoint. Their product definition in Tuya's catalog is
  wrong: category `tdq` (circuit breaker) instead of `wsdcg`, and
  `/v1.1/m/life/{id}/specifications` is empty. Their values are only available via
  the shadow endpoint. "Зал" (`bfb3a145`, category `wsdcg`) works normally. The
  IoT Core path has the same split, which is why it also has a shadow fallback.
- Cloud error `28841002 IoT Core service subscription has expired` means the IoT Core
  trial ended. Either extend it on iot.tuya.com (Cloud → Cloud Services → IoT Core →
  My Subscriptions → Extend Trial Period) or use the Smart Life backend, which does
  not depend on it.
- Battery-powered sensors may not provide real-time status in cloud mode; shadow properties are used as fallback.
- Local mode provides faster updates but requires devices to be on the same network.
- If device names are stale (cloud mode), delete `device_names_cache.json` (cache is 24h).
- If token issues occur (cloud mode), delete `token_cache.json`.
- For local mode issues, verify IP addresses and local keys are correct.
- DPS codes may vary by device model; check `tuya_local.py` parsing logic if values are incorrect.

## Non-goals / guardrails for agents
- Git commit messages must always be written in English.
- Do not add or modify user secrets.
- Do not change the plasmoid id (`org.kde.plasma.tuya`) unless explicitly required.
- Avoid changes that assume a specific absolute path on the user's system.
- Keep JSON output format consistent between cloud and local modes.

## Current device configuration
**Only 2 smart plugs are used.** Do not add more.
- Детская (power strip) — local_sockets index 0, color green (#10b981)
- Холодильник (refrigerator) — local_sockets index 1, color amber (#f59e0b)
- "Чайник" and "Комп" were intentionally removed by user request.

Socket layout: 1 column × 2 rows (vertical stacking via ColumnLayout).

After modifying QML, copy it to the installed widget directory and restart plasmashell:
```
cp contents/ui/main.qml ~/.local/share/plasma/plasmoids/org.kde.plasma.tuya/contents/ui/main.qml
killall plasmashell && sleep 2 && nohup plasmashell &
```
