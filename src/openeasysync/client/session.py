# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Session manager coordinating hardware initialization, poller, and wire dispatch."""

import logging
import queue
import threading
import time
from collections.abc import Callable

from openeasysync.constants import (
    MODE_OFFLINE,
    MODE_TRANSFER_LINE,
    REPLY_STATUS_ACK,
)
from openeasysync.framing.poller import LinkPoller
from openeasysync.framing.reassembler import PacketReassembler
from openeasysync.framing.splitter import PacketSplitter
from openeasysync.protocol.path_mapping import PathMapper
from openeasysync.server.handler import PeerServerHandler
from openeasysync.transport.base import TransportBackend

logger = logging.getLogger("openeasysync.session")


class SessionManager:
    """Manages the lifecycle of an active OTi-2108 peer-to-peer session."""

    def __init__(
        self,
        transport: TransportBackend,
        path_mapper: PathMapper | None = None,
        on_connection_change: Callable[[bool], None] | None = None,
        on_activity: Callable[[], None] | None = None,
    ):
        self.transport = transport
        self.path_mapper = path_mapper or PathMapper()
        self.on_connection_change = on_connection_change
        self.on_activity = on_activity

        self.server_handler = PeerServerHandler(self.path_mapper)
        self.splitter = PacketSplitter()
        self.reassembler = PacketReassembler()
        self.poller = LinkPoller(
            transport=self.transport,
            on_activity=self._on_internal_activity,
        )

        self._lock = threading.RLock()
        self._is_connected = False
        self._running = False
        self._dispatcher_thread: threading.Thread | None = None
        self._last_rx_time: float = 0.0
        self._last_activity_time: float = 0.0

        # Request-reply coordination: pending reply queue for synchronous client calls
        self._reply_queue: queue.Queue[bytes] = queue.Queue()
        self._streaming_queue: queue.Queue[bytes] = queue.Queue()
        self._active_stream_callback: Callable[[bytes], None] | None = None

    def _on_internal_activity(self) -> None:
        self._last_activity_time = time.time()
        if self.on_activity:
            self.on_activity()

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def connect(
        self,
        timeout_ms: int = 5000,
        settle_delay_s: float = 0.1,
        wait_settle: bool = True,
    ) -> bool:
        """Executes the standard hardware init handshake and XML connect."""
        # 1. 0xF0 Identify
        self.transport.identify()

        # 2. 0x00 TEST UNIT READY
        self.transport.test_unit_ready()

        # 3. 0xD8 0x01 Set Timeout
        self.transport.set_timeout(4000)

        # 4. Read Buffer Size & USB Speed
        self.transport.get_buffer_size()
        self.transport.get_usb_speed()

        # 5. Enter Transfer Line Mode (0x007F)
        self.transport.set_mode(MODE_TRANSFER_LINE)

        # 6. Flush Pipes (CDB 0xD8 0x01 cmd=8 and cmd=7 at 0x0041D014 / 0x0043258B)
        self.transport.flush_pipes()

        # 7. Start Poller and Dispatcher
        self._running = True
        self.poller.start()

        self._dispatcher_thread = threading.Thread(
            target=self._dispatch_loop, name="OTi-SessionDispatcher", daemon=True
        )
        self._dispatcher_thread.start()

        # 8. Settle link and wait for peer startup handshake to complete
        if wait_settle:
            self.wait_settled(quiet_interval_s=0.25, timeout_s=1.5, min_settle_s=settle_delay_s)

        self._is_connected = True
        if self.on_connection_change:
            self.on_connection_change(True)
        return True

    def wait_settled(
        self,
        quiet_interval_s: float = 0.25,
        timeout_s: float = 1.5,
        min_settle_s: float = 0.1,
    ) -> bool:
        """Waits for link and peer initialization activity to settle.

        Monitors the link until no incoming peer commands have arrived for at
        least `quiet_interval_s`, or until `timeout_s` expires. A minimum
        delay of `min_settle_s` is observed to allow peer detection.
        """
        start = time.time()
        if min_settle_s > 0:
            time.sleep(min_settle_s)

        while time.time() - start < timeout_s:
            with self.poller._lock:
                tx_pending = len(self.poller._tx_deque)
            rx_pending = self.poller.rx_queue.qsize()

            time_since_last_rx = time.time() - self._last_rx_time

            if tx_pending == 0 and rx_pending == 0 and time_since_last_rx >= quiet_interval_s:
                return True
            time.sleep(0.05)

        return True

    def disconnect(self) -> None:
        """Cleanly disconnects the session."""
        if not self._running:
            return

        self._running = False
        self._is_connected = False
        if self.on_connection_change:
            self.on_connection_change(False)

        # 1. Stop background poller and dispatcher threads before altering hardware mode
        self.poller.stop()
        if self._dispatcher_thread and self._dispatcher_thread.is_alive():
            self._dispatcher_thread.join(timeout=1.0)

        # 2. Put hardware bridge into offline mode
        try:
            self.transport.set_mode(MODE_OFFLINE)
        except Exception:
            pass

        # 3. Close transport handle
        self.transport.close()

    def send_command(
        self,
        cmd_payload: bytes,
        timeout: float = 10.0,
        expected_opcodes: tuple[int, ...] | None = None,
    ) -> bytes:
        """Sends a Client Command and synchronously waits for the Server Reply."""
        with self._lock:
            # Drain any stale replies in reply queue
            while not self._reply_queue.empty():
                try:
                    self._reply_queue.get_nowait()
                except queue.Empty:
                    break

            # If peer sent a command recently, wait briefly for our reply to transmit
            # and link to stabilize to prevent half-duplex collisions
            elapsed = time.time() - self._last_rx_time
            if elapsed < 0.1:
                time.sleep(0.1 - elapsed)

            # Split logical command into 64 KB blocks
            blocks = self.splitter.split_message(cmd_payload)
            logger.debug(
                "Enqueuing command opcode %#04x (%d bytes across %d blocks)",
                cmd_payload[0],
                len(cmd_payload),
                len(blocks),
            )
            for block in blocks:
                self.poller.enqueue_tx(block)

            # Wait for Server Reply
            start_time = time.time()
            while time.time() - start_time < timeout:
                remaining = timeout - (time.time() - start_time)
                if remaining <= 0:
                    break
                try:
                    reply = self._reply_queue.get(timeout=remaining)
                    opcode = reply[0]
                    # If an intermediate ACK was received but caller expects a specific data reply
                    if (
                        expected_opcodes
                        and opcode == REPLY_STATUS_ACK
                        and REPLY_STATUS_ACK not in expected_opcodes
                    ):
                        logger.debug(
                            "Received intermediate ACK %#04x, continuing to wait for %s...",
                            opcode,
                            [f"{op:#04x}" for op in expected_opcodes],
                        )
                        continue
                    return reply
                except queue.Empty:
                    break

            raise TimeoutError(f"Timeout waiting for reply to opcode {cmd_payload[0]:#04x}")

    def send_command_multi_reply(self, cmd_payload: bytes, timeout: float = 15.0) -> list[bytes]:
        """Sends a command that expects multiple reply messages (e.g. 0x0C Batch Download)."""
        with self._lock:
            while not self._reply_queue.empty():
                try:
                    self._reply_queue.get_nowait()
                except queue.Empty:
                    break

            blocks = self.splitter.split_message(cmd_payload)
            for block in blocks:
                self.poller.enqueue_tx(block)

            replies: list[bytes] = []
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    rep = self._reply_queue.get(timeout=2.0)
                    replies.append(rep)
                    opcode = rep[0]
                    # Opcode 0xA7 indicates batch completion; 0xA1, 0xA2, 0xA9 indicate end
                    if opcode in (0xA7, 0xA1, 0xA2, 0xA9):
                        break
                except queue.Empty:
                    if replies:
                        break
                    continue
            return replies

    def _dispatch_loop(self) -> None:
        """Background thread dequeuing RX blocks from poller and routing messages."""
        while self._running:
            try:
                block = self.poller.dequeue_rx(timeout=0.1)
            except queue.Empty:
                continue
            except Exception:
                continue

            try:
                complete_msg = self.reassembler.feed_block(block)
                if complete_msg is not None:
                    self._route_message(complete_msg)
            except Exception:
                self.reassembler.reset()

    def _route_message(self, message: bytes) -> None:
        """Routes a complete logical message."""
        if not message:
            return

        self._last_rx_time = time.time()
        opcode = message[0]
        logger.debug("Received complete wire message: opcode=%#04x, len=%d", opcode, len(message))

        # 1. Server Reply (0xA1..0xAC) -> routes to client waiting thread
        if 0xA1 <= opcode <= 0xAC:
            self._reply_queue.put(message)
            return

        # 2. Client Command (0x01..0x39) -> services request via PeerServerHandler
        replies = self.server_handler.handle_command(message)
        for rep in replies:
            reply_blocks = self.splitter.split_message(rep)
            for b in reply_blocks:
                self.poller.enqueue_tx(b)
