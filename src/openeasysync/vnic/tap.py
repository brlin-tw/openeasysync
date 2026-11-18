# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Virtual Ethernet TAP device abstractions (in-memory for testing, Linux /dev/net/tun)."""

import os
import queue
import select
import struct
import sys
from abc import ABC, abstractmethod

from openeasysync.framing.vnic import VNIC_MAX_FRAME_LEN, generate_vnic_mac

# Linux <linux/if_tun.h> ioctl constants
TUNSETIFF = 0x400454CA
IFF_TAP = 0x0002
IFF_NO_PI = 0x1000


class TapDevice(ABC):
    """Abstract Layer-2 TAP interface for Virtual NIC Ethernet frame I/O."""

    @property
    @abstractmethod
    def mac_address(self) -> bytes:
        """6-byte IEEE 802.3 MAC address of the local virtual adapter."""

    @abstractmethod
    def read_frame(self, timeout: float = 0.01) -> bytes | None:
        """Reads one outbound Ethernet frame from the host, or None on timeout."""

    @abstractmethod
    def write_frame(self, frame: bytes) -> None:
        """Delivers one inbound Ethernet frame from the bridge to the host."""

    @abstractmethod
    def close(self) -> None:
        """Closes the TAP device and releases resources."""

    def __enter__(self) -> "TapDevice":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class InMemoryTapDevice(TapDevice):
    """Queue-backed in-memory TAP device for rootless unit and loopback tests."""

    def __init__(self, mac_address: bytes | None = None, maxsize: int = 256):
        self._mac = mac_address if mac_address is not None else generate_vnic_mac()
        self.tx_queue: queue.Queue[bytes] = queue.Queue(maxsize=maxsize)
        self.rx_queue: queue.Queue[bytes] = queue.Queue(maxsize=maxsize)
        self._closed = False

    @property
    def mac_address(self) -> bytes:
        return self._mac

    def read_frame(self, timeout: float = 0.01) -> bytes | None:
        if self._closed:
            return None
        try:
            return self.tx_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def write_frame(self, frame: bytes) -> None:
        if self._closed:
            return
        self.rx_queue.put_nowait(bytes(frame))

    def inject_outbound_frame(self, frame: bytes) -> None:
        """Simulates the host OS transmitting an Ethernet frame into the TAP."""
        self.tx_queue.put(bytes(frame))

    def pop_inbound_frame(self, timeout: float = 1.0) -> bytes:
        """Retrieves an Ethernet frame delivered to the host OS from the bridge."""
        return self.rx_queue.get(timeout=timeout)

    def close(self) -> None:
        self._closed = True


class LinuxTapDevice(TapDevice):
    """Linux /dev/net/tun Layer-2 TAP device (IFF_TAP | IFF_NO_PI)."""

    def __init__(
        self,
        ifname: str = "oti0",
        mac_address: bytes | None = None,
        tun_path: str = "/dev/net/tun",
    ):
        if sys.platform != "linux":
            raise NotImplementedError("LinuxTapDevice is only supported on Linux")

        import fcntl

        self._mac = mac_address if mac_address is not None else generate_vnic_mac(ifname.encode())
        self._fd = os.open(tun_path, os.O_RDWR)
        try:
            ifname_bytes = ifname.encode("ascii")[:15]
            ifr = struct.pack("16sH14s", ifname_bytes, IFF_TAP | IFF_NO_PI, b"")
            res = fcntl.ioctl(self._fd, TUNSETIFF, ifr)
            self.ifname = res[:16].split(b"\x00", 1)[0].decode("ascii", errors="replace")
        except Exception:
            os.close(self._fd)
            raise

    @property
    def mac_address(self) -> bytes:
        return self._mac

    def read_frame(self, timeout: float = 0.01) -> bytes | None:
        if self._fd < 0:
            return None
        readable, _, _ = select.select([self._fd], [], [], max(0.0, timeout))
        if not readable:
            return None
        data = os.read(self._fd, VNIC_MAX_FRAME_LEN + 64)
        return data if data else None

    def write_frame(self, frame: bytes) -> None:
        if self._fd < 0:
            return
        os.write(self._fd, frame)

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
