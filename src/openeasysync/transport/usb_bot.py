# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Direct USB Bulk-Only Transport (BOT) backend for OTi-2108 using PyUSB / libusb."""

import struct
from typing import Literal

from openeasysync.constants import (
    CSW_STATUS_FAILED,
    OTI_CBW_TAG,
    USB_PID_2108,
    USB_VID_OTI,
    USBC_SIGNATURE,
    USBS_SIGNATURE,
)
from openeasysync.transport.base import TransportBackend


def wrap_bot_cbw(
    cdb: bytes,
    transfer_length: int,
    direction_in: bool = True,
    tag: int = OTI_CBW_TAG,
    lun: int = 0,
) -> bytes:
    """Wraps a CDB in a standard 31-byte USB BOT Command Block Wrapper (CBW).

    OTi-2108 firmware expects bCBWCBLength=16 and constant dCBWTag=0x08210827.
    """
    flags = 0x80 if direction_in and transfer_length > 0 else 0x00
    cdb_len = max(len(cdb), 16)
    cdb_padded = cdb.ljust(16, b"\x00")
    header = struct.pack("<IIIBBB", USBC_SIGNATURE, tag, transfer_length, flags, lun, cdb_len)
    return header + cdb_padded


def parse_bot_csw(csw: bytes, expected_tag: int | None = None) -> tuple[int, int, int]:
    """Parses a 13-byte USB BOT Command Status Wrapper (CSW).

    Returns: (tag, data_residue, status).
    """
    if len(csw) != 13:
        raise ValueError(f"Expected 13-byte CSW, got {len(csw)} bytes")
    sig, tag, residue, status = struct.unpack("<IIIB", csw)
    if sig != USBS_SIGNATURE:
        raise ValueError(f"Invalid CSW signature: 0x{sig:08x} (expected 0x{USBS_SIGNATURE:08x})")
    if expected_tag is not None and tag != expected_tag:
        raise ValueError(f"CSW tag mismatch: 0x{tag:08x} != 0x{expected_tag:08x}")
    return tag, residue, status


