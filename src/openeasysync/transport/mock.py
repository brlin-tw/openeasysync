# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dual-host in-memory mock loopback transport for offline unit and integration tests."""

import queue
import struct
import threading
from typing import Literal

from openeasysync.constants import (
    ACTION_IDLE_1,
    ACTION_READY_WRITE_BURST,
    ACTION_READY_WRITE_SINGLE,
    ACTION_REMOTE_BULK,
    BULK_BLOCK_SIZE,
    CMD_FLUSH_RX,
    CMD_FLUSH_TX,
    CMD_SET_MODE,
    CMD_SET_TIMEOUT,
    CSW_STATUS_FAILED,
    CSW_STATUS_PASSED,
    MODE_TRANSFER_LINE,
    OPTYPE_BUFFER_SIZE,
    OPTYPE_LINK_POLL,
    OPTYPE_PEER_MODE,
    OPTYPE_USB_SPEED,
    SPEED_USB20,
)
from openeasysync.transport.base import TransportBackend


class MockBridgeCable:
    """Simulates the physical OTi-2108 dual-host hardware bridge."""

    def __init__(
        self,
        speed_a: int = SPEED_USB20,
        speed_b: int = SPEED_USB20,
        initial_mode_a: int = MODE_TRANSFER_LINE,
        initial_mode_b: int = MODE_TRANSFER_LINE,
        fw_rev: int = 3,
        buffer_size_code: int = 4,
        custom_buffer_size: int | None = None,
    ):
        self.lock = threading.RLock()
        self.speed = {"A": speed_a, "B": speed_b}
        self.mode = {"A": initial_mode_a, "B": initial_mode_b}
        self.timeout = {"A": 4000, "B": 4000}
        self.fw_rev = fw_rev
        self.buffer_size_code = buffer_size_code  # default 4 = 512 KB
        self.custom_buffer_size = custom_buffer_size

        # FIFO queues: A -> B and B -> A
        self.queue_a_to_b: queue.Queue[bytes] = queue.Queue(maxsize=32)
        self.queue_b_to_a: queue.Queue[bytes] = queue.Queue(maxsize=32)

        self.transport_a: MockTransport | None = None
        self.transport_b: MockTransport | None = None

    def create_pair(self) -> tuple["MockTransport", "MockTransport"]:
        """Creates and returns the paired (Host A, Host B) transport endpoints."""
        self.transport_a = MockTransport(self, "A")
        self.transport_b = MockTransport(self, "B")
        return self.transport_a, self.transport_b


