# Nikon Z6 / Z7 / Z6 II / Z7 II Unlock Tools

[中文说明](README.zh.md)

Standalone Windows tools for **HDMI RAW activation** and **full language-list unlocking** on first-generation Nikon Z6/Z7 cameras, plus **experimental HDMI RAW activation** on Z6 II/Z7 II. The two features have separate scripts. There is no combined unlock command or parameter. No firmware image is flashed. Second-generation language unlocking is not included: the project currently has firmware analysis, but no corresponding executable unlock tool.

## Compatibility

| Camera | Firmware | Validation |
| --- | --- | --- |
| Z6 | C 3.80 | Individual RAW and language operations, plus persistence after normal power-off/on, validated on one camera in the source project |
| Z7 | C 3.80 | Experimental; shared KEY tables established by firmware analysis, but the complete write sequence and persistence have not been hardware validated; requires `--experimental-z7` |
| Z6 II / Z7 II | C 1.70 | HDMI RAW only, using `unlock_hdmi_raw_ii.py`; firmware constants and offline simulations checked, no second-generation hardware/persistence/recording validation |

Use the script for your camera generation: each rejects the other generation, other firmware versions and other models. Identical KEY files alone do not establish identical configuration behavior. This release's packaging and command separation were checked offline; it was not used to write a camera during release preparation. External recorder compatibility and recorded video quality are not covered by these checks.

## Contents

- `unlock_hdmi_raw.py`: first-generation HDMI RAW only.
- `unlock_languages.py`: first-generation full language list only; retains the existing region field.
- `unlock_hdmi_raw_ii.py`: experimental Z6 II / Z7 II C 1.70 HDMI RAW only.
- `unlock_core.py`, `unlock_cli.py`, `wpd_transport.py`: shared implementation.
- `keys/HDMIRAW.KEY`, `keys/SRF_FUNC.KEY`: binary camera authorization files, exactly 32 bytes each, shared by the analyzed Z6/Z7 firmware. These are not software license documents or proof of an official Nikon service upgrade.
- `keys/z6ii_z7ii/HDMIARAW.KEY`, `keys/z6ii_z7ii/SRF_FUNC.KEY`: second-generation files, 32 bytes each. **Both differ from the first-generation files; do not mix them.**
- `requirements.txt`, `test_*.py`, `SHA256SUMS.txt`: dependency pin, offline tests, release checksums.

## Setup

Use Windows and Python **3.11 or newer for Z6 II/Z7 II** (first-generation tools also support Python 3.10). Open a terminal in this folder:

```powershell
python -m pip install -r requirements.txt
```

For first-generation HDMI RAW, copy **`keys/HDMIRAW.KEY` and `keys/SRF_FUNC.KEY`** to the camera memory card's root directory. For Z6 II/Z7 II, use only the two files inside **`keys/z6ii_z7ii/`** instead. Preserve their uppercase names and binary contents; do not copy just the containing folder or rename them to `.txt`. Merely placing the files on the card does not activate RAW. The first-generation language-only operation does not use the KEY-dependent RAW commands and does not require these files.

Insert the card, use a sufficiently charged battery, turn the camera on and connect it by USB. Close applications using the camera. The tools use Windows WPD; no replacement USB driver is needed. Keep USB and power connected until the command finishes.

## Z6 / Z7: HDMI RAW only

Preview (offline), then apply:

```powershell
python unlock_hdmi_raw.py unlock
python unlock_hdmi_raw.py unlock --execute
```

Disconnect USB, power the camera off normally and back on, inspect the HDMI RAW menu, reconnect USB, then verify:

```powershell
python unlock_hdmi_raw.py verify
```

This verification checks RAW only; the language list may remain restricted. Keep both KEY files on the card for this verification.

## Z6 / Z7: full language list only

```powershell
python unlock_languages.py unlock
python unlock_languages.py unlock --execute
```

Disconnect USB, power off normally and back on, inspect the language menu, reconnect USB, then verify:

```powershell
python unlock_languages.py verify
```

This operation does not activate RAW. It exposes the firmware's existing language list, including Simplified Chinese; it does not install translations or change the selected menu language automatically.

For experimental Z7 use, append `--experimental-z7` to each camera operation, for example:

```powershell
python unlock_languages.py unlock --execute --experimental-z7
```

## Z6 II / Z7 II: experimental HDMI RAW only

