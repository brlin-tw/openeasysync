# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Windows SCSI Pass-Through Direct (SPTI) transport for OTi-2108 (\\\\.\\X:)."""

import ctypes
import sys
from typing import Literal

from openeasysync.transport.base import TransportBackend

IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x0004D014

SCSI_IOCTL_DATA_OUT = 0
SCSI_IOCTL_DATA_IN = 1
SCSI_IOCTL_DATA_UNSPECIFIED = 2


class ScsiPassThroughDirect(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("ScsiStatus", ctypes.c_ubyte),
        ("PathId", ctypes.c_ubyte),
        ("TargetId", ctypes.c_ubyte),
        ("Lun", ctypes.c_ubyte),
        ("CdbLength", ctypes.c_ubyte),
        ("SenseInfoLength", ctypes.c_ubyte),
        ("DataIn", ctypes.c_ubyte),
        ("DataTransferLength", ctypes.c_ulong),
        ("TimeOutValue", ctypes.c_ulong),
        ("DataBuffer", ctypes.c_void_p),
        ("SenseInfoOffset", ctypes.c_ulong),
        ("Cdb", ctypes.c_ubyte * 16),
    ]


class WindowsSptiTransport(TransportBackend):
    """Windows SCSI Pass-Through Direct transport."""

    def __init__(self, drive_letter: str):
        if sys.platform != "win32":
            raise NotImplementedError("WindowsSptiTransport is only supported on Windows")

        clean_drive = drive_letter.strip().rstrip(":\\").upper()
        self.device_path = f"\\\\.\\{clean_drive}:"

        import ctypes.wintypes

        self.kernel32 = ctypes.windll.kernel32
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3

        self.handle = self.kernel32.CreateFileW(
            self.device_path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if self.handle == -1 or self.handle == 0xFFFFFFFF:
            err = self.kernel32.GetLastError()
            raise OSError(f"Failed to open drive {self.device_path}: Windows error {err}")

    def execute_cdb(
        self,
        cdb: bytes,
        direction: Literal["NONE", "IN", "OUT"],
        data_or_length: bytes | int = 0,
        timeout_ms: int = 5000,
    ) -> tuple[int, bytes, tuple[int, int, int]]:
        import ctypes.wintypes

        class SptiWithSense(ctypes.Structure):
            _fields_ = [
                ("spti", ScsiPassThroughDirect),
                ("sense", ctypes.c_ubyte * 32),
            ]

        req = SptiWithSense()
        req.spti.Length = ctypes.sizeof(ScsiPassThroughDirect)
        req.spti.PathId = 0xFF
        req.spti.TargetId = 0xFF
        req.spti.Lun = 0xFF
        req.spti.CdbLength = min(len(cdb), 16)
        for i, b in enumerate(cdb[:16]):
            req.spti.Cdb[i] = b

        req.spti.SenseInfoLength = 32
        req.spti.SenseInfoOffset = ctypes.sizeof(ScsiPassThroughDirect)
        req.spti.TimeOutValue = max(1, timeout_ms // 1000)

        data_buf = None
        if direction == "IN":
            req.spti.DataIn = SCSI_IOCTL_DATA_IN
            read_len = int(data_or_length)
            req.spti.DataTransferLength = read_len
            if read_len > 0:
                data_buf = (ctypes.c_ubyte * read_len)()
                req.spti.DataBuffer = ctypes.cast(data_buf, ctypes.c_void_p)
        elif direction == "OUT":
            req.spti.DataIn = SCSI_IOCTL_DATA_OUT
            out_bytes = (
                bytes(data_or_length) if not isinstance(data_or_length, bytes) else data_or_length
            )
            req.spti.DataTransferLength = len(out_bytes)
            if len(out_bytes) > 0:
                data_buf = (ctypes.c_ubyte * len(out_bytes)).from_buffer_copy(out_bytes)
                req.spti.DataBuffer = ctypes.cast(data_buf, ctypes.c_void_p)
        else:
            req.spti.DataIn = SCSI_IOCTL_DATA_UNSPECIFIED
            req.spti.DataTransferLength = 0

        bytes_returned = ctypes.wintypes.DWORD(0)
        success = self.kernel32.DeviceIoControl(
            self.handle,
            IOCTL_SCSI_PASS_THROUGH_DIRECT,
            ctypes.byref(req),
            ctypes.sizeof(req),
            ctypes.byref(req),
            ctypes.sizeof(req),
            ctypes.byref(bytes_returned),
            None,
        )

        sense = (0, 0, 0)
        if req.spti.ScsiStatus != 0 or not success:
            sb = bytes(req.sense)
            sk = sb[2] & 0x0F if len(sb) > 2 else 0
            asc = sb[12] if len(sb) > 12 else 0
            ascq = sb[13] if len(sb) > 13 else 0
            sense = (sk, asc, ascq)

        data_in = b""
        if direction == "IN" and data_buf is not None:
            data_in = bytes(data_buf[: req.spti.DataTransferLength])

        status = req.spti.ScsiStatus if success else 1
        return status, data_in, sense

    def close(self) -> None:
        if hasattr(self, "handle") and self.handle not in (-1, 0xFFFFFFFF, None):
            self.kernel32.CloseHandle(self.handle)
            self.handle = None