class MockTransport(TransportBackend):
    """One host endpoint of a simulated OTi-2108 mock bridge."""

    def __init__(self, bridge: MockBridgeCable, side: Literal["A", "B"]):
        self.bridge = bridge
        self.side = side
        self.peer_side: Literal["A", "B"] = "B" if side == "A" else "A"
        self._is_closed = False

    @property
    def in_queue(self) -> queue.Queue[bytes]:
        return self.bridge.queue_b_to_a if self.side == "A" else self.bridge.queue_a_to_b

    @property
    def out_queue(self) -> queue.Queue[bytes]:
        return self.bridge.queue_a_to_b if self.side == "A" else self.bridge.queue_b_to_a

    def execute_cdb(
        self,
        cdb: bytes,
        direction: Literal["NONE", "IN", "OUT"],
        data_or_length: bytes | int = 0,
        timeout_ms: int = 5000,
    ) -> tuple[int, bytes, tuple[int, int, int]]:
        if self._is_closed:
            return CSW_STATUS_FAILED, b"", (0x09, 0x89, 0x00)  # Link lost

        opcode = cdb[0]

        # 1. SCSI 0x00 TEST UNIT READY
        if opcode == 0x00:
            return CSW_STATUS_PASSED, b"", (0, 0, 0)

        # 2. 0xF0 Identify OTi-2108
        if opcode == 0xF0:
            # 12-byte response: Chip 2108, configurable fw_rev (default 3), date "030729"
            resp = b"\x00\x00\x21\x08\x00" + bytes([self.bridge.fw_rev & 0xFF]) + b"030729"
            return CSW_STATUS_PASSED, resp, (0, 0, 0)

        # 3. 0xD8 0x00 Read Register / Poll
        if opcode == 0xD8 and cdb[1] == 0x00:
            op_type = cdb[2]
            with self.bridge.lock:
                if op_type == OPTYPE_BUFFER_SIZE:
                    resp = bytes([0x00, self.bridge.buffer_size_code])
                elif op_type == OPTYPE_USB_SPEED:
                    local_spd = self.bridge.speed[self.side]
                    peer_spd = self.bridge.speed[self.peer_side]
                    resp = bytes([local_spd, peer_spd])
                elif op_type == OPTYPE_PEER_MODE:
                    peer_m = self.bridge.mode[self.peer_side]
                    resp = struct.pack(">H", peer_m)
                elif op_type == OPTYPE_LINK_POLL:
                    local_queued = (cdb[3] << 8) | cdb[4]
                    in_count = self.in_queue.qsize()

                    if in_count > 0:
                        # Peer has sent bulk blocks to read
                        action_code = ACTION_REMOTE_BULK
                        resp = bytes([action_code]) + struct.pack(">H", in_count) + bytes(4)
                    elif local_queued > 0:
                        # Local host wants to send and bridge has space
                        action_code = (
                            ACTION_READY_WRITE_BURST
                            if local_queued > 1
                            else ACTION_READY_WRITE_SINGLE
                        )
                        resp = bytes([action_code]) + bytes(6)
                    else:
                        action_code = ACTION_IDLE_1
                        resp = bytes([action_code]) + bytes(6)
                else:
                    resp = bytes(8)

            req_len = int(data_or_length) if direction == "IN" else len(resp)
            return CSW_STATUS_PASSED, resp[:req_len], (0, 0, 0)

        # 4. 0xD8 0x01 Write Command / Mode
        if opcode == 0xD8 and cdb[1] == 0x01:
            cmd = cdb[2]
            val = (cdb[3] << 8) | cdb[4]
            with self.bridge.lock:
                if cmd == CMD_SET_MODE:
                    self.bridge.mode[self.side] = val
                elif cmd == CMD_SET_TIMEOUT:
                    self.bridge.timeout[self.side] = val
                elif cmd == CMD_FLUSH_TX:
                    while not self.out_queue.empty():
                        try:
                            self.out_queue.get_nowait()
                        except queue.Empty:
                            break
                elif cmd == CMD_FLUSH_RX:
                    while not self.in_queue.empty():
                        try:
                            self.in_queue.get_nowait()
                        except queue.Empty:
                            break
            return CSW_STATUS_PASSED, b"", (0, 0, 0)

        # 5. 0xD9 0x2A Bulk Pipe Write
        if opcode == 0xD9 and cdb[1] == 0x2A:
            data = (
                bytes(data_or_length) if not isinstance(data_or_length, bytes) else data_or_length
            )
            if len(data) != BULK_BLOCK_SIZE:
                data = data.ljust(BULK_BLOCK_SIZE, b"\x00")[:BULK_BLOCK_SIZE]
            try:
                self.out_queue.put(data, timeout=timeout_ms / 1000.0)
                return CSW_STATUS_PASSED, b"", (0, 0, 0)
            except queue.Full:
                return CSW_STATUS_FAILED, b"", (0x09, 0x82, 0x00)  # Buffer Overflow / Busy

        # 6. 0xD9 0x28 Bulk Pipe Read
        if opcode == 0xD9 and cdb[1] == 0x28:
            read_len = int(data_or_length)
            try:
                block = self.in_queue.get(timeout=timeout_ms / 1000.0)
                return CSW_STATUS_PASSED, block[:read_len], (0, 0, 0)
            except queue.Empty:
                return CSW_STATUS_FAILED, b"", (0x09, 0x81, 0x00)  # Peer Timeout

        # 7. 0xDB 0x28 Vendor Flash / Config Sector Read
        if opcode == 0xDB and cdb[1] == 0x28:
            addr = (cdb[4] << 8) | cdb[5]
            cnt = (cdb[7] << 8) | cdb[8]
            total_len = max(1, cnt) * 512
            buf = bytearray(total_len)
            with self.bridge.lock:
                if addr == 0x0022 and self.bridge.custom_buffer_size is not None:
                    buf[0:7] = b"OTiMing"
                    struct.pack_into("<I", buf, 8, self.bridge.custom_buffer_size)
            req_len = int(data_or_length) if direction == "IN" else total_len
            return CSW_STATUS_PASSED, bytes(buf[:req_len]), (0, 0, 0)

        # Unknown CDB
        return CSW_STATUS_FAILED, b"", (0x05, 0x20, 0x00)  # Invalid opcode

    def close(self) -> None:
        self._is_closed = True
