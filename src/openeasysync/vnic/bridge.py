# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Virtual NIC (mode 0x0044) user-space poll loop and TAP bridge."""

import logging
import threading
import time

from openeasysync.constants import (
    ACTION_IDLE_0,
    ACTION_IDLE_1,
    ACTION_PEER_EVENT,
    ACTION_READY_WRITE_BURST,
    ACTION_READY_WRITE_SINGLE,
    ACTION_REMOTE_BULK,
    MODE_OFFLINE,
    MODE_VIRTUAL_NIC,
    VNIC_BLOCK_SIZE,
)
from openeasysync.framing.vnic import (
    NDIS_PACKET_TYPE_DEFAULT,
    VNIC_MAX_FRAMES_PER_BLOCK,
    decode_vnic_batch,
    encode_vnic_batches,
    matches_packet_filter,
)
from openeasysync.transport.base import TransportBackend
from openeasysync.vnic.tap import TapDevice

logger = logging.getLogger("openeasysync.vnic")


class VirtualNicBridge:
    """Bridges Ethernet frames between a TapDevice and an OTi-2108 TransportBackend."""

    def __init__(
        self,
        transport: TransportBackend,
        tap: TapDevice,
        *,
        poll_interval_s: float = 0.01,
        packet_filter: int = NDIS_PACKET_TYPE_DEFAULT,
        apply_packet_filter: bool = True,
    ):
        self.transport = transport
        self.tap = tap
        self.poll_interval_s = poll_interval_s
        self.packet_filter = packet_filter
        self.apply_packet_filter = apply_packet_filter

        self._running = False
        self._thread: threading.Thread | None = None
        self._pending_tx: list[bytes] = []

        self.tx_packets = 0
        self.rx_packets = 0
        self.tx_bytes = 0
        self.rx_bytes = 0
        self.peer_nic_active = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, enter_mode: bool = True) -> None:
        """Enters Virtual NIC mode (0x0044) if requested and starts the poll loop."""
        if self._running:
            return
        if enter_mode:
            self.transport.set_mode(MODE_VIRTUAL_NIC)
            self.transport.flush_pipes()
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="OTi-VirtualNIC", daemon=True
        )
        self._thread.start()

    def stop(self, restore_mode: int | None = MODE_OFFLINE, timeout: float = 2.0) -> None:
        """Stops the Virtual NIC poll loop and optionally restores bridge mode."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if restore_mode is not None:
            try:
                self.transport.set_mode(restore_mode)
            except Exception:
                pass

    def _collect_tx_frames(self) -> None:
        """Drains up to VNIC_MAX_FRAMES_PER_BLOCK outbound frames from the TAP."""
        while len(self._pending_tx) < VNIC_MAX_FRAMES_PER_BLOCK:
            timeout = 0.0 if self._pending_tx else 0.001
            frame = self.tap.read_frame(timeout=timeout)
            if frame is None:
                break
            self._pending_tx.append(frame)

    def _flush_tx_batch(self) -> None:
        """Encodes pending TX frames into 0x8A00 block(s) and writes to the bridge."""
        if not self._pending_tx:
            return
        batch = self._pending_tx[:VNIC_MAX_FRAMES_PER_BLOCK]
        blocks = encode_vnic_batches(batch)
        for block in blocks:
            self.transport.write_bulk_block(block)
        self.tx_packets += len(batch)
        self.tx_bytes += sum(len(f) for f in batch)
        del self._pending_tx[: len(batch)]

    def _read_rx_batches(self, count: int) -> None:
        """Reads `count` 0x8A00 RX blocks and delivers matching frames to the TAP."""
        for _ in range(max(1, count)):
            block = self.transport.read_bulk_block(length=VNIC_BLOCK_SIZE)
            frames = decode_vnic_batch(block, strict=False)
            for frame in frames:
                if self.apply_packet_filter and not matches_packet_filter(
                    frame, self.tap.mac_address, self.packet_filter
                ):
                    continue
                self.tap.write_frame(frame)
                self.rx_packets += 1
                self.rx_bytes += len(frame)

    def step(self) -> None:
        """Executes one poll-and-transfer iteration (matches usbnic.sys 0x00011614)."""
        self._collect_tx_frames()
        has_tx = 1 if self._pending_tx else 0
        action_code, remote_blocks, _, _ = self.transport.poll_link(has_tx)

        if action_code == ACTION_REMOTE_BULK:
            self._read_rx_batches(remote_blocks)
        elif action_code in (
            ACTION_READY_WRITE_SINGLE,
            ACTION_READY_WRITE_BURST,
            ACTION_IDLE_0,
            ACTION_IDLE_1,
        ):
            if self._pending_tx:
                self._flush_tx_batch()
            else:
                time.sleep(self.poll_interval_s)
        elif action_code == ACTION_PEER_EVENT:
            try:
                self.peer_nic_active = self.transport.get_peer_mode() == MODE_VIRTUAL_NIC
            except Exception:
                pass
            time.sleep(self.poll_interval_s)
        else:
            time.sleep(self.poll_interval_s)

    def _run_loop(self) -> None:
        while self._running:
            try:
                self.step()
            except Exception as exc:
                logger.debug("Virtual NIC loop error: %s", exc)
                time.sleep(self.poll_interval_s)
