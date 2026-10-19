#!/usr/bin/env python3
"""Nikon Z6/Z7 3.80 shared PTP operations. No firmware patching.

Z6 hardware validated; Z7 experimental. See README.md.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct

ITEM8_SIZE = 0x1FFFC
MAGIC = 0x548B3FBA


def check(response, vendor=False, allowed=(0x2001,)):
    code, params = response
    if code != 0x2001:
        raise RuntimeError(f"PTP transport response: {code!r}, params={params!r}")
    if vendor and (not params or params[0] not in allowed):
        raise RuntimeError(f"Nikon response missing/failed: {params!r}")
    return params


def params(command, *args):
    if len(args) > 4:
        raise ValueError("At most four internal command arguments")
    return [command, *args, *([0] * (4 - len(args)))]


def internal(cam, command, *args, allowed=(0x2001,)):
    # F002 uses the already established exit route in this project.
    op = 0x94FE if command == 0xF002 else 0x94FD
    return check(cam.send(op, params(command, *args)), True, allowed)


def read_internal(cam, command, *args):
    data, response = cam.read(0x94FF, params(command, *args))
    check(response, True)
    return data


@contextmanager
def service(cam):
    internal(cam, 0xF001, allowed=(0x2001, 0x201E))
    try:
        yield
    finally:
        internal(cam, 0xF002)


def read_options(cam):
    data = read_internal(cam, 0xF02D, 0, ITEM8_SIZE)
    if len(data) != ITEM8_SIZE:
        raise RuntimeError(f"Incomplete item8: {len(data)} / {ITEM8_SIZE}")
    return data


def read_region(cam):
    data = read_internal(cam, 0xF006)
    if len(data) != 4:
        raise RuntimeError(f"F006 returned {len(data)} bytes, expected 4")
    # Only the first two bytes are initialized by the firmware handler.
    return data[:2]


def snapshot(cam, folder, include_raw=False):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    region = read_region(cam)
    data = read_options(cam)
    (folder / "item8_masked.bin").write_bytes(data)
    meta = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "device_id": getattr(cam, "device_id", None),
        "identity": getattr(cam, "identity", None),
        "region_bytes": region.hex(),
        "language_flag": data[16],
        "sha256": hashlib.sha256(data).hexdigest(),
        "masked_offsets": [4, 18],
        "note": "F02D forcibly masks offsets 4 and 18; this is NOT a full restorable backup.",
    }
    # Save baseline even if key-file-dependent probes subsequently fail.
    (folder / "snapshot.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if include_raw:
        raw = internal(cam, 0xC101)
        status = internal(cam, 0xC051)
        if len(raw) < 2 or len(status) < 2:
            raise RuntimeError("Missing C101/C051 readback values")
        meta.update(raw_authorized=raw[1], raw_status=status[1])
        (folder / "snapshot.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return data, meta


def update(cam, action, folder, language_value=1):
    """Call only after model/version validation. No automatic retry of writes."""
    if action not in ("persist-raw", "enable-languages"):
        raise ValueError(action)
    raw = action == "persist-raw"
    languages = action == "enable-languages"
    with service(cam):
        before, meta = snapshot(cam, folder, include_raw=raw)
        expected = before
        if languages:
            if not 0 <= language_value <= 255:
                raise ValueError("Invalid saved language flag")
            response = cam.write(0x94FE, params(0xF02E, 16, 1), bytes([language_value]))
            result = check(response, True)
            if len(result) < 2 or result[1] != 1:
                raise RuntimeError("F02E did not acknowledge one byte; not committing")
            expected = before[:16] + bytes([language_value]) + before[17:]
            # sub_101B095A increments the final u32 during F02E synchronization.
            counter = struct.unpack_from("<I", before, ITEM8_SIZE - 4)[0]
            counter = counter + 1 if 1 <= counter <= 0xFFFFFFEE else 1
            expected = expected[:-4] + struct.pack("<I", counter)
        if raw:
            internal(cam, 0xC102, MAGIC)
            internal(cam, 0xC052, 1)
            if internal(cam, 0xC101)[1] != 1 or internal(cam, 0xC051)[1] != 1:
                raise RuntimeError("RAW staging verification failed; not committing")
        after = read_options(cam)
        region_after = read_region(cam).hex()
        if after != expected or region_after != meta["region_bytes"]:
            (Path(folder) / "unexpected_after.bin").write_bytes(after)
            differences = [{"offset": i, "before": before[i], "expected": expected[i], "actual": after[i]} for i in range(len(after)) if after[i] != expected[i]]
            (Path(folder) / "unexpected.json").write_text(json.dumps({"region_after": region_after, "differences": differences}, indent=2), encoding="utf-8")
            raise RuntimeError(f"Unexpected configuration change; not committing: {differences[:20]}, region={region_after}")
        internal(cam, 0xF030)
        # A response is not proof of cold-boot persistence. Keep a separate result.
        if read_options(cam) != expected:
            raise RuntimeError("Post-commit readback mismatch")
        if raw:
            if internal(cam, 0xC101)[1] != 1 or internal(cam, 0xC051)[1] != 1:
                raise RuntimeError("Post-commit RAW readback mismatch")
        result = {"action": action, "commit_acknowledged": True,
                  "cold_boot_verified": False, "language_flag_after": expected[16]}
        (Path(folder) / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def device_info(data):
    """Parse PTP DeviceInfo without interpreting any vendor private data."""
    offset = 0

    def take(n):
        nonlocal offset
        value = data[offset:offset+n]
        if len(value) != n:
            raise ValueError("Truncated DeviceInfo")
        offset += n
        return value

    def string():
        count = take(1)[0]
        if not count:
            return ""
        return take(count * 2).decode("utf-16le").rstrip("\x00")

    take(8)  # StandardVersion, VendorExtensionID, VendorExtensionVersion
    string()
    take(2)  # FunctionalMode
    for _ in range(5):
        count = struct.unpack("<I", take(4))[0]
        take(count * 2)
    return dict(zip(("manufacturer", "model", "version", "serial"), (string() for _ in range(4))))


def validate_camera(cam, allow_experimental_z7=False):
    data, response = cam.read(0x1001, [])
    check(response)
    identity = device_info(data)
    if "nikon" not in identity["manufacturer"].lower():
        raise RuntimeError(f"Not Nikon: {identity}")
    if identity["model"].replace(" ", "").upper() not in ("Z6", "NIKONZ6", "Z7", "NIKONZ7"):
        raise RuntimeError(f"Only first-generation Z6/Z7 supported: {identity}")
    if identity["version"].strip().upper().removeprefix("V") not in ("3.80", "03.80", "3.80.00", "03.80.00"):
        raise RuntimeError(f"Only firmware 3.80 analyzed: {identity}")
    if identity["model"].replace(" ", "").upper() in ("Z7", "NIKONZ7") and not allow_experimental_z7:
        raise RuntimeError("Z7 is not hardware validated; use --experimental-z7 to opt in")
    cam.identity = identity
    return identity


