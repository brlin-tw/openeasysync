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
    """Represents the fixed-size 552-byte (0x228) WIN32_FIND_DATAW directory entry layout."""

    filename: str = ""
    file_attributes: int = FILE_ATTRIBUTE_NORMAL
    creation_time: int = 0
    last_access_time: int = 0
    last_write_time: int = 0
    file_size: int = 0
    is_drive: bool = False
    entry_kind: int = ENTRY_KIND_FILE
    drive_type: int = DRIVE_FIXED
    total_bytes: int = 0
    total_free_bytes: int = 0
    caller_free_bytes: int = 0
    volume_label: str = ""
    filesystem_name: str = ""

    def __init__(
        self,
        filename: str = "",
        file_attributes: int = FILE_ATTRIBUTE_NORMAL,
        creation_time: int = 0,
        last_access_time: int = 0,
        last_write_time: int = 0,
        file_size: int = 0,
        is_drive: bool = False,
        find_data: Win32FindData | None = None,
        entry_kind: int = ENTRY_KIND_FILE,
        drive_type: int = DRIVE_FIXED,
        total_bytes: int = 0,
        total_free_bytes: int = 0,
        caller_free_bytes: int = 0,
        volume_label: str = "",
        filesystem_name: str = "",
    ):
        if find_data is not None:
            self.filename = find_data.filename
            self.file_attributes = find_data.file_attributes
            self.creation_time = find_data.creation_time
            self.last_access_time = find_data.last_access_time
            self.last_write_time = find_data.last_write_time
            self.file_size = find_data.file_size
        else:
            self.filename = filename
            self.file_attributes = file_attributes
            self.creation_time = creation_time
            self.last_access_time = last_access_time
            self.last_write_time = last_write_time
            self.file_size = file_size

        self.is_drive = is_drive or (entry_kind == ENTRY_KIND_DRIVE)
        self.entry_kind = ENTRY_KIND_DRIVE if self.is_drive else entry_kind
        self.drive_type = drive_type
        self.total_bytes = total_bytes
        self.total_free_bytes = total_free_bytes
        self.caller_free_bytes = caller_free_bytes
        self.volume_label = volume_label
        self.filesystem_name = filesystem_name

    @property
    def is_dir(self) -> bool:
        return bool(self.file_attributes & FILE_ATTRIBUTE_DIRECTORY)

    @property
    def is_file(self) -> bool:
        return not self.is_dir and not self.is_drive

    @property
    def is_directory(self) -> bool:
        return self.is_dir

    @property
    def alternate_filename(self) -> str:
        return ""

    @property
    def find_data(self) -> "Entry552":
        """Backward-compatible find_data accessor."""
        return self

    def pack(self, encoding: str = "utf-16le") -> bytes:
        """Packs into exactly 552 bytes according to WIN32_FIND_DATAW layout."""
        fn_encoded = self.filename.encode("utf-16le")[:512]
        c_file_name_len = len(fn_encoded)
        fn_field = fn_encoded.ljust(512, b"\x00")

        header = struct.pack(
            "<IQQQQI",
            self.file_attributes,
            self.creation_time,
            self.last_access_time,
            self.last_write_time,
            self.file_size,
            c_file_name_len,
        )
        entry = header + fn_field
        if len(entry) != DIR_ENTRY_SIZE:
            raise ValueError(f"Packed entry size mismatch: {len(entry)} != {DIR_ENTRY_SIZE}")
        return entry

    @classmethod
    def unpack(cls, data: bytes, encoding: str = "utf-16le") -> "Entry552":
        """Unpacks 552 bytes into an Entry552 instance."""
        if len(data) < DIR_ENTRY_SIZE:
            raise ValueError(
                f"Expected at least {DIR_ENTRY_SIZE} bytes for Entry552, got {len(data)}"
            )

        (
            attrs,
            cr_time,
            acc_time,
            wr_time,
            file_size,
            name_len,
        ) = struct.unpack_from("<IQQQQI", data, 0)

        raw_name = data[40 : 40 + min(name_len, 512)]
        fn = raw_name.decode("utf-16le", errors="replace").rstrip("\x00")

        return cls(
            filename=fn,
            file_attributes=attrs,
            creation_time=cr_time,
            last_access_time=acc_time,
            last_write_time=wr_time,
            file_size=file_size,
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
    return Entry552(
        filename=name,
        file_attributes=attrs,
        creation_time=ct,
        last_access_time=at,
        last_write_time=mt,
        file_size=0 if is_dir else size,
        is_drive=False,
    )


def make_drive_entry(
    name: str,
    drive_type: int = DRIVE_FIXED,
    total_bytes: int = 100 * 1024 * 1024 * 1024,
    free_bytes: int = 50 * 1024 * 1024 * 1024,
    label: str = "Local Disk",
    fs_name: str = "NTFS",
) -> Entry552:
    """Helper to create a drive/volume Entry552."""
    return Entry552(
        filename=name,
        file_attributes=FILE_ATTRIBUTE_DIRECTORY,
        is_drive=True,
        entry_kind=ENTRY_KIND_DRIVE,
        drive_type=drive_type,
        total_bytes=total_bytes,
        total_free_bytes=free_bytes,
        caller_free_bytes=free_bytes,
        volume_label=label,
        filesystem_name=fs_name,
    )
