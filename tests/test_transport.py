# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for transport abstractions, BOT wrappers, sense decoders, and mock bridge."""

import struct

import pytest

from openeasysync.constants import (
    CSW_STATUS_PASSED,
    MODE_TRANSFER_LINE,
    OTI_CBW_TAG,
    SENSE_MAP,
    SPEED_USB20,
    USBC_SIGNATURE,
    USBS_SIGNATURE,
    decode_sense_code,
)
from openeasysync.transport.mock import MockBridgeCable
from openeasysync.transport.usb_bot import parse_bot_csw, wrap_bot_cbw


def test_wrap_bot_cbw():
    """Verify CBW conforms to 31-byte USB BOT specification."""
    cdb = bytes([0xF0] + [0x00] * 13 + [0x4F, 0x54])
    cbw = wrap_bot_cbw(cdb, 12, direction_in=True, tag=OTI_CBW_TAG)

    assert len(cbw) == 31
    sig, tag, xfer_len, flags, lun, cdb_len = struct.unpack_from("<IIIBBB", cbw, 0)
    assert sig == USBC_SIGNATURE
    assert tag == OTI_CBW_TAG
    assert xfer_len == 12
    assert flags == 0x80  # IN
    assert lun == 0
    assert cdb_len == 16
    assert cbw[15:31] == cdb


def test_parse_bot_csw():
    """Verify CSW parsing and tag validation."""
    csw_raw = struct.pack("<IIIB", USBS_SIGNATURE, OTI_CBW_TAG, 0, CSW_STATUS_PASSED)
    tag, residue, status = parse_bot_csw(csw_raw, expected_tag=OTI_CBW_TAG)
    assert tag == OTI_CBW_TAG
    assert residue == 0
    assert status == CSW_STATUS_PASSED

    # Invalid signature raises ValueError
    bad_sig = struct.pack("<IIIB", 0x12345678, OTI_CBW_TAG, 0, 0)
    with pytest.raises(ValueError, match="Invalid CSW signature"):
        parse_bot_csw(bad_sig)

    # Tag mismatch raises ValueError
    with pytest.raises(ValueError, match="CSW tag mismatch"):
        parse_bot_csw(csw_raw, expected_tag=0x99999999)


def test_decode_sense_codes():
    """Verify all 24-bit SCSI sense codes match Part 1 Section 4 lookup table."""
    for sense24, (expected_code, _) in SENSE_MAP.items():
        sk = (sense24 >> 16) & 0x0F
        asc = (sense24 >> 8) & 0xFF
        ascq = sense24 & 0xFF
        decoded24, code, _ = decode_sense_code(sk, asc, ascq)
        assert decoded24 == sense24
        assert code == expected_code

    # Unrecognized code returns 0xFFFF
    _, code, _ = decode_sense_code(0x05, 0x99, 0x99)
    assert code == 0xFFFF


def test_mock_bridge_cable_loopback():
    """Verify MockBridgeCable simulates registers, speeds, and pipe transfers."""
    cable = MockBridgeCable()
    host_a, host_b = cable.create_pair()

    # Identify
    info_a = host_a.identify()
    assert info_a["chip_id"] == "OTi-2108"
    assert info_a["fw_rev"] == 3
    assert info_a["fw_date"] == "030729"

    # Speeds
    local_spd, peer_spd = host_a.get_usb_speed()
    assert local_spd == SPEED_USB20
    assert peer_spd == SPEED_USB20

    # Modes
    assert host_a.get_peer_mode() == MODE_TRANSFER_LINE
    host_b.set_mode(0x0044)
    assert host_a.get_peer_mode() == 0x0044

    # Bulk Block Write (A -> B)
    test_block = b"HELLO_OTI2108_" * 4000
    host_a.write_bulk_block(test_block)

    # Poll from B should detect remote block
    action, rem_blocks, _, _ = host_b.poll_link(0)
    assert action == 5
    assert rem_blocks == 1

    # Bulk Block Read on B
    received = host_b.read_bulk_block()
    assert len(received) == 65536
    assert received.startswith(b"HELLO_OTI2108_")

    host_a.close()
    host_b.close()
