# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Peer server request dispatcher executing local filesystem operations for the remote peer."""

import os
import shutil
import sys
from typing import Any

from openeasysync.constants import (
    DRIVE_FIXED,
    DRIVE_REMOVABLE,
    OP_CANCEL_TRANSFER,
    OP_CREATE_DIR,
    OP_DELETE_DIR,
    OP_DELETE_FILE,
    OP_DOWNLOAD_BATCH,
    OP_DOWNLOAD_SINGLE,
    OP_LIST_DIR,
    OP_PRECHECK_BATCH,
    OP_PRECHECK_BATCH_EXT,
    OP_PRECHECK_SINGLE,
    OP_QUERY_ROOT,
    OP_QUERY_SPACE,
    OP_RENAME_DIR,
    OP_RENAME_FILE,
    OP_SET_MAC_MODE,
    OP_UPLOAD_BATCH,
    OP_UPLOAD_SINGLE,
    OP_XML_CONTROL,
    STATUS_EXISTS,
    STATUS_MAC_MODE_ACK,
    STATUS_OK,
    STATUS_UPLOAD_BATCH_DONE,
    STATUS_UPLOAD_SINGLE_DONE,
)
from openeasysync.protocol.commands import decode_command
from openeasysync.protocol.directory_entry import (
    Entry552,
    Win32FindData,
    filetime_to_unix,
    make_drive_entry,
    make_file_entry,
    unix_to_filetime,
)
from openeasysync.protocol.path_mapping import PathMapper
from openeasysync.protocol.replies import (
    BatchDownloadEmptyRecord,
    BatchDownloadStreamRecord,
    BatchPrecheckExtConflict,
    encode_download_batch_done,
    encode_download_batch_empty,
    encode_download_batch_stream,
    encode_download_single_reply,
    encode_error_code,
    encode_listing_reply,
    encode_precheck_batch_ext_reply,
    encode_precheck_batch_reply,
    encode_status_ack,
    encode_upload_batch_error,
)
from openeasysync.protocol.xml_msg import build_oti_msg, parse_oti_msg


