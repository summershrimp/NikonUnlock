# macOS Transport and Z6 II Hardware Validation Guide

This repository supports running `unlock_hdmi_raw_ii.py` natively on **macOS** via the system `ImageCaptureCore.framework` (`mac_transport.py` + `mac_ptp_bridge.m`), alongside the Windows WPD transport.

The macOS transport preserves the exact same model/firmware guards, masked `F02D` backup, dual-KEY verification (`C101` / `C051`), explicit `--execute` gate, `F030` commit, and post-commit readback checks as Windows. First-generation language unlocking (`unlock_languages.py`) requires an outbound PTP data phase (`F02E`) and remains Windows-only.

## Hardware Validation Status (Z6 II C 1.70)

| Item | Status | Observed Evidence |
| --- | --- | --- |
| Camera & firmware | Validated on 1 unit | `Nikon Corporation` / `Z 6_2` / `V1.70` |
| Transport | Validated | macOS `ImageCaptureCore` (`ICCameraDevice requestSendPTPCommand`) |
| Card slot mapping (`A:\`) | Validated | Firmware handlers `0x10779156` / `0x10779352` decode fixed bitwise-inverted paths `A:\HDMIARAW.KEY` and `A:\SRF_FUNC.KEY` (no `B:\` fallback). On Z6 II, PTP storage `0x00010000` is **Slot 1 (CFexpress/XQD = `A:\`)** and `0x00020001` is **Slot 2 (SD = `B:\`)**. Placing keys only on an SD card in Slot 2 (even with Slot 1 empty) returns `0x200F` (`key missing`). Placing both keys on a CFexpress/XQD card in **Slot 1** succeeds (`0x2001`). |
| Pre-unlock baseline | Validated | Normal-mode `0x944B` returned `0x2005` (`8197`); service-mode `C101` and `C051` returned `[0x2001, 0, 0, 0, 0]`; `F02D` read back `131068` (`0x1FFFC`) bytes with offsets `4, 5` masked to `0`. |
| Activation & commit | Validated | `C102(0x28CD7EE8)` -> `0x2001`, `C052(1)` -> `0x2001`, `C101` -> `1`, `C051` -> `1`, unmasked `131066` bytes of `F02D` unchanged, `F030` commit acknowledged in ~2.04 s (`0x2001`), post-exit `0x944B` -> `0x2001` (`8193`). |
| Power-cycle persistence | Validated | After normal power-off and cold boot, `verify --after-reboot` observed normal-mode `0x944B` returning `0x2001` immediately before entering service mode, `C101=1` and `C051=1` inside service mode, unmasked `F02D` SHA-256 unchanged, and `0x944B` returning `0x2001` after `F002`. |
| Remaining boundaries | Unvalidated | Physical **Z7 II** hardware and actual external HDMI RAW recorder capture/decoding (e.g., Atomos / Blackmagic Video Assist) must still be verified separately. |

## Prerequisites on macOS

1. **Xcode Command Line Tools** (`clang` is invoked automatically on first device access to compile `build/mac_ptp_bridge` against `Foundation` and `ImageCaptureCore`).
2. **Python 3.11 or newer** (for example `python3.12` from Homebrew/Miniforge; macOS system `/usr/bin/python3` is 3.9 and too old for `Exception.add_note`).
3. **Do not install `requirements.txt` on macOS**: `comtypes` is only for Windows WPD.
4. Close **Photos**, **Image Capture**, **NX Tether**, **Lightroom**, or any background utility that opens a PTP session with the camera.

## Step 1: Copy Second-Generation Keys to a Slot 1 (CFexpress / XQD) Card

The repository already includes both 32-byte second-generation key files:

| File | Size | SHA-256 |
| --- | ---: | --- |
| `keys/z6ii_z7ii/HDMIARAW.KEY` | 32 bytes | `78312639fbbd5d406b7ac56313d9d25b6035e0889b901392389268fa81b76dcd` |
| `keys/z6ii_z7ii/SRF_FUNC.KEY` | 32 bytes | `dda93ba42e32a8c057a0fabb911b0c82156e24a36fd735a8f27d06927c0b52a5` |

Mount your **CFexpress Type B or XQD card** (for **Slot 1**) on macOS and write/verify the keys directly to the mounted volume root using `prepare-keys`, which also strips any macOS AppleDouble `._HDMIARAW.KEY` / `._SRF_FUNC.KEY` sidecar files:

```sh
python3.12 unlock_hdmi_raw_ii.py prepare-keys --keys-dir "/Volumes/YOUR_CFEXPRESS_VOLUME"
sync
diskutil eject "/Volumes/YOUR_CFEXPRESS_VOLUME"
```

Always eject the volume cleanly (`diskutil eject` or Finder eject) before removing the card from the reader so ExFAT directory entries are flushed to media.

## Step 2: Connect Camera and Run Read-Only Checks

1. Insert the prepared CFexpress/XQD card into **Slot 1** of the Z6 II / Z7 II.
2. Turn the camera **ON** and connect it to the Mac via USB.
3. Run the offline plan and device discovery:

```sh
python3.12 unlock_hdmi_raw_ii.py unlock
python3.12 unlock_hdmi_raw_ii.py list-devices
```

4. Run the read-only normal-mode and service-mode checks:

```sh
python3.12 unlock_hdmi_raw_ii.py status --device auto
python3.12 unlock_hdmi_raw_ii.py snapshot --device auto
```

- On an unactivated camera, `status` reports `"normal_mode_944B": [8197, []]` (`0x2005`) and `"raw_authorized_now": false`.
- `snapshot` enters service mode (`F001`), saves `item8_masked.bin` (`131068` bytes), verifies that the camera can read both Slot 1 key files via `C101` and `C051` (expecting `raw_authorized: 0, raw_status: 0` before activation), and exits service mode (`F002`).
- If `snapshot` raises `0x200F`, verify that the card is in **Slot 1 (CFexpress/XQD)** rather than Slot 2 (SD), and that both filenames are uppercase in the card root.

## Step 3: Execute Activation and Verify Persistence Across Reboot

When `snapshot` succeeds cleanly, run the activation sequence:

```sh
python3.12 unlock_hdmi_raw_ii.py unlock --execute --device auto
```

Keep USB and camera power untouched until the command prints the JSON result (`"commit_acknowledged": true`, `"final_state": {"raw_authorized": 1, "raw_status": 1}`, `"normal_mode_944B": [8193, []]`).

Then verify cold-boot persistence:

1. Turn the camera power switch to **OFF** and wait for the display/top LCD to shut down completely.
2. Turn the camera power switch back to **ON** with USB connected.
3. Run:

```sh
python3.12 unlock_hdmi_raw_ii.py verify --after-reboot --device auto
```

Confirm that `normal_mode_944B` is `[8193, []]` (`0x2001`) and `final_state` reports `{"raw_authorized": 1, "raw_status": 1}`. You can also inspect **Setup Menu -> HDMI -> Advanced -> RAW output options** on the camera body.

## macOS Implementation Notes

- **Asynchronous `ICDeviceBrowser` enumeration**: on macOS, `deviceBrowserDidEnumerateLocalDevices:` and the first `didAddDevice:` callback for `ICCameraDevice` can arrive on separate RunLoop turns. `mac_ptp_bridge.m` explicitly waits via `waitForCamera:timeout:` until the target Nikon camera is discovered and unambiguous before attempting `requestOpenSession`.
- **Command allowlist**: `mac_ptp_bridge.m` enforces a strict opcode/parameter allowlist for the second-generation profile (`0x1001`, `0x944B`, `0x94FF/F02D`, `0x94FD/F001`, `0x94FD/C101`, `0x94FD/C051`, `0x94FE/F002`), and only unlocks `C102(0x28CD7EE8)`, `C052(1)`, and `F030` when launched with `--activate` (`unlock --execute`).
