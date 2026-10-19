#!/usr/bin/env python3
"""Nikon Z6 II / Z7 II C 1.70 HDMI RAW activation (static-analysis profile).

Default is an offline plan. Windows WPD transport has been used on Z6;
this second-generation profile has NOT been tested on a physical camera.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct

ITEM8_SIZE = 0x1FFFC
MAGIC = 0x28CD7EE8
MASKED_OFFSETS = (4, 5)
KEYS = {
    "HDMIARAW.KEY": bytes.fromhex("79869759a9dbea2beb46bf9d53ee580dc9f750dbe3b95c3687a80a11c502a484"),
    "SRF_FUNC.KEY": bytes.fromhex("8b98ec9af1fc67bdb9aee75ded7d540ac85adc4c545dbf0a50c5ef4bf29c7d52"),
}
KEY_DIR = Path(__file__).resolve().parent / "keys" / "z6ii_z7ii"


def save_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare_keys(folder):
    """Create local binary artifacts, never overwrite a different existing file."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in KEYS.items():
        p = folder / name
        if p.exists() and p.read_bytes() != content:
            raise RuntimeError(f"Refusing to replace a different key: {p}")
    for name, content in KEYS.items():
        p = folder / name
        if not p.exists():
            with p.open("xb") as stream:
                stream.write(content)
    validate_keys(folder)
    return {name: {"size": len(data), "sha256": digest(data)} for name, data in KEYS.items()}


def validate_keys(folder):
    for name, content in KEYS.items():
        p = Path(folder) / name
        if not p.is_file() or p.read_bytes() != content:
            raise RuntimeError(f"Missing/wrong second-generation binary KEY: {p}")


def params(command, *args):
    if len(args) > 4 or any(type(x) is not int or not 0 <= x <= 0xFFFFFFFF for x in (command, *args)):
        raise ValueError("Internal command needs at most four u32 arguments")
    return [command, *args, *([0] * (4-len(args)))]


def check(response, vendor=False, allowed=(0x2001,)):
    code, values = response
    if code != 0x2001:
        raise RuntimeError(f"PTP failed: {code!r}, parameters={values!r}")
    if vendor and (not values or values[0] not in allowed):
        raise RuntimeError(f"Nikon command failed: {values!r}; 200F=key missing, A001=key/check failed")
    return values


def internal(cam, command, *args, allowed=(0x2001,)):
    opcode = 0x94FE if command == 0xF002 else 0x94FD
    return check(cam.send(opcode, params(command, *args)), True, allowed)


@contextmanager
def service(cam):
    # Include entry in try: an ambiguous transport failure may follow entry.
    primary = None
    try:
        internal(cam, 0xF001, allowed=(0x2001, 0x201E))
        yield
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            internal(cam, 0xF002)
        except Exception as exc:
            if primary is None:
                raise
            primary.add_note(f"Service-mode exit also failed: {exc}")


def device_info(data):
    offset = 0
    def take(n):
        nonlocal offset
        value = data[offset:offset+n]
        if len(value) != n:
            raise ValueError("Truncated PTP DeviceInfo")
        offset += n
        return value
    def string():
        count = take(1)[0]
        return take(count*2).decode("utf-16le").rstrip("\0") if count else ""
    take(8)
    string()
    take(2)
    for _ in range(5):
        take(struct.unpack("<I", take(4))[0]*2)
    return dict(zip(("manufacturer", "model", "version", "serial"), (string() for _ in range(4))))


def validate_camera(cam):
    data, response = cam.read(0x1001, [])
    check(response)
    identity = device_info(data)
    model = re.sub(r"\s+", "", identity["model"]).upper().removeprefix("NIKON")
    if identity["manufacturer"].strip().upper() not in ("NIKON", "NIKON CORPORATION") or model not in ("Z6II", "Z7II", "Z6_2", "Z7_2"):
        raise RuntimeError(f"Only Nikon Z6 II / Z7 II supported: {identity}")
    version = identity["version"].strip().upper().removeprefix("V")
    if not re.fullmatch(r"0?1\.70(?:\.00|\.02)?", version):
        raise RuntimeError(f"Only C 1.70 analyzed; refusing other firmware: {identity}")
    cam.identity = identity
    return identity


def read_options(cam):
    data, response = cam.read(0x94FF, params(0xF02D, 0, ITEM8_SIZE))
    check(response, True)
    if len(data) != ITEM8_SIZE:
        raise RuntimeError(f"Incomplete item8: {len(data)} / {ITEM8_SIZE}")
    if any(data[i] != 0 for i in MASKED_OFFSETS):
        raise RuntimeError("Unexpected F02D masking; second-generation profile does not match")
    return data


def raw_state(cam):
    result = {}
    for name, cmd in (("raw_authorized", 0xC101), ("raw_status", 0xC051)):
        value = internal(cam, cmd)
        if len(value) < 2 or type(value[1]) is not int or value[1] not in (0, 1):
            raise RuntimeError(f"Invalid {name} readback: {value!r}")
        result[name] = value[1]
    return result


def snapshot(cam, folder):
    data = read_options(cam)
    (folder / "item8_masked.bin").write_bytes(data)
    meta = {"created_utc": datetime.now(timezone.utc).isoformat(),
            "identity": cam.identity, "size": len(data), "sha256": digest(data),
            "masked_offsets": list(MASKED_OFFSETS),
            "note": "F02D masks bytes 4 and 5. NOT an exact restorable image."}
    save_json(folder / "snapshot.json", meta)
    # Both key-dependent checks complete BEFORE either activation command.
    meta.update(raw_state(cam))
    save_json(folder / "snapshot.json", meta)
    return data, meta


def unchanged(cam, before, folder, label):
    after = read_options(cam)
    if after != before:
        (folder / f"unexpected_{label}.bin").write_bytes(after)
        diffs = [{"offset": i, "before": x, "after": y} for i, (x, y) in enumerate(zip(before, after)) if x != y]
        save_json(folder / f"unexpected_{label}.json", diffs)
        raise RuntimeError(f"Unexpected item8 change at {label}; first differences: {diffs[:8]}")


def require_enabled(cam):
    value = raw_state(cam)
    if value != {"raw_authorized": 1, "raw_status": 1}:
        raise RuntimeError(f"RAW readback is not fully enabled: {value}")
    return value


class LoggedCamera:
    """Append a request BEFORE sending it; preserve partial-operation evidence."""
    def __init__(self, camera, folder):
        self.camera = camera
        self.path = folder / "transactions.jsonl"
    def log(self, value):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"utc": datetime.now(timezone.utc).isoformat(), **value}) + "\n")
    def call(self, method, opcode, args):
        self.log({"event": "request", "method": method, "opcode": hex(opcode), "params": [hex(x) for x in args]})
        try:
            result = getattr(self.camera, method)(opcode, args)
        except BaseException as exc:
            self.log({"event": "exception", "error": str(exc)})
            raise
        if method == "read":
            data, response = result
            self.log({"event": "response", "response": response, "size": len(data), "sha256": digest(data)})
        else:
            self.log({"event": "response", "response": result})
        return result
    def send(self, opcode, args):
        return self.call("send", opcode, args)
    def read(self, opcode, args):
        return self.call("read", opcode, args)


