# Experimental macOS transport for Z6 II / Z7 II HDMI RAW

This local branch adds an ImageCaptureCore PTP transport for the existing
second-generation C 1.70 profile. It keeps the same model and firmware guard,
backup, command sequence, and explicit activation flag as the Windows script.
The macOS transport has compiled and passed an offline framing check. It has
not been exercised against a camera. The second-generation activation and
power-cycle persistence remain unvalidated on physical Z6 II/Z7 II cameras.

This transport supports the second-generation HDMI RAW script only. The
first-generation language operation needs a PTP outbound data phase and still
uses the Windows WPD transport.

## Prerequisites

- macOS with Xcode Command Line Tools (the helper builds with clang).
- Python 3.11 or newer. The current workspace has python3.12 available;
  macOS system python3 may be too old.
- A Z6 II or Z7 II reporting C 1.70, a charged battery, and a compatible card.
- Close Photos, Image Capture, and other applications holding the camera.
- Do not install requirements.txt on macOS; its comtypes dependency is for WPD
  on Windows. The macOS transport uses the system ImageCaptureCore framework.

## Key files

The cloned repository already contains both second-generation files:

| File | Size | SHA-256 |
| --- | ---: | --- |
| keys/z6ii_z7ii/HDMIARAW.KEY | 32 bytes | 78312639fbbd5d406b7ac56313d9d25b6035e0889b901392389268fa81b76dcd |
| keys/z6ii_z7ii/SRF_FUNC.KEY | 32 bytes | dda93ba42e32a8c057a0fabb911b0c82156e24a36fd735a8f27d06927c0b52a5 |

These two hashes were checked against the actual files and the corresponding
SHA256SUMS.txt entries in this checkout. The other SHA256SUMS.txt entries refer
to the original release files and no longer describe source files modified on
this local branch.

Use a card reader to copy both binary files to the card root, preserving the
names exactly. The internal camera path A:\\ is not a macOS drive letter. Its
mapping to the two physical card slots has not been validated. Local file
validation cannot prove that the camera can read the files on the card.

## Sequence

Open Terminal in the repository folder. The example interpreter can be
replaced with another Python 3.11+ executable.

~~~sh
python3.12 unlock_hdmi_raw_ii.py unlock
python3.12 unlock_hdmi_raw_ii.py list-devices
~~~

The first command prints an offline plan. The second enumerates Nikon cameras
without opening a service session. On its first use, the macOS transport
compiles build/mac_ptp_bridge automatically. If several Nikon cameras appear,
copy the exact id field from list-devices and append --device "ID" to each
camera command.

Insert the prepared card, power on the camera, and connect it by USB. For a
read-only baseline, run:

~~~sh
python3.12 unlock_hdmi_raw_ii.py status
python3.12 unlock_hdmi_raw_ii.py snapshot
~~~

Status queries DeviceInfo and the normal-mode 0x944B gate. A false
raw_authorized_now is expected before activation. Snapshot enters and exits
service mode, saves the masked item-8 configuration, and queries the two RAW
states. It sends no activation or commit command. A service-mode transition
can still reload temporary camera state. If snapshot reports a KEY-related
error, resolve the card placement or contents before any activation attempt.

To request activation explicitly:

~~~sh
python3.12 unlock_hdmi_raw_ii.py unlock --execute
~~~

The script creates a fresh directory under backups/z6ii_z7ii/. Preserve its
snapshot.json, item8_masked.bin, transactions.jsonl, and result.json. The
readback masks bytes 4 and 5, so item8_masked.bin is not a restorable image.
The macOS bridge allows C102, C052, and F030 only for this explicit activation
invocation. There is no automatic write retry or rollback. A lost commit
response leaves the outcome uncertain.

After an acknowledged activation, disconnect USB and power the camera off
normally, then on again. Inspect the HDMI RAW menu, reconnect USB, and run:

~~~sh
python3.12 unlock_hdmi_raw_ii.py verify --after-reboot
~~~

The flag records your assertion that a power cycle occurred. A successful
readback does not validate external RAW recording. A compatible external
recorder must capture and decode an actual HDMI RAW file for that acceptance.

## Transport boundary

mac_ptp_bridge.m uses ICCameraDevice requestSendPTPCommand with a command
allowlist for this profile. It has no outbound PTP data phase, firmware update,
reset, or general camera memory command. The Python profile records logical
requests and responses. Actual behavior of the service commands over
ImageCaptureCore remains to be established with a physical camera.
