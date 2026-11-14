# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""552-byte Directory & Drive Entry serialization, deserialization, and FILETIME conversion."""

import struct
import time
from dataclasses import dataclass

from openeasysync.constants import (
    DIR_ENTRY_SIZE,
    DRIVE_FIXED,
    ENTRY_KIND_DRIVE,
    ENTRY_KIND_FILE,
    FILE_ATTRIBUTE_DIRECTORY,
    FILE_ATTRIBUTE_NORMAL,
    WIN32_FIND_DATA_SIZE,
)

# Number of 100-nanosecond intervals between 1601-01-01 and 1970-01-01
EPOCH_DIFF_100NS = 116444736000000000


def unix_to_filetime(unix_time: float) -> int:
    """Converts a POSIX timestamp (seconds since 1970-01-01) to a 64-bit Windows FILETIME."""
    if unix_time <= 0:
        return 0
    return int((unix_time * 10_000_000) + EPOCH_DIFF_100NS)


def filetime_to_unix(filetime: int) -> float:
    """Converts a 64-bit Windows FILETIME to a POSIX timestamp."""
    if filetime <= 0:
        return 0.0
    return (filetime - EPOCH_DIFF_100NS) / 10_000_000.0


@dataclass
class Win32FindData:
    """Represents the 320-byte WIN32_FIND_DATAA structure."""

    file_attributes: int = FILE_ATTRIBUTE_NORMAL
    creation_time: int = 0
    last_access_time: int = 0
    last_write_time: int = 0
    file_size: int = 0
    reserved0: int = 0
    reserved1: int = 0
    filename: str = ""
    alternate_filename: str = ""

    @property
    def is_directory(self) -> bool:
        return bool(self.file_attributes & FILE_ATTRIBUTE_DIRECTORY)

    def pack(self, encoding: str = "utf-8") -> bytes:
        """Packs WIN32_FIND_DATAA into 320 bytes."""
        size_high = (self.file_size >> 32) & 0xFFFFFFFF
        size_low = self.file_size & 0xFFFFFFFF

        fn_bytes = self.filename.encode(encoding, errors="replace")[:259] + b"\x00"
        fn_field = fn_bytes.ljust(260, b"\x00")

        alt_bytes = self.alternate_filename.encode(encoding, errors="replace")[:13] + b"\x00"
        alt_field = alt_bytes.ljust(14, b"\x00")

        padding = b"\x00\x00"

        header = struct.pack(
            "<IQQQIIII",
            self.file_attributes,
            self.creation_time,
            self.last_access_time,
            self.last_write_time,
            size_high,
            size_low,
            self.reserved0,
            self.reserved1,
        )
        return header + fn_field + alt_field + padding

    @classmethod
    def unpack(cls, data: bytes, encoding: str = "utf-8") -> "Win32FindData":
        """Unpacks 320 bytes of WIN32_FIND_DATAA."""
        if len(data) < WIN32_FIND_DATA_SIZE:
            raise ValueError(
                f"Expected at least {WIN32_FIND_DATA_SIZE} bytes for WIN32_FIND_DATAA, got {len(data)}"
            )

        (
            attrs,
            cr_time,
            acc_time,
            wr_time,
            size_high,
            size_low,
            res0,
            res1,
        ) = struct.unpack_from("<IQQQIIII", data, 0)

        file_size = (size_high << 32) | size_low

        raw_fn = data[44 : 44 + 260]
        fn = raw_fn.split(b"\x00", 1)[0].decode(encoding, errors="replace")

        raw_alt = data[304 : 304 + 14]
        alt = raw_alt.split(b"\x00", 1)[0].decode(encoding, errors="replace")

        return cls(
            file_attributes=attrs,
            creation_time=cr_time,
            last_access_time=acc_time,
            last_write_time=wr_time,
            file_size=file_size,
            reserved0=res0,
            reserved1=res1,
            filename=fn,
            alternate_filename=alt,
        )


