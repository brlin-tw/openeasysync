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
            on_activity=self.on_activity,
        )

        self._lock = threading.RLock()
        self._is_connected = False
        self._running = False
        self._dispatcher_thread: threading.Thread | None = None

        # Request-reply coordination: pending reply queue for synchronous client calls
        self._reply_queue: queue.Queue[bytes] = queue.Queue()
        self._streaming_queue: queue.Queue[bytes] = queue.Queue()
        self._active_stream_callback: Callable[[bytes], None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def connect(self, timeout_ms: int = 5000) -> bool:
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

        # 5. Flush Pipes
        self.transport.flush_pipes()

        # 6. Enter Transfer Line Mode (0x007F)
        self.transport.set_mode(MODE_TRANSFER_LINE)
        time.sleep(0.05)

        # 7. Start Poller and Dispatcher
        self._running = True
        self.poller.start()

        self._dispatcher_thread = threading.Thread(
            target=self._dispatch_loop, name="OTi-SessionDispatcher", daemon=True
        )
        self._dispatcher_thread.start()

        self._is_connected = True
        if self.on_connection_change:
            self.on_connection_change(True)
        return True

    def disconnect(self) -> None:
        """Cleanly disconnects the session."""
        if not self._running:
            return

        try:
            self.transport.set_mode(MODE_OFFLINE)
        except Exception:
            pass

        self._running = False
        self._is_connected = False
        if self.on_connection_change:
            self.on_connection_change(False)

        self.poller.stop()
        if self._dispatcher_thread and self._dispatcher_thread.is_alive():
            self._dispatcher_thread.join(timeout=1.0)

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
