# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Device detection and enumeration for OTi-2108 bridges."""

import glob
import sys

from openeasysync.constants import USB_PID_2108, USB_VID_OTI
from openeasysync.transport.base import TransportBackend


def find_usb_device() -> TransportBackend | None:
    """Detects OTi-2108 device via PyUSB / libusb."""
    try:
        from openeasysync.transport.usb_bot import PyUsbTransport

        return PyUsbTransport(vid=USB_VID_OTI, pid=USB_PID_2108)
    except Exception:
        return None


def find_linux_sg_device() -> TransportBackend | None:
    """Scans /dev/sg* devices on Linux for an OTi-2108 bridge."""
    if sys.platform != "linux":
        return None

    from openeasysync.transport.sg_io import LinuxSgIoTransport

    for dev_path in sorted(glob.glob("/dev/sg*")):
        try:
            transport = LinuxSgIoTransport(dev_path)
            info = transport.identify()
            if info.get("chip_id") == "OTi-2108":
                return transport
            transport.close()
        except Exception:
            continue
    return None


def find_windows_device() -> TransportBackend | None:
    """Scans drive letters C..Z on Windows for an OTi-2108 bridge."""
    if sys.platform != "win32":
        return None

    from openeasysync.transport.spti import WindowsSptiTransport

    for letter_code in range(ord("C"), ord("Z") + 1):
        drive = chr(letter_code)
        try:
            transport = WindowsSptiTransport(drive)
            info = transport.identify()
            if info.get("chip_id") == "OTi-2108":
                return transport
            transport.close()
        except Exception:
            continue
    return None


def detect_device() -> TransportBackend | None:
    """Attempts to auto-detect an OTi-2108 bridge using all available backends."""
    # 1. Try PyUSB direct BOT first (most reliable, works cross-platform)
    transport = find_usb_device()
    if transport:
        return transport

    # 2. Try Linux SG_IO
    if sys.platform == "linux":
        transport = find_linux_sg_device()
        if transport:
            return transport

    # 3. Try Windows SPTI
    if sys.platform == "win32":
        transport = find_windows_device()
        if transport:
            return transport

    return None
