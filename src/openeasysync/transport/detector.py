# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Device detection, enumeration, and multi-device selection for OTi-2108 bridges."""

import glob
import sys
from typing import Any

from openeasysync.constants import USB_PID_2108, USB_VID_OTI
from openeasysync.transport.base import TransportBackend


def list_connected_devices() -> list[dict[str, Any]]:
    """Lists all detected OTi-2108 devices and reports their availability status."""
    devices: list[dict[str, Any]] = []

    # 1. Enumerate USB devices via PyUSB
    try:
        import usb.core
        import usb.util

        usb_devs = list(usb.core.find(find_all=True, idVendor=USB_VID_OTI, idProduct=USB_PID_2108))
        for idx, dev in enumerate(usb_devs):
            port_str = (
                ".".join(str(p) for p in dev.port_numbers)
                if getattr(dev, "port_numbers", None)
                else "?"
            )
            spec = f"{dev.bus}:{dev.address}"
            desc = f"Bus {dev.bus:03d} Device {dev.address:03d} (Port {port_str})"

            # Probe whether device is available or busy
            is_avail = False
            status_msg = "Available"
            try:
                from openeasysync.transport.usb_bot import PyUsbTransport

                t = PyUsbTransport(dev=dev)
                t.close()
                is_avail = True
            except Exception as exc:
                err_str = str(exc)
                if (
                    "busy" in err_str.lower()
                    or "resource" in err_str.lower()
                    or "claim" in err_str.lower()
                ):
                    status_msg = "In use / claimed (by another process or VM)"
                elif "access" in err_str.lower() or "permission" in err_str.lower():
                    status_msg = "Access denied (check permissions / udev rules)"
                else:
                    status_msg = f"Busy ({err_str})"

            devices.append(
                {
                    "index": idx,
                    "backend": "usb",
                    "spec": spec,
                    "description": desc,
                    "is_available": is_avail,
                    "status": status_msg,
                    "raw_dev": dev,
                }
            )
    except Exception:
        pass

    # 2. Enumerate Linux /dev/sg* devices
    if sys.platform == "linux":
        for dev_path in sorted(glob.glob("/dev/sg*")):
            try:
                from openeasysync.transport.sg_io import LinuxSgIoTransport

                t = LinuxSgIoTransport(dev_path)
                info = t.identify()
                t.close()
                if info.get("chip_id") == "OTi-2108":
                    devices.append(
                        {
                            "index": len(devices),
                            "backend": "sg_io",
                            "spec": dev_path,
                            "description": f"SCSI Generic {dev_path}",
                            "is_available": True,
                            "status": "Available",
                            "raw_dev": dev_path,
                        }
                    )
            except Exception as exc:
                if "already in use" in str(exc):
                    devices.append(
                        {
                            "index": len(devices),
                            "backend": "sg_io",
                            "spec": dev_path,
                            "description": f"SCSI Generic {dev_path}",
                            "is_available": False,
                            "status": "In use by another process",
                            "raw_dev": dev_path,
                        }
                    )

    return devices


def find_usb_device(device_spec: str | None = None) -> TransportBackend | None:
    """Finds an OTi-2108 USB device.

    If device_spec is specified:
        - '<bus>:<address>' (e.g. '1:4', '001:004')
        - integer index (e.g. '0', '1')
    If device_spec is None:
        - Automatically returns the first OTi-2108 device that is NOT already opened/busy.
    """
    try:
        import usb.core

        from openeasysync.transport.usb_bot import PyUsbTransport

        usb_devs = list(usb.core.find(find_all=True, idVendor=USB_VID_OTI, idProduct=USB_PID_2108))
        if not usb_devs:
            return None

        # Filter by spec if provided
        if device_spec:
            clean_spec = device_spec.strip()
            # 1. Match "<bus>:<address>"
            if ":" in clean_spec:
                parts = clean_spec.split(":")
                try:
                    target_bus = int(parts[0])
                    target_addr = int(parts[1])
                    for dev in usb_devs:
                        if dev.bus == target_bus and dev.address == target_addr:
                            return PyUsbTransport(dev=dev)
                except ValueError:
                    pass

            # 2. Match integer index
            try:
                idx = int(clean_spec)
                if 0 <= idx < len(usb_devs):
                    return PyUsbTransport(dev=usb_devs[idx])
            except ValueError:
                pass

        # Auto-selection: Find the first device that isn't already opened/claimed
        for dev in usb_devs:
            try:
                transport = PyUsbTransport(dev=dev)
                return transport
            except Exception:
                # Device is already claimed by another process or VM; try next
                continue

        return None
    except Exception:
        return None


def find_linux_sg_device(device_spec: str | None = None) -> TransportBackend | None:
    """Scans /dev/sg* devices on Linux, skipping any already in use."""
    if sys.platform != "linux":
        return None

    from openeasysync.transport.sg_io import LinuxSgIoTransport

    candidates = (
        [device_spec]
        if device_spec and device_spec.startswith("/dev/")
        else sorted(glob.glob("/dev/sg*"))
    )

    for dev_path in candidates:
        try:
            transport = LinuxSgIoTransport(dev_path)
            info = transport.identify()
            if info.get("chip_id") == "OTi-2108":
                return transport
            transport.close()
        except Exception:
            continue
    return None


def find_windows_device(drive_letter: str | None = None) -> TransportBackend | None:
    """Scans Windows removable drives for an OTi-2108 bridge."""
    if sys.platform != "win32":
        return None

    from openeasysync.transport.spti import WindowsSptiTransport

    candidates = [drive_letter] if drive_letter else [chr(c) for c in range(ord("C"), ord("Z") + 1)]

    for drive in candidates:
        try:
            transport = WindowsSptiTransport(drive)
            info = transport.identify()
            if info.get("chip_id") == "OTi-2108":
                return transport
            transport.close()
        except Exception:
            continue
    return None


def detect_device(device_spec: str | None = None) -> TransportBackend | None:
    """Auto-detects or selects an OTi-2108 bridge, prioritizing available devices."""
    if device_spec:
        clean = device_spec.strip()
        if clean.startswith("/dev/"):
            return find_linux_sg_device(clean)
        if sys.platform == "win32" and (len(clean) == 1 or (len(clean) == 2 and clean[1] == ":")):
            return find_windows_device(clean)
        # Try USB with spec
        t = find_usb_device(clean)
        if t:
            return t

    # 1. Try PyUSB direct BOT (auto-selecting free device)
    transport = find_usb_device()
    if transport:
        return transport

    # 2. Try Linux SG_IO (auto-selecting free node)
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
