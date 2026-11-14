# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Abstract base class for OTi-2108 transport backends."""

import struct
from abc import ABC, abstractmethod
from typing import Literal

from openeasysync.constants import (
    BULK_BLOCK_SIZE,
    CMD_FLUSH_RX,
    CMD_FLUSH_TX,
    CMD_SET_MODE,
    CMD_SET_TIMEOUT,
    CSW_STATUS_PASSED,
    MODE_TRANSFER_LINE,
    OPTYPE_BUFFER_SIZE,
    OPTYPE_LINK_POLL,
    OPTYPE_PEER_MODE,
    OPTYPE_USB_SPEED,
    decode_sense_code,
)


class TransportBackend(ABC):
    """Abstract base class for executing OTi-2108 CDBs across hardware/mock transports."""

    @abstractmethod
    def execute_cdb(
        self,
        cdb: bytes,
        direction: Literal["NONE", "IN", "OUT"],
        data_or_length: bytes | int = 0,
        timeout_ms: int = 5000,
    ) -> tuple[int, bytes, tuple[int, int, int]]:
        """Executes a SCSI/OTi CDB.

        Args:
            cdb: Command Descriptor Block (6, 10, or 16 bytes).
            direction: Data stage direction: "NONE", "IN", or "OUT".
            data_or_length: Number of bytes to read (for IN) or bytes to write (for OUT).
            timeout_ms: Timeout in milliseconds.

        Returns:
            Tuple of:
            - status (int): 0 on success, non-zero on failure.
            - data_in (bytes): Data received during IN stage.
            - sense (tuple[int, int, int]): (sense_key, asc, ascq).
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Closes the transport and releases any resources or interfaces."""
        pass

    def __enter__(self) -> "TransportBackend":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # Convenience high-level OTi-2108 CDB helpers

    def identify(self, timeout_ms: int = 2000) -> dict:
        """Issues CDB 0xF0 (Identify OTi-2108)."""
        cdb = bytes([0xF0] + [0x00] * 13 + [0x4F, 0x54])
        status, data, sense = self.execute_cdb(cdb, "IN", 12, timeout_ms=timeout_ms)
        if status != CSW_STATUS_PASSED:
            _, _, desc = decode_sense_code(*sense)
            raise OSError(f"Identify failed: status={status}, sense={sense} ({desc})")

        if len(data) < 12 or data[0:5] != b"\x00\x00\x21\x08\x00":
            raise ValueError(f"Invalid OTi-2108 signature: {data.hex()}")

        fw_rev = data[5]
        fw_date = data[6:12].decode("ascii", errors="replace")
        return {
            "chip_id": "OTi-2108",
            "fw_rev": fw_rev,
            "fw_date": fw_date,
            "extended_cap": (fw_rev > 2),
        }

    def test_unit_ready(self, timeout_ms: int = 2000) -> bool:
        """Issues SCSI 0x00 TEST UNIT READY. Returns True if ready (or Medium Not Present)."""
        cdb = bytes([0x00] * 6)
        status, _, sense = self.execute_cdb(cdb, "NONE", 0, timeout_ms=timeout_ms)
        if status == CSW_STATUS_PASSED:
            return True
        # Medium Not Present (0x02, 0x3A, 0x00) is treated as normal for OTi-2108 bridge
        if sense[0] == 0x02 and sense[1] == 0x3A:
            return True
        return False

    def read_status(self, op_type: int, param: int = 0, timeout_ms: int = 4000) -> bytes:
        """Issues CDB 0xD8 0x00 (Read Bridge Register / Poll Status)."""
        param_bytes = struct.pack(">H", param & 0xFFFF)
        cdb = bytes([0xD8, 0x00, op_type, param_bytes[0], param_bytes[1]]) + bytes(9) + b"OT"
        # On wire, 2 bytes for op_type 1,2,5 or 7 bytes for op_type 3; request 8 bytes
        status, data, sense = self.execute_cdb(cdb, "IN", 8, timeout_ms=timeout_ms)
        if status != CSW_STATUS_PASSED:
            _, _, desc = decode_sense_code(*sense)
            raise OSError(f"Read status op_type={op_type} failed: status={status}, {desc}")
        return data

    def write_command(self, cmd: int, val: int = 0, seq: int = 0, timeout_ms: int = 4000) -> None:
        """Issues CDB 0xD8 0x01 (Write Bridge Command / Operating Mode)."""
        val_bytes = struct.pack(">H", val & 0xFFFF)
        seq_bytes = struct.pack(">I", seq & 0xFFFFFFFF)
        cdb = bytes([0xD8, 0x01, cmd, val_bytes[0], val_bytes[1]]) + seq_bytes + bytes(5) + b"OT"
        status, _, sense = self.execute_cdb(cdb, "NONE", 0, timeout_ms=timeout_ms)
        if status != CSW_STATUS_PASSED:
            _, _, desc = decode_sense_code(*sense)
            raise OSError(f"Write command cmd={cmd} failed: status={status}, {desc}")

    def poll_link(
        self, local_queued_blocks: int, timeout_ms: int = 4000
    ) -> tuple[int, int, int, int]:
        """Polls link via CDB 0xD8 0x00 (op_type=3).

        Returns: (action_code, remote_queued_blocks, virtual_lba, sector_count).
        """
        data = self.read_status(OPTYPE_LINK_POLL, param=local_queued_blocks, timeout_ms=timeout_ms)
        action_code = data[0] if len(data) > 0 else 0
        remote_queued = struct.unpack(">H", data[1:3])[0] if len(data) >= 3 else 0
        virtual_lba = struct.unpack(">I", data[1:5])[0] if len(data) >= 5 else 0
        sector_count = struct.unpack(">H", data[5:7])[0] if len(data) >= 7 else 0
        return action_code, remote_queued, virtual_lba, sector_count

    def get_usb_speed(self, timeout_ms: int = 2000) -> tuple[int, int]:
        """Queries USB port speeds (local_speed, peer_speed)."""
        data = self.read_status(OPTYPE_USB_SPEED, timeout_ms=timeout_ms)
        local_spd = data[0] if len(data) > 0 else 0
        peer_spd = data[1] if len(data) > 1 else 0
        return local_spd, peer_spd

    def get_buffer_size(self, timeout_ms: int = 2000) -> int:
        """Queries bridge buffer size."""
        data = self.read_status(OPTYPE_BUFFER_SIZE, timeout_ms=timeout_ms)
        code = data[1] if len(data) > 1 else 4
        size_map = {
            1: 256 * 1024,
            2: 256 * 1024,
            3: 512 * 1024,
            4: 512 * 1024,
            5: 1024 * 1024,
            6: 2048 * 1024,
        }
        return size_map.get(code, 512 * 1024)

    def get_peer_mode(self, timeout_ms: int = 2000) -> int:
        """Queries peer operating mode (0x007F, 0x0044, or 0x0000)."""
        data = self.read_status(OPTYPE_PEER_MODE, timeout_ms=timeout_ms)
        if len(data) >= 2:
            return struct.unpack(">H", data[0:2])[0]
        return 0

    def set_mode(self, mode: int = MODE_TRANSFER_LINE, timeout_ms: int = 4000) -> None:
        """Sets local operating mode."""
        self.write_command(CMD_SET_MODE, val=mode, timeout_ms=timeout_ms)

    def set_timeout(self, timeout_ms_val: int = 4000) -> None:
        """Sets hardware timeout in ms."""
        self.write_command(CMD_SET_TIMEOUT, val=timeout_ms_val)

    def flush_pipes(self) -> None:
        """Flushes TX and RX hardware pipes."""
        try:
            self.write_command(CMD_FLUSH_RX, val=0)
        except Exception:
            pass
        try:
            self.write_command(CMD_FLUSH_TX, val=0)
        except Exception:
            pass

    def read_bulk_block(
        self,
        length: int = BULK_BLOCK_SIZE,
        param: int = 0x64,
        seq: int = 0,
        timeout_ms: int = 10000,
    ) -> bytes:
        """Reads a bulk block from the bridge (CDB 0xD9 0x28)."""
        seq_bytes = struct.pack(">I", seq)
        cdb = bytes([0xD9, 0x28, param]) + seq_bytes + bytes(7) + b"OT"
        status, data, sense = self.execute_cdb(cdb, "IN", length, timeout_ms=timeout_ms)
        if status != CSW_STATUS_PASSED:
            _, _, desc = decode_sense_code(*sense)
            raise OSError(f"Bulk read failed: status={status}, {desc}")
        return data

    def write_bulk_block(self, data: bytes, param: int = 0xFF, timeout_ms: int = 10000) -> None:
        """Writes a bulk block to the bridge (CDB 0xD9 0x2A)."""
        if len(data) != BULK_BLOCK_SIZE:
            # Pad or truncate to BULK_BLOCK_SIZE
            if len(data) < BULK_BLOCK_SIZE:
                data = data + bytes(BULK_BLOCK_SIZE - len(data))
            else:
                data = data[:BULK_BLOCK_SIZE]

        cdb = bytes([0xD9, 0x2A, param]) + bytes(11) + b"OT"
        status, _, sense = self.execute_cdb(cdb, "OUT", data, timeout_ms=timeout_ms)
        if status != CSW_STATUS_PASSED:
            _, _, desc = decode_sense_code(*sense)
            raise OSError(f"Bulk write failed: status={status}, {desc}")
