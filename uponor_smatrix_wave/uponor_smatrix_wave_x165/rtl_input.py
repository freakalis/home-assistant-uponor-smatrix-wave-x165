"""Receive-only RTL-SDR byte streams for Windows."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
import subprocess


class RtlError(RuntimeError):
    pass


def discover_rtlsdr_dll(explicit: str | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    configured = os.getenv("UPONOR_RTL_DLL")
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path.cwd() / "rtlsdr.dll")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


class RtlSdrDllStream:
    """Synchronous receive stream using the librtlsdr ABI directly."""

    def __init__(
        self,
        dll_path: Path,
        *,
        device_index: int,
        center_frequency: int,
        sample_rate: int,
        ppm: int = 0,
    ) -> None:
        self.dll_path = Path(dll_path).resolve()
        self.device_index = device_index
        self.center_frequency = center_frequency
        self.sample_rate = sample_rate
        self.ppm = ppm
        self._dll_directory = None
        self._dev = ctypes.c_void_p()
        self._closed = True

        if hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(self.dll_path.parent))
        self.lib = ctypes.CDLL(str(self.dll_path))
        self._declare_api()
        count = int(self.lib.rtlsdr_get_device_count())
        if not 0 <= device_index < count:
            raise RtlError(f"RTL-SDR device index {device_index} unavailable; found {count} device(s)")
        name = self.lib.rtlsdr_get_device_name(device_index)
        self.device_name = name.decode(errors="replace") if name else f"device {device_index}"
        self._check(self.lib.rtlsdr_open(ctypes.byref(self._dev), device_index), "open device")
        self._closed = False
        try:
            self._check(self.lib.rtlsdr_set_sample_rate(self._dev, sample_rate), "set sample rate")
            self._check(self.lib.rtlsdr_set_center_freq(self._dev, center_frequency), "set center frequency")
            if hasattr(self.lib, "rtlsdr_set_freq_correction"):
                ppm_result = self.lib.rtlsdr_set_freq_correction(self._dev, ppm)
                # librtlsdr returns -2 when the requested correction already
                # equals the current value. That is successful configuration,
                # not a device failure.
                if ppm_result not in (0, -2):
                    self._check(ppm_result, "set frequency correction")
            # Same receive policy as the user's SDR++ configuration: tuner AGC
            # enabled, RTL2832 digital AGC disabled.
            self._check(self.lib.rtlsdr_set_tuner_gain_mode(self._dev, 0), "enable tuner AGC")
            if hasattr(self.lib, "rtlsdr_set_agc_mode"):
                self._check(self.lib.rtlsdr_set_agc_mode(self._dev, 0), "disable RTL AGC")
            if hasattr(self.lib, "rtlsdr_set_direct_sampling"):
                self._check(self.lib.rtlsdr_set_direct_sampling(self._dev, 0), "disable direct sampling")
            if hasattr(self.lib, "rtlsdr_set_bias_tee"):
                bias_result = self.lib.rtlsdr_set_bias_tee(self._dev, 0)
                # Some Windows librtlsdr builds return the positive USB
                # control-transfer byte count on success.
                if bias_result < 0:
                    self._check(bias_result, "disable bias tee")
            self._check(self.lib.rtlsdr_reset_buffer(self._dev), "reset receive buffer")
        except Exception:
            self.close()
            raise

    def _declare_api(self) -> None:
        lib = self.lib
        lib.rtlsdr_get_device_count.restype = ctypes.c_uint32
        lib.rtlsdr_get_device_name.argtypes = [ctypes.c_uint32]
        lib.rtlsdr_get_device_name.restype = ctypes.c_char_p
        lib.rtlsdr_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32]
        lib.rtlsdr_open.restype = ctypes.c_int
        lib.rtlsdr_close.argtypes = [ctypes.c_void_p]
        lib.rtlsdr_close.restype = ctypes.c_int
        for name in ("rtlsdr_set_center_freq", "rtlsdr_set_sample_rate"):
            fn = getattr(lib, name)
            fn.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            fn.restype = ctypes.c_int
        for name in (
            "rtlsdr_set_freq_correction",
            "rtlsdr_set_tuner_gain_mode",
            "rtlsdr_set_agc_mode",
            "rtlsdr_set_direct_sampling",
            "rtlsdr_set_bias_tee",
        ):
            if hasattr(lib, name):
                fn = getattr(lib, name)
                fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
                fn.restype = ctypes.c_int
        lib.rtlsdr_reset_buffer.argtypes = [ctypes.c_void_p]
        lib.rtlsdr_reset_buffer.restype = ctypes.c_int
        lib.rtlsdr_read_sync.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.rtlsdr_read_sync.restype = ctypes.c_int

    @staticmethod
    def _check(code: int, operation: str) -> None:
        if code != 0:
            raise RtlError(f"Could not {operation}: librtlsdr error {code}")

    def read(self, sample_count: int) -> bytes:
        if self._closed:
            return b""
        byte_count = 2 * sample_count
        buffer = (ctypes.c_ubyte * byte_count)()
        received = ctypes.c_int()
        self._check(
            self.lib.rtlsdr_read_sync(self._dev, buffer, byte_count, ctypes.byref(received)),
            "read samples",
        )
        return bytes(buffer[:received.value])

    def close(self) -> None:
        if not self._closed:
            self.lib.rtlsdr_close(self._dev)
            self._closed = True
        if self._dll_directory is not None:
            self._dll_directory.close()
            self._dll_directory = None

    def __enter__(self) -> "RtlSdrDllStream":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class RtlSdrProcessStream:
    """Fallback input backed by the standard rtl_sdr command-line tool."""

    def __init__(self, executable: str, *, device_index: int, center_frequency: int, sample_rate: int, ppm: int = 0) -> None:
        resolved = shutil.which(executable) or (executable if Path(executable).is_file() else None)
        if not resolved:
            raise RtlError(f"rtl_sdr executable not found: {executable}")
        command = [resolved, "-d", str(device_index), "-f", str(center_frequency), "-s", str(sample_rate), "-p", str(ppm), "-"]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=None, creationflags=creationflags)
        self.device_name = f"rtl_sdr process, device {device_index}"
        self.center_frequency = center_frequency
        self.sample_rate = sample_rate

    def read(self, sample_count: int) -> bytes:
        if self.process.stdout is None:
            return b""
        wanted = 2 * sample_count
        chunks = bytearray()
        while len(chunks) < wanted:
            chunk = self.process.stdout.read(wanted - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
