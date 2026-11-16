# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hardware, wire protocol, and filesystem constants for OTi-2108 / OpenEasySync."""

# USB Device Identifiers
USB_VID_OTI = 0x0EA0
USB_PID_2108 = 0x2108

# Bulk-Only Transport (BOT) constants
USBC_SIGNATURE = 0x43425355  # "USBC"
USBS_SIGNATURE = 0x53425355  # "USBS"
OTI_CBW_TAG = 0x08210827

CSW_STATUS_PASSED = 0x00
CSW_STATUS_FAILED = 0x01
CSW_STATUS_PHASE_ERROR = 0x02

# Block and Framing Sizes
BULK_BLOCK_SIZE = 65536  # 0x10000 (64 KB)
BLOCK_HEADER_SIZE = 20  # 0x14
MAX_PAYLOAD_PER_BLOCK = 65516  # 0xFFEC (65536 - 20)
DIR_ENTRY_SIZE = 552  # 0x228
WIN32_FIND_DATA_SIZE = 320  # 0x140
VNIC_BLOCK_SIZE = 35328  # 0x8A00

# Operating Modes
MODE_OFFLINE = 0x0000
MODE_TRANSFER_LINE = 0x007F
MODE_VIRTUAL_NIC = 0x0044

# Hardware Register Selectors (CDB 0xD8 0x00)
OPTYPE_BUFFER_SIZE = 1
OPTYPE_USB_SPEED = 2
OPTYPE_LINK_POLL = 3
OPTYPE_PEER_MODE = 5

# Hardware Commands (CDB 0xD8 0x01)
CMD_SET_TIMEOUT = 4
CMD_SET_MODE = 5
CMD_FLUSH_TX = 7
CMD_FLUSH_RX = 8

# USB Link Speeds
SPEED_USB20 = 0x20
SPEED_USB11 = 0x11
SPEED_DISCONNECTED = 0x00

# Link / Pipe Poll Action Codes (CDB 0xD8 0x00 op_type=3)
ACTION_IDLE_0 = 0
ACTION_IDLE_1 = 1
ACTION_VDISK_READ = 2
ACTION_VDISK_WRITE = 3
ACTION_PEER_EVENT = 4
ACTION_REMOTE_BULK = 5
ACTION_READY_WRITE_SINGLE = 6
ACTION_READY_WRITE_BURST = 7

# Client Command Opcodes (0x01..0x39)
OP_QUERY_ROOT = 0x01
OP_NOTIFY_ACTIVE_DIR = 0x02
OP_VERIFY_PEER_NAME = 0x02  # alias for backward compatibility
OP_QUERY_CURRENT_DIR = 0x03
OP_LIST_DIR = 0x04
OP_SELECT_DRIVE_LEGACY = 0x05
OP_REMOVE_DIR = 0x06
OP_DELETE_DIR = 0x06  # alias for backward compatibility
OP_CREATE_DIR = 0x07
OP_DELETE_FILE = 0x08
OP_RENAME = 0x09
OP_RENAME_FILE = 0x09  # alias for backward compatibility
OP_RENAME_DIR = 0x09  # alias for backward compatibility
OP_UPLOAD_SINGLE = 0x0A
OP_DOWNLOAD_SINGLE = 0x0B
OP_FILE_CHUNK = 0x0C
OP_ABORT_UPLOAD = 0x0D
OP_CANCEL_TRANSFER = 0x0D  # alias for backward compatibility
OP_TRANSFER_COMPLETE = 0x0E
OP_QUERY_SPACE = 0x0E  # alias for backward compatibility
OP_CANCEL_KEEP_PARTIAL = 0x0F
OP_PRECHECK_SINGLE = 0x0F  # alias for backward compatibility
OP_DOWNLOAD_BATCH = 0x10
OP_SET_MAC_MODE = 0x10  # alias for backward compatibility
OP_ABORT_BATCH = 0x11
OP_BATCH_CLEAR_READONLY = 0x12
OP_PRECHECK_BATCH = 0x13
OP_CANCEL_SPLITTER = 0x14
OP_CREATE_BATCH_ZERO_FILES = 0x15
OP_PRECHECK_BATCH_EXT = 0x16
OP_UI_NOTIFICATION = 0x17
OP_QUERY_ERRORS = 0x17  # alias for backward compatibility
OP_KEEP_ALIVE_32 = 0x32
OP_KEEP_ALIVE_33 = 0x33
OP_PLUGIN_RPC = 0x34
OP_XML_CONTROL = 0x34  # alias for backward compatibility
OP_BATCH_DOWNLOAD_IDS_16 = 0x35
OP_BATCH_DOWNLOAD_IDS_32 = 0x36
OP_SET_FILE_ATTRS = 0x37
OP_FORMAT_WIN32_ERRORS = 0x38
OP_EXTRA_XML = 0x39
OP_VNIC_SWITCH = 0x39  # alias for backward compatibility
OP_EXTENDED_XML_MIN = 0x32
OP_EXTENDED_XML_MAX = 0x39

