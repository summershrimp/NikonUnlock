"""macOS ImageCaptureCore transport for the second-generation RAW profile.

The Objective-C helper is compiled locally on first use. It accepts only the
small PTP command set used by unlock_hdmi_raw_ii.py and never sends a PTP data
phase to the camera. First-generation language writes remain Windows-only.
"""

import base64
import json
import select
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "mac_ptp_bridge.m"
BINARY = ROOT / "build" / "mac_ptp_bridge"


def _helper():
    if sys.platform != "darwin":
        raise RuntimeError("mac_transport requires macOS")
    if not BINARY.exists() or BINARY.stat().st_mtime_ns < SOURCE.stat().st_mtime_ns:
        compiler = shutil.which("clang")
        if compiler is None:
            raise RuntimeError("Install Xcode Command Line Tools to compile mac_ptp_bridge.m")
        BINARY.parent.mkdir(parents=True, exist_ok=True)
        command = [compiler, "-fobjc-arc", "-fblocks", "-O2", str(SOURCE),
                   "-framework", "Foundation", "-framework", "ImageCaptureCore",
                   "-o", str(BINARY)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"macOS PTP helper compilation failed:\n{result.stderr}")
    return BINARY


def _read_json(stream, timeout):
    ready, _, _ = select.select([stream], [], [], timeout)
    if not ready:
        raise TimeoutError("macOS PTP helper did not respond in time")
    line = stream.readline()
    if not line:
        raise RuntimeError("macOS PTP helper exited without a response")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise RuntimeError("Invalid macOS PTP helper response")
    if "error" in value:
        raise RuntimeError(f"macOS PTP helper: {value['error']}")
    return value


class MacCamera:
    """The send/read/context-manager API consumed by unlock_hdmi_raw_ii.py."""

    def __init__(self, device_id=None, allow_activation=False):
        command = [str(_helper()), "--open", device_id or "auto"]
        if allow_activation:
            command.append("--activate")
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            encoding="utf-8", bufsize=1,
        )
        try:
            response = _read_json(self.process.stdout, 30)
            if response.get("ready") is not True:
                raise RuntimeError("macOS PTP helper did not open a camera session")
            self.device_id = response.get("device_id") or None
        except BaseException:
            self.process.kill()
            self.process.wait()
            raise

    @classmethod
    def devices(cls):
        result = subprocess.run([str(_helper()), "--list"], capture_output=True,
                                text=True, encoding="utf-8", timeout=20)
        if result.returncode:
            raise RuntimeError(f"macOS Nikon discovery failed: {result.stderr.strip()}")
        value = json.loads(result.stdout)
        if "error" in value:
            raise RuntimeError(value["error"])
        return value["devices"]

    def _call(self, method, opcode, params):
        if self.process.poll() is not None:
            raise RuntimeError("macOS PTP helper is no longer running")
        request = {"method": method, "opcode": opcode, "params": list(params)}
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        response = _read_json(self.process.stdout, 35)
        code, values = response["code"], response["params"]
        if not isinstance(code, int) or not isinstance(values, list):
            raise RuntimeError("Invalid macOS PTP response fields")
        return response, (code, values)

    def send(self, opcode, params):
        _, response = self._call("send", opcode, params)
        return response

    def read(self, opcode, params):
        value, response = self._call("read", opcode, params)
        data = base64.b64decode(value["data_base64"], validate=True)
        return data, response

    def close(self):
        if self.process.poll() is not None:
            return
        try:
            self.process.stdin.write('{"method":"close"}\n')
            self.process.stdin.flush()
            response = _read_json(self.process.stdout, 8)
            if response.get("closed") is not True:
                raise RuntimeError("macOS PTP helper did not acknowledge session close")
            self.process.wait(timeout=8)
            if self.process.returncode:
                raise RuntimeError("macOS PTP helper exited with an error")
        except BaseException:
            self.process.kill()
            self.process.wait()
            raise

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        try:
            self.close()
        except Exception:
            if error_type is None:
                raise
        return False
