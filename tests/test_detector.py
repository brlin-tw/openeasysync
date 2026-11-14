# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for device detection, enumeration, and multi-device selection."""

from unittest.mock import MagicMock, patch

from openeasysync.transport.detector import (
    detect_device,
    find_usb_device,
    list_connected_devices,
)


def test_list_connected_devices_empty():
    """Verify list_connected_devices returns an empty list when no devices are present."""
    with patch("usb.core.find", return_value=[]):
        devs = list_connected_devices()
        assert devs == []


def test_find_usb_device_auto_selects_unopened_device():
    """Verify auto-selection skips busy devices and selects the first available one."""
    dev_busy = MagicMock(bus=1, address=4, port_numbers=(1, 2))
    dev_free = MagicMock(bus=1, address=5, port_numbers=(1, 3))

    with patch("usb.core.find", return_value=[dev_busy, dev_free]):
        # Mock PyUsbTransport so that dev_busy raises EBUSY and dev_free succeeds
        def mock_transport_init(self, dev=None, **kwargs):
            if dev == dev_busy:
                raise RuntimeError("Cannot claim interface: device is already opened or busy")
            self.dev = dev

        with patch(
            "openeasysync.transport.usb_bot.PyUsbTransport.__init__",
            side_effect=mock_transport_init,
            autospec=True,
        ):
            # 1. Auto-selection (device_spec=None): should pick dev_free
            transport = find_usb_device(None)
            assert transport is not None
            assert transport.dev == dev_free

            # 2. Explicit selection by bus:address "1:5"
            transport_explicit = find_usb_device("1:5")
            assert transport_explicit is not None
            assert transport_explicit.dev == dev_free

            # 3. Explicit selection by index "1"
            transport_idx = find_usb_device("1")
            assert transport_idx is not None
            assert transport_idx.dev == dev_free


def test_detect_device_routing():
    """Verify detect_device routes to appropriate backend based on specification."""
    with patch("openeasysync.transport.detector.find_linux_sg_device") as mock_sg:
        mock_sg.return_value = MagicMock()
        t = detect_device("/dev/sg2")
        assert t is not None
        mock_sg.assert_called_once_with("/dev/sg2")

    with patch("openeasysync.transport.detector.find_usb_device") as mock_usb:
        mock_usb.return_value = MagicMock()
        t = detect_device("1:4")
        assert t is not None
        mock_usb.assert_called_once_with("1:4")