class PeerServerHandler:
    """Handles incoming Client Commands from a remote peer and returns reply payloads."""

    def __init__(self, path_mapper: PathMapper | None = None):
        self.mapper = path_mapper or PathMapper()
        self.is_cancelled = False

    def handle_command(self, payload: bytes) -> list[bytes]:
        """Dispatches a command payload and returns a list of reply payload(s)."""
        if not payload:
            return [encode_error_code(1)]  # ERROR_INVALID_FUNCTION

        cmd = decode_command(payload)
        opcode = cmd["opcode"]

        if opcode == OP_QUERY_ROOT:
            return [self._handle_query_root()]

        if opcode == OP_LIST_DIR:
            return [self._handle_list_dir(cmd["path"])]

        if opcode == OP_DELETE_FILE:
            return [self._handle_delete_file(cmd["path"])]

        if opcode == OP_DELETE_DIR:
            return [self._handle_delete_dir(cmd["path"])]

        if opcode == OP_CREATE_DIR:
            return [self._handle_create_dir(cmd["path"])]

        if opcode in (OP_RENAME_FILE, OP_RENAME_DIR):
            return [self._handle_rename(cmd["old_path"], cmd["new_path"])]

        if opcode == OP_UPLOAD_SINGLE:
            return [
                self._handle_upload_single(cmd["dest_path"], cmd["find_data"], cmd["file_data"])
            ]

        if opcode == OP_DOWNLOAD_SINGLE:
            return [self._handle_download_single(cmd["path"])]

        if opcode == OP_UPLOAD_BATCH:
            return [self._handle_upload_batch(cmd["base_dir"], cmd["records"])]

        if opcode == OP_DOWNLOAD_BATCH:
            return self._handle_download_batch(cmd["paths"])

        if opcode == OP_CANCEL_TRANSFER:
            self.is_cancelled = True
            return [encode_status_ack(STATUS_OK)]

        if opcode == OP_QUERY_SPACE:
            return [self._handle_query_space(cmd["path"])]

        if opcode == OP_PRECHECK_SINGLE:
            return [self._handle_precheck_single(cmd["path"])]

        if opcode == OP_SET_MAC_MODE:
            return [encode_status_ack(STATUS_MAC_MODE_ACK)]

        if opcode == OP_PRECHECK_BATCH:
            return [self._handle_precheck_batch(cmd["dest_dir"], cmd["rel_paths"])]

        if opcode == OP_PRECHECK_BATCH_EXT:
            return [self._handle_precheck_batch_ext(cmd["dest_dir"], cmd["records"])]

        if opcode == OP_XML_CONTROL:
            return [self._handle_xml_control(cmd["xml"])]

        return [encode_error_code(1)]  # ERROR_INVALID_FUNCTION

    def _handle_query_root(self) -> bytes:
        drives: list[Entry552] = []
        if sys.platform == "win32":
            import string

            for letter in string.ascii_uppercase:
                root = f"{letter}:\\"
                if os.path.exists(root):
                    try:
                        usage = shutil.disk_usage(root)
                        drives.append(
                            make_drive_entry(
                                root,
                                drive_type=DRIVE_FIXED,
                                total_bytes=usage.total,
                                free_bytes=usage.free,
                                label="Local Disk",
                                fs_name="NTFS",
                            )
                        )
                    except Exception:
                        pass
        else:
            # Unix: Emulate C:\ as Root (or virtual root)
            local_root = self.mapper.virtual_root if self.mapper.virtual_root else "/"
            try:
                usage = shutil.disk_usage(local_root)
                total_b = usage.total
                free_b = usage.free
            except Exception:
                total_b = 100 * 1024 * 1024 * 1024
                free_b = 50 * 1024 * 1024 * 1024

            drives.append(
                make_drive_entry(
                    "C:\\",
                    drive_type=DRIVE_FIXED,
                    total_bytes=total_b,
                    free_bytes=free_b,
                    label="Root Filesystem",
                    fs_name="ext4",
                )
            )

            # Check /media or /mnt for mounted removable drives
            for mnt_dir in ("/media", "/mnt"):
                if os.path.isdir(mnt_dir):
                    try:
                        for entry in os.listdir(mnt_dir):
                            full_mnt = os.path.join(mnt_dir, entry)
                            if os.path.ismount(full_mnt):
                                try:
                                    u = shutil.disk_usage(full_mnt)
                                    drives.append(
                                        make_drive_entry(
                                            f"D:\\{entry}",
                                            drive_type=DRIVE_REMOVABLE,
                                            total_bytes=u.total,
                                            free_bytes=u.free,
                                            label=entry,
                                            fs_name="FAT32",
                                        )
                                    )
                                except Exception:
                                    pass
                    except Exception:
                        pass

        return encode_listing_reply(drives)

    def _handle_list_dir(self, dir_path: str) -> bytes:
        local_path = self.mapper.wire_to_local(dir_path)
        if not os.path.exists(local_path):
            return encode_error_code(3)  # ERROR_PATH_NOT_FOUND
        if not os.path.isdir(local_path):
            return encode_error_code(267)  # ERROR_DIRECTORY

        entries: list[Entry552] = []
        try:
            with os.scandir(local_path) as it:
                for entry in it:
                    try:
                        stat = entry.stat()
                        is_d = entry.is_dir()
                        e = make_file_entry(
                            name=entry.name,
                            size=stat.st_size if not is_d else 0,
                            is_dir=is_d,
                            mtime=stat.st_mtime,
                            ctime=stat.st_ctime,
                            atime=stat.st_atime,
                        )
                        entries.append(e)
                    except Exception:
                        continue
            # Sort directories first, then alphabetical
            entries.sort(key=lambda x: (not x.is_dir, x.find_data.filename.lower()))
            return encode_listing_reply(entries)
        except PermissionError:
            return encode_error_code(5)  # ERROR_ACCESS_DENIED
        except Exception:
            return encode_error_code(3)

    def _handle_delete_file(self, file_path: str) -> bytes:
        local_path = self.mapper.wire_to_local(file_path)
        try:
            if os.path.isfile(local_path) or os.path.islink(local_path):
                os.remove(local_path)
                return encode_status_ack(STATUS_OK)
            return encode_error_code(2)  # ERROR_FILE_NOT_FOUND
        except PermissionError:
            return encode_error_code(5)  # ERROR_ACCESS_DENIED
        except Exception:
            return encode_error_code(2)

    def _handle_delete_dir(self, dir_path: str) -> bytes:
        local_path = self.mapper.wire_to_local(dir_path)
        try:
            if os.path.isdir(local_path):
                shutil.rmtree(local_path)
                return encode_status_ack(STATUS_OK)
            return encode_error_code(3)  # ERROR_PATH_NOT_FOUND
        except PermissionError:
            return encode_error_code(5)
        except Exception:
            return encode_error_code(3)

    def _handle_create_dir(self, dir_path: str) -> bytes:
        local_path = self.mapper.wire_to_local(dir_path)
        try:
            os.makedirs(local_path, exist_ok=True)
            return encode_status_ack(STATUS_OK)
        except PermissionError:
            return encode_error_code(5)
        except Exception:
            return encode_error_code(3)

    def _handle_rename(self, old_path: str, new_path: str) -> bytes:
        local_old = self.mapper.wire_to_local(old_path)
        local_new = self.mapper.wire_to_local(new_path)
        try:
            os.rename(local_old, local_new)
            return encode_status_ack(STATUS_OK)
        except PermissionError:
            return encode_error_code(5)
        except FileNotFoundError:
            return encode_error_code(2)
        except Exception:
            return encode_error_code(3)

    def _handle_upload_single(
        self, dest_path: str, find_data: Win32FindData, file_data: bytes
    ) -> bytes:
        local_dest = self.mapper.wire_to_local(dest_path)
        try:
            parent_dir = os.path.dirname(local_dest)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            with open(local_dest, "wb") as f:
                f.write(file_data)

            # Restore timestamps
            mtime = filetime_to_unix(find_data.last_write_time)
            atime = filetime_to_unix(find_data.last_access_time)
            if mtime > 0:
                os.utime(local_dest, (atime if atime > 0 else mtime, mtime))

            return encode_status_ack(STATUS_UPLOAD_SINGLE_DONE)
        except PermissionError:
            return encode_error_code(5)
        except Exception:
            return encode_error_code(112)  # ERROR_DISK_FULL or generic error

    def _handle_download_single(self, src_path: str) -> bytes:
        local_src = self.mapper.wire_to_local(src_path)
        try:
            if not os.path.isfile(local_src):
                return encode_error_code(2)  # ERROR_FILE_NOT_FOUND

            stat = os.stat(local_src)
            with open(local_src, "rb") as f:
                data = f.read()

            fd = Win32FindData(
                file_attributes=128,  # NORMAL
                creation_time=unix_to_filetime(stat.st_ctime),
                last_access_time=unix_to_filetime(stat.st_atime),
                last_write_time=unix_to_filetime(stat.st_mtime),
                file_size=stat.st_size,
                filename=os.path.basename(local_src),
            )
            return encode_download_single_reply(fd, data)
        except PermissionError:
            return encode_error_code(5)
        except Exception:
            return encode_error_code(2)

    def _handle_upload_batch(self, base_dir: str, records: list[Any]) -> bytes:
        local_base = self.mapper.wire_to_local(base_dir)
        try:
            os.makedirs(local_base, exist_ok=True)
            for rec in records:
                rel = rec.rel_path.replace("\\", "/").lstrip("/")
                dest_file = os.path.join(local_base, rel)
                if rec.find_data.is_directory:
                    os.makedirs(dest_file, exist_ok=True)
                else:
                    parent = os.path.dirname(dest_file)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    with open(dest_file, "wb") as f:
                        f.write(rec.data)
                    mtime = filetime_to_unix(rec.find_data.last_write_time)
                    atime = filetime_to_unix(rec.find_data.last_access_time)
                    if mtime > 0:
                        os.utime(dest_file, (atime if atime > 0 else mtime, mtime))
            return encode_status_ack(STATUS_UPLOAD_BATCH_DONE)
        except Exception as exc:
            return encode_upload_batch_error(5, str(exc))

    def _handle_download_batch(self, paths: list[str]) -> list[bytes]:
        stream_records: list[BatchDownloadStreamRecord] = []
        empty_records: list[BatchDownloadEmptyRecord] = []

        for p in paths:
            local_p = self.mapper.wire_to_local(p)
            if not os.path.exists(local_p):
                continue

            if os.path.isdir(local_p):
                # Recursively scan directory
                for root, dirs, files in os.walk(local_p):
                    rel_dir = os.path.relpath(root, os.path.dirname(local_p)).replace("/", "\\")
                    stat_d = os.stat(root)
                    fd_d = Win32FindData(
                        file_attributes=0x10,
                        last_write_time=unix_to_filetime(stat_d.st_mtime),
                        filename=os.path.basename(root),
                    )
                    empty_records.append(BatchDownloadEmptyRecord(rel_path=rel_dir, find_data=fd_d))

                    for fn in files:
                        fp = os.path.join(root, fn)
                        stat_f = os.stat(fp)
                        rel_f = os.path.relpath(fp, os.path.dirname(local_p)).replace("/", "\\")
                        fd_f = Win32FindData(
                            file_attributes=128,
                            last_write_time=unix_to_filetime(stat_f.st_mtime),
                            last_access_time=unix_to_filetime(stat_f.st_atime),
                            creation_time=unix_to_filetime(stat_f.st_ctime),
                            file_size=stat_f.st_size,
                            filename=fn,
                        )
                        if stat_f.st_size == 0:
                            empty_records.append(
                                BatchDownloadEmptyRecord(rel_path=rel_f, find_data=fd_f)
                            )
                        else:
                            with open(fp, "rb") as f:
                                data = f.read()
                            stream_records.append(
                                BatchDownloadStreamRecord(rel_path=rel_f, find_data=fd_f, data=data)
                            )
            else:
                stat_f = os.stat(local_p)
                fn = os.path.basename(local_p)
                fd_f = Win32FindData(
                    file_attributes=128,
                    last_write_time=unix_to_filetime(stat_f.st_mtime),
                    last_access_time=unix_to_filetime(stat_f.st_atime),
                    creation_time=unix_to_filetime(stat_f.st_ctime),
                    file_size=stat_f.st_size,
                    filename=fn,
                )
                if stat_f.st_size == 0:
                    empty_records.append(BatchDownloadEmptyRecord(rel_path=fn, find_data=fd_f))
                else:
                    with open(local_p, "rb") as f:
                        data = f.read()
                    stream_records.append(
                        BatchDownloadStreamRecord(rel_path=fn, find_data=fd_f, data=data)
                    )

        replies: list[bytes] = []
        if empty_records:
            replies.append(encode_download_batch_empty(empty_records))
        if stream_records:
            replies.append(encode_download_batch_stream(stream_records))
        replies.append(encode_download_batch_done())
        return replies

    def _handle_query_space(self, path: str) -> bytes:
        local_p = self.mapper.wire_to_local(path)
        chk_dir = local_p if os.path.isdir(local_p) else os.path.dirname(local_p)
        try:
            usage = shutil.disk_usage(chk_dir or "/")
            entry = make_drive_entry(path, total_bytes=usage.total, free_bytes=usage.free)
            return encode_listing_reply([entry])
        except Exception:
            return encode_error_code(3)

    def _handle_precheck_single(self, path: str) -> bytes:
        local_p = self.mapper.wire_to_local(path)
        if os.path.exists(local_p):
            return encode_status_ack(STATUS_EXISTS)
        return encode_status_ack(STATUS_OK)

    def _handle_precheck_batch(self, dest_dir: str, rel_paths: list[str]) -> bytes:
        local_base = self.mapper.wire_to_local(dest_dir)
        conflicts: list[str] = []
        for rel in rel_paths:
            full = os.path.join(local_base, rel.replace("\\", "/").lstrip("/"))
            if os.path.exists(full):
                conflicts.append(rel)
        return encode_precheck_batch_reply(conflicts)

    def _handle_precheck_batch_ext(self, dest_dir: str, records: list[Any]) -> bytes:
        local_base = self.mapper.wire_to_local(dest_dir)
        conflicts: list[BatchPrecheckExtConflict] = []
        for rec in records:
            full = os.path.join(local_base, rec.rel_path.replace("\\", "/").lstrip("/"))
            if os.path.exists(full):
                stat = os.stat(full)
                peer_fd = Win32FindData(
                    file_attributes=0x10 if os.path.isdir(full) else 128,
                    last_write_time=unix_to_filetime(stat.st_mtime),
                    file_size=stat.st_size if not os.path.isdir(full) else 0,
                    filename=os.path.basename(full),
                )
                conflicts.append(
                    BatchPrecheckExtConflict(
                        rel_path=rec.rel_path,
                        peer_find_data=peer_fd,
                        src_find_data=rec.find_data,
                    )
                )
        return encode_precheck_batch_ext_reply(conflicts)

    def _handle_xml_control(self, xml_str: str) -> bytes:
        parsed = parse_oti_msg(xml_str)
        cmd = parsed.get("command", "")
        if cmd == "CONNECT":
            reply_xml = build_oti_msg(msg_type="RESPONSE", command="CONNECT")
        elif cmd == "DISCONNECT":
            reply_xml = build_oti_msg(msg_type="RESPONSE", command="DISCONNECT")
        else:
            reply_xml = build_oti_msg(msg_type="RESPONSE", command=cmd)
        from openeasysync.protocol.commands import encode_xml_control

        return encode_xml_control(reply_xml)