@dataclass
class Entry552:
    """Represents the 552-byte Directory/Drive entry layout."""

    entry_kind: int = ENTRY_KIND_FILE  # 0 = Drive, 1 = File/Directory
    find_data: Win32FindData = None  # type: ignore
    drive_type: int = DRIVE_FIXED
    total_bytes: int = 0
    total_free_bytes: int = 0
    caller_free_bytes: int = 0
    volume_label: str = ""
    filesystem_name: str = ""

    def __post_init__(self):
        if self.find_data is None:
            self.find_data = Win32FindData()

    @property
    def is_drive(self) -> bool:
        return self.entry_kind == ENTRY_KIND_DRIVE

    @property
    def is_dir(self) -> bool:
        return self.entry_kind == ENTRY_KIND_FILE and self.find_data.is_directory

    @property
    def is_file(self) -> bool:
        return self.entry_kind == ENTRY_KIND_FILE and not self.find_data.is_directory

    def pack(self, encoding: str = "utf-8") -> bytes:
        """Packs into exactly 552 bytes."""
        fd_bytes = self.find_data.pack(encoding=encoding)

        vol_bytes = self.volume_label.encode(encoding, errors="replace")[:99] + b"\x00"
        vol_field = vol_bytes.ljust(100, b"\x00")

        fs_bytes = self.filesystem_name.encode(encoding, errors="replace")[:99] + b"\x00"
        fs_field = fs_bytes.ljust(100, b"\x00")

        part1 = struct.pack("<I", self.entry_kind) + fd_bytes
        part2 = struct.pack(
            "<IQQQ",
            self.drive_type,
            self.total_bytes,
            self.total_free_bytes,
            self.caller_free_bytes,
        )
        entry = part1 + part2 + vol_field + fs_field
        if len(entry) != DIR_ENTRY_SIZE:
            raise ValueError(f"Packed entry size mismatch: {len(entry)} != {DIR_ENTRY_SIZE}")
        return entry

    @classmethod
    def unpack(cls, data: bytes, encoding: str = "utf-8") -> "Entry552":
        """Unpacks 552 bytes into an Entry552 instance."""
        if len(data) < DIR_ENTRY_SIZE:
            raise ValueError(
                f"Expected at least {DIR_ENTRY_SIZE} bytes for Entry552, got {len(data)}"
            )

        entry_kind = struct.unpack_from("<I", data, 0)[0]
        find_data = Win32FindData.unpack(data[4 : 4 + WIN32_FIND_DATA_SIZE], encoding=encoding)

        offset = 4 + WIN32_FIND_DATA_SIZE
        drive_type, total_b, free_b, caller_b = struct.unpack_from("<IQQQ", data, offset)

        vol_raw = data[offset + 28 : offset + 28 + 100]
        vol_label = vol_raw.split(b"\x00", 1)[0].decode(encoding, errors="replace")

        fs_raw = data[offset + 128 : offset + 128 + 100]
        fs_name = fs_raw.split(b"\x00", 1)[0].decode(encoding, errors="replace")

        return cls(
            entry_kind=entry_kind,
            find_data=find_data,
            drive_type=drive_type,
            total_bytes=total_b,
            total_free_bytes=free_b,
            caller_free_bytes=caller_b,
            volume_label=vol_label,
            filesystem_name=fs_name,
        )


def make_file_entry(
    name: str,
    size: int = 0,
    is_dir: bool = False,
    mtime: float | None = None,
    ctime: float | None = None,
    atime: float | None = None,
) -> Entry552:
    """Helper to create a file or directory Entry552."""
    now = time.time()
    mt = unix_to_filetime(mtime if mtime is not None else now)
    ct = unix_to_filetime(ctime if ctime is not None else now)
    at = unix_to_filetime(atime if atime is not None else now)

    attrs = FILE_ATTRIBUTE_DIRECTORY if is_dir else FILE_ATTRIBUTE_NORMAL
    fd = Win32FindData(
        file_attributes=attrs,
        creation_time=ct,
        last_access_time=at,
        last_write_time=mt,
        file_size=0 if is_dir else size,
        filename=name,
    )
    return Entry552(entry_kind=ENTRY_KIND_FILE, find_data=fd)


def make_drive_entry(
    name: str,
    drive_type: int = DRIVE_FIXED,
    total_bytes: int = 100 * 1024 * 1024 * 1024,
    free_bytes: int = 50 * 1024 * 1024 * 1024,
    label: str = "Local Disk",
    fs_name: str = "NTFS",
) -> Entry552:
    """Helper to create a drive/volume Entry552."""
    fd = Win32FindData(
        file_attributes=FILE_ATTRIBUTE_DIRECTORY,
        filename=name,
    )
    return Entry552(
        entry_kind=ENTRY_KIND_DRIVE,
        find_data=fd,
        drive_type=drive_type,
        total_bytes=total_bytes,
        total_free_bytes=free_bytes,
        caller_free_bytes=free_bytes,
        volume_label=label,
        filesystem_name=fs_name,
    )
