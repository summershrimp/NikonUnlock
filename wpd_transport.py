"""Windows WPD MTP transport, including correctly framed data phases.

Requires comtypes. No device is opened at import time.
Constants follow Microsoft's WpdMtpExtensions.h. COM transport needs live testing.
"""
import ctypes as C
import os
import sys
from pathlib import Path

MTPEXT = "{4D545058-1A2E-4106-A357-771E0819FC56}"
COMMON = "{F0422A9C-5DC8-4440-B5BD-5DF28835658A}"
MAX_TRANSFER = 4 * 1024 * 1024


class WpdCamera:
    def __init__(self, device_id=None):
        self._load()
        ids = self.devices()
        candidates = [x for x in ids if "vid_04b0" in x.lower()]
        if device_id is None:
            if len(candidates) != 1:
                raise RuntimeError(f"Select exactly one Nikon with --device. Available: {ids}")
            device_id = candidates[0]
        elif device_id not in candidates:
            raise RuntimeError("Selected device is absent or is not a Nikon USB device")
        self.device_id = device_id
        self.dev = self.client.CreateObject(self.api.PortableDevice)
        self.dev.Open(device_id, self.values())

    @classmethod
    def _load(cls):
        if hasattr(cls, "client"):
            return
        if os.name != "nt":
            raise RuntimeError("WPD requires Windows")
        import comtypes
        import comtypes.client
        cls.comtypes, cls.client = comtypes, comtypes.client
        system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        cls.api = cls.client.GetModule(os.path.join(system, "PortableDeviceApi.dll"))
        cls.typ = cls.client.GetModule(os.path.join(system, "PortableDeviceTypes.dll"))
        cls.ole = C.OleDLL("ole32")
        cls.ole.PropVariantClear.argtypes = [C.c_void_p]
        cls.ole.CoTaskMemFree.argtypes = [C.c_void_p]
        cls.ole.CoTaskMemFree.restype = None

    @classmethod
    def devices(cls):
        cls._load()
        # Raw manager interface avoids generated GetDevices array marshaling.
        manager = cls.client.CreateObject(cls.api.PortableDeviceManager)
        count = C.c_ulong(0)
        cls.call(manager, 3, [C.c_void_p, C.POINTER(C.c_ulong)], None, C.byref(count))
        pointers = (C.c_void_p * count.value)()
        cls.call(manager, 3, [C.c_void_p, C.POINTER(C.c_ulong)], pointers, C.byref(count))
        result = []
        for p in pointers[:count.value]:
            if p:
                try:
                    result.append(C.wstring_at(p))
                finally:
                    cls.ole.CoTaskMemFree(p)
        return result

    @staticmethod
    def call(obj, slot, argtypes, *args):
        pointer = C.cast(obj, C.c_void_p)
        table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p)))[0]
        fn = C.WINFUNCTYPE(C.c_long, C.c_void_p, *argtypes)(table[slot])
        hr = fn(pointer, *args)
        if hr < 0:
            raise OSError(f"COM slot {slot}: HRESULT 0x{hr & 0xffffffff:08X}")

    def key(self, pid, category=MTPEXT):
        key = self.typ._tagpropertykey()
        names = [x[0] for x in key._fields_]
        setattr(key, names[0], self.comtypes.GUID(category))
        setattr(key, names[1], pid)
        return key

    def values(self):
        return self.client.CreateObject(self.typ.PortableDeviceValues)

    def command(self, command, opcode=None, params=()):
        value = self.values()
        value.SetGuidValue(self.key(1001, COMMON), self.comtypes.GUID(MTPEXT))
        value.SetUnsignedIntegerValue(self.key(1002, COMMON), command)
        if opcode is not None:
            value.SetUnsignedIntegerValue(self.key(1001), opcode)
            collection = self.client.CreateObject(self.typ.PortableDevicePropVariantCollection)
            for p in params:
                variant = self.typ.tag_inner_PROPVARIANT()
                variant.vt = 19
                getattr(variant, variant._fields_[4][0]).ulVal = p
                collection.Add(C.pointer(variant))
            value.SetIPortableDevicePropVariantCollectionValue(self.key(1002), collection)
        return value

    def decode_variant(self, buf):
        kind = C.c_ushort.from_buffer(buf).value
        if kind in (19, 10):
            return C.c_uint32.from_buffer(buf, 8).value
        if kind == 21:
            return C.c_uint64.from_buffer(buf, 8).value
        if kind == 31:
            pointer = C.c_void_p.from_buffer(buf, 8).value
            return C.wstring_at(pointer) if pointer else ""
        if kind == (0x1000 | 17):
            class Vector(C.Structure):
                _fields_ = [("count", C.c_uint32), ("data", C.c_void_p)]
            vector = Vector.from_buffer(buf, 8)
            if vector.count > MAX_TRANSFER:
                raise RuntimeError("Unreasonable WPD buffer size")
            return C.string_at(vector.data, vector.count) if vector.count else b""
        if kind == 13:
            pointer = C.c_void_p(C.c_void_p.from_buffer(buf, 8).value)
            count = C.c_ulong()
            self.call(pointer, 3, [C.POINTER(C.c_ulong)], C.byref(count))
            if count.value > 10000:
                raise RuntimeError("Unreasonable WPD collection size")
            result = []
            for i in range(count.value):
                child = C.create_string_buffer(32)
                try:
                    # IPortableDevicePropVariantCollection: GetCount=3, GetAt=4.
                    self.call(pointer, 4, [C.c_ulong, C.c_void_p], i, C.byref(child))
                    result.append(self.decode_variant(child))
                finally:
                    self.ole.PropVariantClear(C.byref(child))
            return result
        if kind == 0:
            return None
        raise RuntimeError(f"Unexpected PROPVARIANT type {kind}")

    def execute(self, values):
        response = self.dev.SendCommand(0, values)
        count = C.c_ulong()
        self.call(response, 3, [C.POINTER(C.c_ulong)], C.byref(count))
        result = {}
        for i in range(count.value):
            key = self.typ._tagpropertykey()
            buf = C.create_string_buffer(32)
            try:
                self.call(response, 4, [C.c_ulong, C.c_void_p, C.c_void_p], i, C.byref(key), C.byref(buf))
                names = [x[0] for x in key._fields_]
                category = str(getattr(key, names[0])).lower()
                pid = int(getattr(key, names[1]))
                if category == MTPEXT.lower() or (category == COMMON.lower() and pid == 1003):
                    result[(category, pid)] = self.decode_variant(buf)
            finally:
                self.ole.PropVariantClear(C.byref(buf))
        error = result.get((COMMON.lower(), 1003), 0)
        if error & 0x80000000:
            raise OSError(f"WPD command HRESULT 0x{error:08X}")
        return {pid: val for (category, pid), val in result.items() if category == MTPEXT.lower()}

    @staticmethod
    def response(result):
        return result.get(1003), result.get(1004, [])

    def send(self, opcode, params):
        return self.response(self.execute(self.command(12, opcode, params)))

    def end(self, context):
        value = self.command(17)
        value.SetStringValue(self.key(1006), context)
        return self.response(self.execute(value))

    def read(self, opcode, params):
        begin = self.execute(self.command(13, opcode, params))
        if 1006 not in begin:
            return b"", self.response(begin)
        context = begin[1006]
        total = begin.get(1007)
        if total is None or total < 0 or total > MAX_TRANSFER:
            self.end(context)
            raise RuntimeError(f"Unsupported/unknown transfer size {total!r}")
        chunk_size = min(begin.get(1013, 65536), 65536)
        if chunk_size <= 0:
            self.end(context)
            raise RuntimeError("Invalid transfer buffer size")
        data = bytearray()
        try:
            while len(data) < total:
                length = min(chunk_size, total - len(data))
                value = self.command(15)
                value.SetStringValue(self.key(1006), context)
                value.SetUnsignedIntegerValue(self.key(1008), length)
                buffer = (C.c_ubyte * length)()
                value.SetBufferValue(self.key(1012), buffer, length)
                result = self.execute(value)
                received, payload = result.get(1009), result.get(1012)
                if not isinstance(payload, bytes) or received != len(payload) or not 0 < received <= length:
                    raise RuntimeError("Invalid/short WPD data read")
                data.extend(payload)
        finally:
            response = self.end(context)
        return bytes(data), response

    def write(self, opcode, params, data):
        if not data or len(data) > MAX_TRANSFER:
            raise ValueError("Invalid data length")
        value = self.command(14, opcode, params)
        # Required VT_UI8; missing this caused the previous ERROR_NOT_FOUND.
        value.SetUnsignedLargeIntegerValue(self.key(1007), len(data))
        begin = self.execute(value)
        if 1006 not in begin:
            raise RuntimeError(f"Write transfer context missing: {begin}")
        context = begin[1006]
        chunk_size = min(begin.get(1013, 65536), 65536)
        if chunk_size <= 0:
            self.end(context)
            raise RuntimeError("Invalid transfer buffer size")
        try:
            for start in range(0, len(data), chunk_size):
                block = data[start:start+chunk_size]
                value = self.command(16)
                value.SetStringValue(self.key(1006), context)
                value.SetUnsignedIntegerValue(self.key(1010), len(block))
                buffer = (C.c_ubyte * len(block)).from_buffer_copy(block)
                value.SetBufferValue(self.key(1012), buffer, len(block))
                result = self.execute(value)
                if result.get(1011) != len(block):
                    raise RuntimeError("Short WPD write; do not retry blindly")
        finally:
            response = self.end(context)
        return response

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.dev.Close()