# Server Reply Opcodes (0xA1..0xAC)
REPLY_STATUS_ACK = 0xA1
REPLY_LISTING = 0xA2
REPLY_ERROR_CODE = 0xA2  # Deprecated alias; wire 0xA2 is directory listing, do NOT use for errors
REPLY_DOWNLOAD_CHUNK = 0xA3
REPLY_ROOTS = 0xA4
REPLY_DOWNLOAD_SINGLE = 0xA4  # Alias for backward compatibility
REPLY_UPLOAD_ERROR = 0xA5
REPLY_CHUNK_ACK = 0xA5  # Deprecated alias for REPLY_UPLOAD_ERROR
REPLY_UPLOAD_CHUNK_ACK = 0xA5  # Deprecated alias for REPLY_UPLOAD_ERROR
REPLY_DOWNLOAD_BATCH_STREAM = 0xA5  # Deprecated alias for 0xA5
REPLY_DOWNLOAD_BATCH_EMPTY = 0xA6
REPLY_DOWNLOAD_BATCH_DONE = 0xA7
REPLY_PRECHECK_BATCH = 0xA8
REPLY_UPLOAD_BATCH_ERROR = 0xA9
REPLY_PRECHECK_BATCH_EXT = 0xAA
REPLY_DOWNLOAD_UNREADABLE = 0xAB
REPLY_FORMATTED_ERROR = 0xAC

# Status ACK Values (REPLY_STATUS_ACK 0xA1)
STATUS_OK = 0
STATUS_ERROR = 1
STATUS_ACTIVE_DIR_ACK = 2
STATUS_CONFLICT = 2
STATUS_EXISTS = 3
STATUS_DISK_FULL = 6
STATUS_ZERO_LENGTH = 7
STATUS_INLINE_SMALL_FILE = 9
STATUS_UPLOAD_SINGLE_DONE = 9
STATUS_UPLOAD_BATCH_DONE = 0x0B
STATUS_MAC_MODE_ACK = 0x10

# Win32 File Attributes
FILE_ATTRIBUTE_READONLY = 0x00000001
FILE_ATTRIBUTE_HIDDEN = 0x00000002
FILE_ATTRIBUTE_SYSTEM = 0x00000004
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_ARCHIVE = 0x00000020
FILE_ATTRIBUTE_NORMAL = 0x00000080

# Win32 Drive Types (GetDriveTypeA)
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

# Entry Kind in 552-byte struct
ENTRY_KIND_DRIVE = 0
ENTRY_KIND_FILE = 1

# SCSI Sense Codes & Internal Codes
SENSE_MAP: dict[int, tuple[int, str]] = {
    0x000000: (0x0004, "No Sense / Command Succeeded"),
    0x023A00: (0x0005, "Medium Not Present"),
    0x052000: (0x0006, "Invalid Command Operation Code"),
    0x052400: (0x0007, "Invalid Field in CDB"),
    0x062800: (0x0008, "Unit Attention: Not Ready to Ready"),
    0x063F0E: (0x0009, "Unit Attention: Reported LUNs Changed"),
    0x072700: (0x0010, "Data Protect: Write Protected"),
    0x098000: (0x000A, "OTi Vendor: Pipe / Transfer Fault"),
    0x098100: (0x000B, "OTi Vendor: Peer Disconnected / Timeout"),
    0x098200: (0x000F, "OTi Vendor: Buffer Overflow / Busy"),
    0x098300: (0x0012, "OTi Vendor: Sequence Mismatch"),
    0x098400: (0x000D, "OTi Vendor: Peer Reset / Aborted"),
    0x098500: (0x0013, "OTi Vendor: Peer Not Reading / Waiting for Peer"),
    0x098600: (0x000E, "OTi Vendor: Peer Mode Conflict"),
    0x098700: (0x0017, "OTi Vendor: FIFO Underrun"),
    0x098800: (0x0018, "OTi Vendor: FIFO Overrun"),
    0x098900: (0x0019, "OTi Vendor: Link Lost"),
}


def decode_sense_code(sense_key: int, asc: int, ascq: int) -> tuple[int, int, str]:
    """Decode SCSI (sense_key, asc, ascq) to (sense24, internal_code, description)."""
    sense24 = ((sense_key & 0x0F) << 16) | ((asc & 0xFF) << 8) | (ascq & 0xFF)
    internal_code, desc = SENSE_MAP.get(
        sense24, (0xFFFF, f"Unrecognized Sense Error ({sense24:#08x})")
    )
    return sense24, internal_code, desc
