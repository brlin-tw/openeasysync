# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Link and pipe polling 3-state machine running on a background worker thread."""

import collections
import logging
import queue
import threading
import time
from collections.abc import Callable

from openeasysync.constants import (
    ACTION_IDLE_0,
    ACTION_IDLE_1,
    ACTION_PEER_EVENT,
    ACTION_READY_WRITE_BURST,
    ACTION_READY_WRITE_SINGLE,
    ACTION_REMOTE_BULK,
    ACTION_VDISK_READ,
    ACTION_VDISK_WRITE,
    BULK_BLOCK_SIZE,
)
from openeasysync.transport.base import TransportBackend

logger = logging.getLogger("openeasysync.poller")


class LinkPoller:
    """Manages the 3-state link poll loop (State 0: Poll, State 1: Bulk Write, State 2: Bulk Read)."""

    def __init__(
        self,
        transport: TransportBackend,
        max_blocks_per_burst: int = 16,
        poll_interval_s: float = 0.005,
        on_peer_event: Callable[[], None] | None = None,
        on_activity: Callable[[], None] | None = None,
    ):
        self.transport = transport
        self.max_blocks_per_burst = max_blocks_per_burst
        self.poll_interval_s = poll_interval_s
        self.on_peer_event = on_peer_event
        self.on_activity = on_activity

        self._tx_deque: collections.deque[bytes] = collections.deque()
        self.rx_queue: queue.Queue[bytes] = queue.Queue(maxsize=128)

        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self.state: int = 0  # 0: Poll, 1: Bulk Write, 2: Bulk Read

    @property
    def tx_queue(self):
        """Backward-compatible proxy for inspect/querying tx queue state."""

        class _QueueProxy:
            def __init__(proxy_self):
                pass

            def qsize(proxy_self) -> int:
                with self._lock:
                    return len(self._tx_deque)

            def empty(proxy_self) -> bool:
                with self._lock:
                    return len(self._tx_deque) == 0

            def put(proxy_self, item: bytes) -> None:
                self.enqueue_tx(item)

        return _QueueProxy()

    def start(self) -> None:
        """Starts the background poller thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop, name="OTi-LinkPoller", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stops the poller thread."""
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def enqueue_tx(self, block: bytes) -> None:
        """Enqueues a 64 KB block to be transmitted."""
        if len(block) != BULK_BLOCK_SIZE:
            block = block.ljust(BULK_BLOCK_SIZE, b"\x00")[:BULK_BLOCK_SIZE]
        with self._lock:
            self._tx_deque.append(block)

    def dequeue_rx(self, timeout: float | None = 5.0) -> bytes:
        """Dequeues a received 64 KB block, waiting up to timeout seconds."""
        return self.rx_queue.get(timeout=timeout)

    def _run_loop(self) -> None:
        """Main polling thread execution loop."""
        while self._running:
            try:
                if self.state == 0:
                    self._state_poll()
            except Exception as exc:
                logger.debug("Exception in poller loop: %s", exc)
                time.sleep(self.poll_interval_s)

    def _state_poll(self) -> None:
        """State 0: Issues 0xD8 0x00 op_type=3 and transitions based on action_code."""
        with self._lock:
            tx_count = len(self._tx_deque)
        local_queued = min(tx_count, self.max_blocks_per_burst)
        action_code, remote_blocks, _, _ = self.transport.poll_link(local_queued)

        logger.debug(
            "Poll link: action_code=%d, remote_blocks=%d, tx_queued=%d",
            action_code,
            remote_blocks,
            tx_count,
        )

        if action_code == ACTION_REMOTE_BULK:
            # State 2: Remote has bulk blocks to read
            self.state = 2
            self._state_bulk_read(max(1, remote_blocks))

        elif action_code in (ACTION_READY_WRITE_SINGLE, ACTION_READY_WRITE_BURST):
            # State 1: Bridge explicitly ready for bulk write
            count = (
                1
                if action_code == ACTION_READY_WRITE_SINGLE
                else min(local_queued, self.max_blocks_per_burst)
            )
            if count > 0 and tx_count > 0:
                self.state = 1
                self._state_bulk_write(count)
            else:
                time.sleep(self.poll_interval_s)

        elif action_code in (ACTION_IDLE_0, ACTION_IDLE_1):
            # Idle / Link Ready: if local host has queued TX blocks, initiate write
            # as usbnic.sys does at 0x00011675
            if tx_count > 0:
                count = min(tx_count, self.max_blocks_per_burst)
                if count > 0:
                    self.state = 1
                    self._state_bulk_write(count)
                else:
                    time.sleep(self.poll_interval_s)
            else:
                time.sleep(self.poll_interval_s)

        elif action_code == ACTION_PEER_EVENT:
            if self.on_peer_event:
                self.on_peer_event()
            time.sleep(self.poll_interval_s)

        elif action_code in (ACTION_VDISK_READ, ACTION_VDISK_WRITE):
            # Virtual disk sector I/O handled in full daemon if configured
            time.sleep(self.poll_interval_s)

        else:
            time.sleep(self.poll_interval_s)

    def _state_bulk_write(self, count: int) -> None:
        """State 1: Sends up to `count` 64 KB blocks from TX queue."""
        sent = 0
        try:
            while sent < count:
                with self._lock:
                    if not self._tx_deque:
                        break
                    block = self._tx_deque.popleft()

                logger.debug(
                    "Transmitting bulk block %d/%d (%d bytes)...",
                    sent + 1,
                    count,
                    len(block),
                )
                try:
                    self.transport.write_bulk_block(block)
                    sent += 1
                    if self.on_activity:
                        self.on_activity()
                except Exception as exc:
                    # Do NOT discard the block on write failure; requeue at the front for retry
                    with self._lock:
                        self._tx_deque.appendleft(block)
                    logger.debug(
                        "Transient error during bulk write (%s); requeued block for retry",
                        exc,
                    )
                    time.sleep(self.poll_interval_s)
                    break
        finally:
            self.state = 0

    def _state_bulk_read(self, count: int) -> None:
        """State 2: Reads up to `count` 64 KB blocks into RX queue."""
        read_count = 0
        try:
            while read_count < count:
                logger.debug("Reading bulk block %d/%d...", read_count + 1, count)
                block = self.transport.read_bulk_block()
                self.rx_queue.put(block)
                read_count += 1
                if self.on_activity:
                    self.on_activity()
        except Exception as exc:
            logger.warning("Error during bulk read: %s", exc)
        finally:
            self.state = 0