Requires **C 1.70**, Python 3.11+, and the dedicated second-generation KEY files. The filename is **HDMIARAW.KEY**, with an extra `A`, and `SRF_FUNC.KEY` also has different contents. Copy both files from `keys/z6ii_z7ii/` to the camera card root. The firmware uses its internal `A:\` path, not a PC drive letter; the dual-card slot mapping has not been hardware confirmed. If `0x200F` occurs, resolve the file placement before proceeding.

```powershell
python unlock_hdmi_raw_ii.py unlock
python unlock_hdmi_raw_ii.py status
python unlock_hdmi_raw_ii.py unlock --execute
```

`unlock` without `--execute` is offline. `status` reads DeviceInfo and the normal-mode authorization gate only, without requiring KEY files; it does not establish that both RAW states or recording work. The unlock operation checks model/version, backs up the configuration and validates both card KEY files before either activation command. It then activates RAW, checks state and visible configuration, commits once, reads back, exits service mode and checks the normal-mode gate. It sends no language, region or reset commands.

After success, disconnect USB, power off normally and back on, inspect the HDMI RAW menu, reconnect USB and run:

```powershell
python unlock_hdmi_raw_ii.py verify --after-reboot
```

Keep both KEY files on the card. `--after-reboot` records your assertion of a manual reboot; it does not automatically prove a power cycle or successful external recording. `verify` and `snapshot` enter/exit service mode but send no activation or commit commands. This dedicated experimental script does not use `--experimental-z7`.

Additional commands and options:

```powershell
python unlock_hdmi_raw_ii.py list-devices
python unlock_hdmi_raw_ii.py snapshot
python unlock_hdmi_raw_ii.py prepare-keys
```

`prepare-keys` restores the local second-generation KEY files and refuses to overwrite different contents. `--keys-dir "folder"` selects a local KEY directory; local validation does not copy files to the camera. Use `--device "EXACT_DEVICE_ID"` if multiple Nikon cameras are attached and `--backup-dir "path/to/new-folder"` to choose a new output directory.

The default output is `backups/z6ii_z7ii/<UTC timestamp>/`. It includes `snapshot.json`, `item8_masked.bin`, `transactions.jsonl` (requests/responses) and `result.json` (activation/commit attempts, acknowledgement and errors). Unexpected configuration changes are saved as `unexpected_*.bin/json`. The second-generation readback masks **offsets 4 and 5**, so this is not a complete restorable image; never write it back wholesale. Logs contain camera identity information.

On failure, the script attempts to exit service mode and does not automatically retry or roll back writes. Partial RAM changes may exist; a lost commit response does not prove the commit failed. Preserve logs and inspect the failure stage before retrying. Firmware evidence tests are maintained in the source project; this portable release's tests do not require proprietary firmware modules.

## Z6 / Z7: backups and recovery

Each camera operation that saves data creates a new directory under `backups/`. Use `--backup-dir "path/to/new-folder"` to choose another location. Existing directories are not overwritten. Snapshots contain the camera serial number and device ID; remove identifying data before sharing logs.

The scripts back up the configuration, stage only the selected feature, compare the configuration and region, commit with F030, read back, and exit service mode. A successful command alone is not proof of persistence across reboot. On errors, keep the backup and error output; do not repeatedly retry writes without examining the failure.

`item8_masked.bin` is **not a complete restorable image**: the camera masks offsets 4 and 18 on readback. Never write it back wholesale. To restore only the previous language flag from the same camera and firmware:

```powershell
python unlock_languages.py unlock --restore-language-from "backups/PREVIOUS/snapshot.json"
python unlock_languages.py unlock --execute --restore-language-from "backups/PREVIOUS/snapshot.json"
python unlock_languages.py snapshot
```

After restoring a restricted list, `verify` correctly reports that the full list is not enabled; use `snapshot` to inspect the restored flag. No RAW deactivation command is provided.

To list connected devices:

```powershell
python unlock_hdmi_raw.py list-devices
```

If more than one Nikon is connected, append `--device "EXACT_DEVICE_ID"` to the selected script's command. `snapshot` enters then exits service mode and saves readback; `verify` also enters then exits service mode. Neither issues unlock or commit commands, but service-mode transitions can reload temporary state.

## Troubleshooting and offline tests

`0x200F`: KEY file not found. `0x2013`: file read failed. `0xA001`: KEY validation/handshake failed. `0x2019`: device busy. Check card placement, exact file names/size, USB connection and applications holding the camera. Both the outer PTP response and Nikon's vendor response must succeed.

```powershell
python -m unittest discover -v
```

The tests use simulated transport and do not connect to a camera. This is an independent, unofficial tool that writes persistent camera settings; retain the generated backups.
