# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Linux SG_IO SCSI pass-through transport for OTi-2108 (/dev/sgX)."""

import ctypes
import fcntl
import os
from typing import Literal

from openeasysync.transport.base import TransportBackend

SG_IO = 0x2285
SG_DXFER_NONE = -1
SG_DXFER_TO_DEV = -2
SG_DXFER_FROM_DEV = -3


class SgIoHdr(ctypes.Structure):
    _fields_ = [
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    ]


class LinuxSgIoTransport(TransportBackend):
    """Linux SCSI generic pass-through transport."""

    def __init__(self, device_path: str):
        self.device_path = device_path
        self.fd = os.open(device_path, os.O_RDWR | os.O_NONBLOCK)
        self._has_lock = False
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._has_lock = True
        except OSError as exc:
            os.close(self.fd)
            self.fd = -1
            raise OSError(f"Device {device_path} is already in use by another process") from exc

    def execute_cdb(
        self,
        cdb: bytes,
        direction: Literal["NONE", "IN", "OUT"],
        data_or_length: bytes | int = 0,
        timeout_ms: int = 5000,
    ) -> tuple[int, bytes, tuple[int, int, int]]:
        cdb_buf = (ctypes.c_ubyte * len(cdb)).from_buffer_copy(cdb)
        sense_buf = (ctypes.c_ubyte * 64)()

        if direction == "IN":
            read_len = int(data_or_length)
            dxfer_dir = SG_DXFER_FROM_DEV
            data_buf = (ctypes.c_ubyte * read_len)() if read_len > 0 else None
            dxfer_len = read_len
            dxfer_ptr = ctypes.cast(data_buf, ctypes.c_void_p) if data_buf else None
        elif direction == "OUT":
            out_bytes = (
                bytes(data_or_length) if not isinstance(data_or_length, bytes) else data_or_length
            )
            dxfer_dir = SG_DXFER_TO_DEV
            dxfer_len = len(out_bytes)
            data_buf = (
                (ctypes.c_ubyte * dxfer_len).from_buffer_copy(out_bytes) if dxfer_len > 0 else None
            )
            dxfer_ptr = ctypes.cast(data_buf, ctypes.c_void_p) if data_buf else None
        else:
            dxfer_dir = SG_DXFER_NONE
            dxfer_len = 0
            dxfer_ptr = None
            data_buf = None

        hdr = SgIoHdr()
        hdr.interface_id = ord("S")
        hdr.dxfer_direction = dxfer_dir
        hdr.cmd_len = len(cdb)
        hdr.mx_sb_len = ctypes.sizeof(sense_buf)
        hdr.dxfer_len = dxfer_len
        hdr.dxferp = dxfer_ptr
        hdr.cmdp = ctypes.cast(cdb_buf, ctypes.c_void_p)
        hdr.sbp = ctypes.cast(sense_buf, ctypes.c_void_p)
        hdr.timeout = timeout_ms

        fcntl.ioctl(self.fd, SG_IO, hdr)

        sense = (0, 0, 0)
        if hdr.sb_len_wr > 0:
            sb = bytes(sense_buf[: hdr.sb_len_wr])
            sk = sb[2] & 0x0F if len(sb) > 2 else 0
            asc = sb[12] if len(sb) > 12 else 0
            ascq = sb[13] if len(sb) > 13 else 0
            sense = (sk, asc, ascq)

        data_in = b""
        if direction == "IN" and data_buf is not None:
            actual_len = max(0, dxfer_len - hdr.resid) if 0 <= hdr.resid <= dxfer_len else dxfer_len
            data_in = bytes(data_buf[:actual_len])

        return hdr.status, data_in, sense

    def close(self) -> None:
        if self.fd >= 0:
            if getattr(self, "_has_lock", False):
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_UN)
                except Exception:
                    pass
            os.close(self.fd)
            self.fd = -1