def run(camera, action, folder, keys_dir=KEY_DIR, after_reboot=False):
    if action not in ("unlock", "verify", "snapshot", "status"):
        raise ValueError(action)
    if action != "status":
        validate_keys(keys_dir)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    cam = LoggedCamera(camera, folder)
    result = {"action": action, "profile": "Z6II/Z7II C1.70 static-analysis",
              "activation_attempted": False, "commit_attempted": False,
              "commit_acknowledged": False, "success": False,
              "user_reported_reboot": after_reboot, "cold_boot_verified": False}
    save_json(folder / "result.json", result)
    try:
        result["identity"] = validate_camera(cam)
        if action == "status":
            response = cam.send(0x944B, [])
            if response[0] not in (0x2001, 0x2005):
                check(response)
            result.update(normal_mode_944B=response, raw_authorized_now=response[0] == 0x2001)
        else:
            if action == "verify":
                check(cam.send(0x944B, []))
            with service(cam):
                before, meta = snapshot(cam, folder)
                result["initial_state"] = {k: meta[k] for k in ("raw_authorized", "raw_status")}
                if action == "unlock":
                    unchanged(cam, before, folder, "preflight")
                    result["activation_attempted"] = True
                    save_json(folder / "result.json", result)
                    internal(cam, 0xC102, MAGIC)
                    internal(cam, 0xC052, 1)
                    require_enabled(cam)
                    unchanged(cam, before, folder, "before_commit")
                    result["commit_attempted"] = True
                    save_json(folder / "result.json", result)
                    internal(cam, 0xF030)
                    result["commit_acknowledged"] = True
                    save_json(folder / "result.json", result)
                    unchanged(cam, before, folder, "after_commit")
                    result["final_state"] = require_enabled(cam)
                elif action == "verify":
                    result["final_state"] = require_enabled(cam)
            if action in ("unlock", "verify"):
                response = cam.send(0x944B, [])
                check(response)
                result["normal_mode_944B"] = response
                result["readback_verified"] = True
        result["success"] = True
    except BaseException as exc:
        result["error"] = str(exc)
        result["error_notes"] = getattr(exc, "__notes__", [])
        result["note"] = "No write retry or rollback performed. Inspect transactions before retrying; partial RAM changes or a completed commit may exist."
        raise
    finally:
        save_json(folder / "result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="plan", choices=("plan", "prepare-keys", "list-devices", "status", "snapshot", "verify", "unlock"))
    parser.add_argument("--execute", action="store_true", help="Send activation commands; without this flag unlock is an offline plan")
    parser.add_argument("--device", help="Exact WPD device ID (required if multiple Nikon cameras are attached)")
    parser.add_argument("--keys-dir", type=Path, default=KEY_DIR)
    parser.add_argument("--backup-dir", type=Path, help="New directory for this run; existing paths are refused")
    parser.add_argument("--after-reboot", action="store_true", help="For verify: record your assertion that you manually power-cycled the camera")
    args = parser.parse_args(argv)
    if args.after_reboot and args.action != "verify":
        parser.error("--after-reboot is only for verify")
    if args.action == "plan" or (args.action == "unlock" and not args.execute):
        print("OFFLINE PLAN; no camera accessed. Profile: Nikon Z6 II / Z7 II C1.70 ONLY.")
        print("Copy HDMIARAW.KEY and SRF_FUNC.KEY from keys-dir to the camera card root (A:).")
        print("DeviceInfo guard -> F001 -> masked backup + both key checks -> C102(0x28CD7EE8)")
        print("-> C052(1) -> RAW/config verification -> F030 -> verification -> F002 -> 944B.")
        print("F02D size=0x1FFFC; masked offsets=4,5. No language/region/reset commands.")
        print("Static-analysis profile; second-generation hardware/persistence/RAW recording remain untested.")
        return 0
    if args.action == "prepare-keys":
        print(json.dumps(prepare_keys(args.keys_dir), indent=2))
        print(f"Keys saved to {args.keys_dir}; copy BOTH binary files to the camera card root.")
        return 0
    if args.action != "list-devices" and args.action != "status":
        validate_keys(args.keys_dir)
    from wpd_transport import WpdCamera
    if args.action == "list-devices":
        print(json.dumps(WpdCamera.devices(), indent=2))
        return 0
    folder = args.backup_dir or Path(__file__).resolve().parent / "backups" / "z6ii_z7ii" / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f_UTC")
    if folder.exists():
        parser.error(f"Backup directory already exists: {folder}")
    with WpdCamera(args.device) as cam:
        result = run(cam, args.action, folder, args.keys_dir, args.after_reboot)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Audit/backup: {folder}")
    if args.action == "unlock":
        print("Commit acknowledged and current state verified. Manually power off/on, then run verify --after-reboot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
