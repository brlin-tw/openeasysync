# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Transport abstraction layer for OTi-2108 hardware backends and mock loopback."""

from openeasysync.transport.base import TransportBackend
from openeasysync.transport.mock import MockBridgeCable, MockTransport

__all__ = [
    "TransportBackend",
    "MockBridgeCable",
    "MockTransport",
]
