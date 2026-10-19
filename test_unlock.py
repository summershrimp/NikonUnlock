"""Offline tests: no COM imports, no camera access."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import unlock_core as r
from wpd_transport import WpdCamera


class Camera:
    def __init__(self):
        self.data = bytearray(r.ITEM8_SIZE)
        self.data[:4] = b"\x1f\xba\x3f\x10"
        self.region = b"\x00\x05"
        self.events = []
        self.fail = None
        self.corrupt = False

    def send(self, op, args):
        self.events.append((op, args[:], None))
        cmd = args[0]
        if cmd == self.fail:
            return 0x2001, [0xA001]
        if cmd == 0xC102:
            self.data[4] = 0x6A
        if cmd == 0xC052:
            self.data[18] = args[1]
        if cmd == 0xC101:
            return 0x2001, [0x2001, int(self.data[4] == 0x6A)]
        if cmd == 0xC051:
            return 0x2001, [0x2001, self.data[18]]
        return 0x2001, [0x2001]

    def read(self, op, args):
        self.events.append((op, args[:], None))
        assert op == 0x94FF
        if args[0] == 0xF006:
            return self.region + b"\xcc\xdd", (0x2001, [0x2001])
        assert args == r.params(0xF02D, 0, r.ITEM8_SIZE)
        result = bytearray(self.data)
        result[4] = result[18] = 0
        return bytes(result), (0x2001, [0x2001])

    def write(self, op, args, data):
        self.events.append((op, args[:], data))
        assert op == 0x94FE and args == r.params(0xF02E, 16, 1)
        self.data[16] = data[0]
        counter = struct.unpack_from("<I", self.data, r.ITEM8_SIZE - 4)[0]
        struct.pack_into("<I", self.data, r.ITEM8_SIZE - 4, counter + 1 if 1 <= counter <= 0xFFFFFFEE else 1)
        if self.corrupt:
            self.data[17] = 7
        return 0x2001, [0x2001, 1]


class RecoveryTests(unittest.TestCase):
    def run_update(self, cam, action, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "new"
            result = r.update(cam, action, folder, **kwargs)
            meta = json.loads((folder / "snapshot.json").read_text())
            self.assertEqual(meta["masked_offsets"], [4, 18])
            self.assertFalse(result["cold_boot_verified"])
            self.assertEqual(len((folder / "item8_masked.bin").read_bytes()), r.ITEM8_SIZE)
            return result

    def test_raw_commits_without_reset(self):
        cam = Camera()
        self.run_update(cam, "persist-raw")
        commands = [e[1][0] for e in cam.events]
        self.assertLess(commands.index(0xC102), commands.index(0xF030))
        self.assertEqual(commands.count(0xF030), 1)
        self.assertNotIn(0xF004, commands)
        self.assertNotIn(0xF02F, commands)
        self.assertEqual(cam.events[-1][:2], (0x94FE, r.params(0xF002)))

    def test_language_write_changes_only_byte16(self):
        cam = Camera()
        cam.data[4], cam.data[18] = 0x6A, 1
        before = bytes(cam.data)
        self.run_update(cam, "enable-languages")
        self.assertEqual(bytes(cam.data[:-4]), before[:16] + b"\x01" + before[17:-4])
        self.assertEqual(struct.unpack_from("<I", cam.data, r.ITEM8_SIZE - 4)[0], 1)
        self.assertEqual(cam.region, b"\x00\x05")
        self.assertFalse(any(e[1][0] in (0xC102, 0xC052, 0xF004, 0xF02F) for e in cam.events))

    def test_combined_action_rejected(self):
        with self.assertRaises(ValueError):
            r.update(Camera(), "activate", "unused")

    def test_flag_rollback_changes_only_byte16(self):
        cam = Camera()
        cam.data[16] = 1
        self.run_update(cam, "enable-languages", language_value=0)
        self.assertEqual(cam.data[16], 0)

    def test_failed_staging_does_not_commit_and_exits(self):
        cam = Camera()
        cam.fail = 0xC052
        with self.assertRaises(RuntimeError):
            self.run_update(cam, "persist-raw")
        self.assertNotIn(0xF030, [e[1][0] for e in cam.events])
        self.assertEqual(cam.events[-1][1][0], 0xF002)

    def test_unexpected_byte_change_does_not_commit(self):
        cam = Camera()
        cam.corrupt = True
        with self.assertRaises(RuntimeError):
            self.run_update(cam, "enable-languages")
        self.assertNotIn(0xF030, [e[1][0] for e in cam.events])

    def test_outer_success_is_not_vendor_success(self):
        for response in ((0x2001, []), (0x2001, [0xA001]), (None, [0x2001]), (0x2005, [0x2001])):
            with self.assertRaises(RuntimeError):
                r.check(response, True)

    def test_device_info_and_truncation(self):
        def s(text):
            return bytes([len(text)+1]) + (text+"\0").encode("utf-16le")
        data = bytes(8) + s("") + bytes(2) + bytes(20)
        data += s("Nikon Corporation") + s("Z 6") + s("3.80") + s("1234")
        self.assertEqual(r.device_info(data)["model"], "Z 6")
        with self.assertRaises(ValueError):
            r.device_info(data[:-3])



class Values:
    def __init__(self, command):
        self.command = command
        self.properties = {}

    def SetStringValue(self, key, value):
        self.properties[key] = ("string", value)

    def SetUnsignedIntegerValue(self, key, value):
        self.properties[key] = ("u32", value)

    def SetUnsignedLargeIntegerValue(self, key, value):
        self.properties[key] = ("u64", value)

    def SetBufferValue(self, key, value, size):
        self.properties[key] = ("buffer", bytes(value)[:size])


class TransportMock(WpdCamera):
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def command(self, command, opcode=None, params=()):
        value = Values(command)
        value.opcode, value.params = opcode, params
        return value

    def key(self, pid, category=None):
        return pid

    def execute(self, value):
        self.calls.append(value)
        return next(self.results)


class TransportTests(unittest.TestCase):
    def test_write_supplies_size_and_ends_transfer(self):
        cam = TransportMock([{1006: "ctx", 1013: 512}, {1011: 1}, {1003: 0x2001, 1004: [0x2001, 1]}])
        self.assertEqual(cam.write(0x94FE, r.params(0xF02E,16,1), b"\x01"), (0x2001, [0x2001,1]))
        self.assertEqual([v.command for v in cam.calls], [14, 16, 17])
        self.assertEqual(cam.calls[0].properties[1007], ("u64", 1))
        self.assertEqual(cam.calls[1].properties[1012], ("buffer", b"\x01"))

    def test_read_supplies_buffer_and_ends_transfer(self):
        cam = TransportMock([{1006: "ctx", 1007: 4, 1013: 512}, {1009: 4, 1012: b"abcd"}, {1003: 0x2001, 1004: [0x2001]}])
        self.assertEqual(cam.read(0x94FF, r.params(0xF006))[0], b"abcd")
        self.assertEqual([v.command for v in cam.calls], [13, 15, 17])
        self.assertEqual(cam.calls[1].properties[1008], ("u32", 4))

    def test_short_write_ends_and_raises_without_retry(self):
        cam = TransportMock([{1006: "ctx",1013:512}, {1011:0}, {1003:0x2001}])
        with self.assertRaises(RuntimeError):
            cam.write(0x94FE, r.params(0xF02E,16,1), b"\x01")
        self.assertEqual([v.command for v in cam.calls], [14,16,17])


if __name__ == "__main__":
    unittest.main()