class PyUsbTransport(TransportBackend):
    """Direct PyUSB/libusb BOT transport for OTi-2108."""

    def __init__(self, dev=None, vid: int = USB_VID_OTI, pid: int = USB_PID_2108):
        try:
            import usb.core
            import usb.util
        except ImportError as exc:
            raise RuntimeError("pyusb is required for PyUsbTransport") from exc

        if dev is None:
            dev = usb.core.find(idVendor=vid, idProduct=pid)
            if dev is None:
                raise RuntimeError(
                    f"OTi-2108 USB device not found (VID={vid:#06x}, PID={pid:#06x})"
                )

        self.dev = dev
        self._tag = OTI_CBW_TAG

        # Detach OS driver if bound
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except Exception:
            pass

        try:
            dev.set_configuration()
        except Exception:
            pass

        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]

        ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: (
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
            ),
        )
        ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: (
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
            ),
        )

        if ep_out is None or ep_in is None:
            raise RuntimeError("Could not locate Bulk IN/OUT endpoints on OTi-2108 device")

        self.ep_out = ep_out
        self.ep_in = ep_in

        # Claim the interface exclusively so other processes or VMs know it is in use
        self._claimed_interface = None
        try:
            usb.util.claim_interface(dev, intf.bInterfaceNumber)
            self._claimed_interface = intf.bInterfaceNumber
        except Exception as exc:
            raise RuntimeError(
                f"Cannot claim interface {intf.bInterfaceNumber} on USB device "
                f"(Bus {dev.bus} Device {dev.address}): device is already opened or busy: {exc}"
            ) from exc

    def reset_recovery(self) -> None:
        """Performs USB BOT Reset Recovery (Spec Section 5.3.4)."""
        intf_num = self._claimed_interface if self._claimed_interface is not None else 0
        try:
            # Bulk-Only Mass Storage Reset: bmRequestType=0x21, bRequest=0xFF
            self.dev.ctrl_transfer(0x21, 0xFF, 0x0000, intf_num, b"", timeout=1000)
        except Exception:
            pass
        try:
            self.dev.clear_halt(self.ep_in)
        except Exception:
            pass
        try:
            self.dev.clear_halt(self.ep_out)
        except Exception:
            pass

    def execute_cdb(
        self,
        cdb: bytes,
        direction: Literal["NONE", "IN", "OUT"],
        data_or_length: bytes | int = 0,
        timeout_ms: int = 5000,
        tag: int = OTI_CBW_TAG,
    ) -> tuple[int, bytes, tuple[int, int, int]]:
        if direction == "IN":
            transfer_len = int(data_or_length)
            is_in = True
            out_bytes = b""
        elif direction == "OUT":
            out_bytes = (
                bytes(data_or_length) if not isinstance(data_or_length, bytes) else data_or_length
            )
            transfer_len = len(out_bytes)
            is_in = False
        else:
            transfer_len = 0
            is_in = False
            out_bytes = b""

        # 1. Write CBW
        cbw = wrap_bot_cbw(cdb, transfer_len, direction_in=is_in, tag=tag)
        try:
            self.ep_out.write(cbw, timeout=timeout_ms)
        except Exception as exc:
            # If writing CBW times out or stalls, perform BOT Reset Recovery and retry once
            if (
                getattr(exc, "errno", None) in (32, 110)
                or "time" in str(exc).lower()
                or "pipe" in str(exc).lower()
            ):
                self.reset_recovery()
                self.ep_out.write(cbw, timeout=timeout_ms)
            else:
                raise

        # 2. Data Stage
        data_in = b""
        if transfer_len > 0:
            if is_in:
                try:
                    data_in = bytes(self.ep_in.read(transfer_len, timeout=timeout_ms))
                except Exception as exc:
                    # Endpoint STALL during data stage (e.g. pipe error)
                    if getattr(exc, "errno", None) == 32 or "pipe" in str(exc).lower():
                        try:
                            self.dev.clear_halt(self.ep_in)
                        except Exception:
                            pass
                    else:
                        raise
            else:
                try:
                    self.ep_out.write(out_bytes, timeout=timeout_ms)
                except Exception as exc:
                    if getattr(exc, "errno", None) == 32 or "pipe" in str(exc).lower():
                        try:
                            self.dev.clear_halt(self.ep_out)
                        except Exception:
                            pass
                    else:
                        raise

        # 3. Read CSW
        csw_raw = b""
        try:
            csw_raw = bytes(self.ep_in.read(13, timeout=timeout_ms))
        except Exception as exc:
            # CSW read STALL: clear halt and retry once per BOT Section 5.3
            if getattr(exc, "errno", None) == 32 or "pipe" in str(exc).lower():
                try:
                    self.dev.clear_halt(self.ep_in)
                    csw_raw = bytes(self.ep_in.read(13, timeout=timeout_ms))
                except Exception:
                    self.reset_recovery()
                    raise
            else:
                raise

        _, _, status = parse_bot_csw(csw_raw, expected_tag=tag)

        # 4. Check sense on failure
        sense = (0, 0, 0)
        if status == CSW_STATUS_FAILED:
            sense = self._request_sense(timeout_ms)

        return status, data_in, sense

    def _request_sense(self, timeout_ms: int = 2000) -> tuple[int, int, int]:
        """Issues SCSI REQUEST SENSE (03 00 00 00 12 00)."""
        cdb = bytes([0x03, 0x00, 0x00, 0x00, 0x12, 0x00])
        tag = OTI_CBW_TAG
        cbw = wrap_bot_cbw(cdb, 18, direction_in=True, tag=tag)
        try:
            self.ep_out.write(cbw, timeout=timeout_ms)
            sense_data = bytes(self.ep_in.read(18, timeout=timeout_ms))
            csw = bytes(self.ep_in.read(13, timeout=timeout_ms))
            parse_bot_csw(csw, expected_tag=tag)
            if len(sense_data) >= 14:
                sk = sense_data[2] & 0x0F
                asc = sense_data[12]
                ascq = sense_data[13]
                return sk, asc, ascq
        except Exception as exc:
            if getattr(exc, "errno", None) == 32 or "pipe" in str(exc).lower():
                try:
                    self.dev.clear_halt(self.ep_in)
                except Exception:
                    pass
        return 0, 0, 0

    def close(self) -> None:
        try:
            import usb.util

            if getattr(self, "_claimed_interface", None) is not None:
                try:
                    usb.util.release_interface(self.dev, self._claimed_interface)
                except Exception:
                    pass
                self._claimed_interface = None
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass
