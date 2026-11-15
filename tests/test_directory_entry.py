# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for 552-byte Directory Entry struct and Windows FILETIME conversion."""

from openeasysync.constants import (
    DIR_ENTRY_SIZE,
    FILE_ATTRIBUTE_DIRECTORY,
)
from openeasysync.protocol.directory_entry import (
    Entry552,
    Win32FindData,
    filetime_to_unix,
    make_file_entry,
    unix_to_filetime,
)


def test_filetime_unix_conversion_roundtrip():
    """Verify conversion between POSIX timestamp and Windows 64-bit FILETIME."""
    # Known point: 2026-01-01 00:00:00 UTC = 1767225600.0
    known_unix = 1767225600.0
    ft = unix_to_filetime(known_unix)
    assert ft > 0

    reconstructed_unix = filetime_to_unix(ft)
    assert abs(reconstructed_unix - known_unix) < 1e-5

    # Epoch 1970-01-01 test
    assert unix_to_filetime(0.0) == 0
    assert filetime_to_unix(0) == 0.0


def test_win32_find_data_pack_unpack():
    """Verify 320-byte WIN32_FIND_DATAA structure round-trip."""
    fd = Win32FindData(
        file_attributes=FILE_ATTRIBUTE_DIRECTORY,
        creation_time=130000000000000000,
        last_access_time=130000000010000000,
        last_write_time=130000000020000000,
        file_size=123456789,
        filename="Documents_and_Settings",
        alternate_filename="DOCUME~1",
    )
    raw = fd.pack()
    assert len(raw) == 320

    unpacked = Win32FindData.unpack(raw)
    assert unpacked.file_attributes == fd.file_attributes
    assert unpacked.creation_time == fd.creation_time
    assert unpacked.last_access_time == fd.last_access_time
    assert unpacked.last_write_time == fd.last_write_time
    assert unpacked.file_size == fd.file_size
    assert unpacked.filename == fd.filename
    assert unpacked.alternate_filename == fd.alternate_filename
    assert unpacked.is_directory is True


def test_entry552_file_and_dir_pack_unpack():
    """Verify 552-byte Entry552 packs to exactly 552 bytes and deserializes correctly."""
    # 1. File Entry with Unicode name
    file_entry = make_file_entry("測試文件_test_doc.pdf", size=98765, is_dir=False)
    file_raw = file_entry.pack()
    assert len(file_raw) == DIR_ENTRY_SIZE

    unpacked_f = Entry552.unpack(file_raw)
    assert unpacked_f.is_file is True
    assert unpacked_f.is_dir is False
    assert unpacked_f.filename == "測試文件_test_doc.pdf"
    assert unpacked_f.find_data.filename == "測試文件_test_doc.pdf"
    assert unpacked_f.find_data.file_size == 98765

    # 2. Directory Entry
    dir_entry = make_file_entry("SubFolder", is_dir=True)
    dir_raw = dir_entry.pack()
    assert len(dir_raw) == DIR_ENTRY_SIZE

    unpacked_d = Entry552.unpack(dir_raw)
    assert unpacked_d.is_dir is True
    assert unpacked_d.is_file is False
    assert unpacked_d.filename == "SubFolder"
