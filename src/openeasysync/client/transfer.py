# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""High-level file transfer engine providing single/batch upload and download APIs."""

import os
from collections.abc import Callable

from openeasysync.client.session import SessionManager
from openeasysync.constants import (
    REPLY_DOWNLOAD_BATCH_EMPTY,
    REPLY_DOWNLOAD_BATCH_STREAM,
    REPLY_DOWNLOAD_SINGLE,
    REPLY_ERROR_CODE,
    REPLY_LISTING,
    REPLY_PRECHECK_BATCH_EXT,
    REPLY_STATUS_ACK,
    STATUS_EXISTS,
    STATUS_OK,
    STATUS_UPLOAD_BATCH_DONE,
    STATUS_UPLOAD_SINGLE_DONE,
)
from openeasysync.protocol.commands import (
    BatchPrecheckExtRecord,
    BatchUploadRecord,
    encode_cancel_transfer,
    encode_create_dir,
    encode_delete_dir,
    encode_delete_file,
    encode_download_batch,
    encode_download_single,
    encode_list_dir,
    encode_precheck_batch_ext,
    encode_precheck_single,
    encode_query_root,
    encode_rename_dir,
    encode_rename_file,
    encode_upload_batch,
    encode_upload_single,
)
from openeasysync.protocol.directory_entry import (
    Entry552,
    Win32FindData,
    filetime_to_unix,
    unix_to_filetime,
)
from openeasysync.protocol.replies import decode_reply


class TransferManager:
    """Provides high-level file management and transfer methods over an active session."""

    def __init__(self, session: SessionManager):
        self.session = session

    def query_roots(self) -> list[Entry552]:
        """Queries remote peer root drives/volumes (Opcode 0x01)."""
        cmd = encode_query_root()
        rep_bytes = self.session.send_command(cmd)
        rep = decode_reply(rep_bytes)
        if rep["opcode"] == REPLY_LISTING:
            return rep["entries"]
        if rep["opcode"] == REPLY_ERROR_CODE:
            raise OSError(f"Remote error querying roots: {rep['win32_error']}")
        return []

    def list_dir(self, remote_path: str) -> list[Entry552]:
        """Lists directory contents on remote peer (Opcode 0x02)."""
        cmd = encode_list_dir(remote_path)
        rep_bytes = self.session.send_command(cmd)
        rep = decode_reply(rep_bytes)
        if rep["opcode"] == REPLY_LISTING:
            return rep["entries"]
        if rep["opcode"] == REPLY_ERROR_CODE:
            raise OSError(f"Remote error listing '{remote_path}': Win32 error {rep['win32_error']}")
        return []

    def create_dir(self, remote_path: str) -> bool:
        """Creates a directory on remote peer (Opcode 0x06)."""
        cmd = encode_create_dir(remote_path)
        rep_bytes = self.session.send_command(cmd)
        rep = decode_reply(rep_bytes)
        return rep["opcode"] == REPLY_STATUS_ACK and rep["status"] == STATUS_OK

    def delete_file(self, remote_path: str) -> bool:
        """Deletes a file on remote peer (Opcode 0x03)."""
        cmd = encode_delete_file(remote_path)
        rep_bytes = self.session.send_command(cmd)
        rep = decode_reply(rep_bytes)
        return rep["opcode"] == REPLY_STATUS_ACK and rep["status"] == STATUS_OK

    def delete_dir(self, remote_path: str) -> bool:
        """Recursively deletes a directory on remote peer (Opcode 0x04)."""
        cmd = encode_delete_dir(remote_path)
        rep_bytes = self.session.send_command(cmd)
        rep = decode_reply(rep_bytes)
        return rep["opcode"] == REPLY_STATUS_ACK and rep["status"] == STATUS_OK

    def rename_file(self, old_path: str, new_path: str) -> bool:
        """Renames a file on remote peer (Opcode 0x07)."""
        cmd = encode_rename_file(old_path, new_path)
        rep_bytes = self.session.send_command(cmd)
        rep = decode_reply(rep_bytes)
        return rep["opcode"] == REPLY_STATUS_ACK and rep["status"] == STATUS_OK

    def rename_dir(self, old_path: str, new_path: str) -> bool:
        """Renames a directory on remote peer (Opcode 0x08)."""
        cmd = encode_rename_dir(old_path, new_path)
        rep_bytes = self.session.send_command(cmd)
        rep = decode_reply(rep_bytes)
        return rep["opcode"] == REPLY_STATUS_ACK and rep["status"] == STATUS_OK

    def precheck_single(self, remote_dest_path: str) -> bool:
        """Checks if destination file already exists on peer (Opcode 0x0F).

        Returns True if file exists, False otherwise.
        """
        cmd = encode_precheck_single(remote_dest_path)
        rep_bytes = self.session.send_command(cmd)
        rep = decode_reply(rep_bytes)
        if rep["opcode"] == REPLY_STATUS_ACK:
            return rep["status"] == STATUS_EXISTS
        return False

    def upload_file(
        self,
        local_path: str,
        remote_dest_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Uploads a single file to remote peer (Opcode 0x09)."""
        stat = os.stat(local_path)
        with open(local_path, "rb") as f:
            file_data = f.read()

        if progress_callback:
            progress_callback(len(file_data), len(file_data))

        fd = Win32FindData(
            file_attributes=128,
            creation_time=unix_to_filetime(stat.st_ctime),
            last_access_time=unix_to_filetime(stat.st_atime),
            last_write_time=unix_to_filetime(stat.st_mtime),
            file_size=stat.st_size,
            filename=os.path.basename(local_path),
        )

        cmd = encode_upload_single(remote_dest_path, fd, file_data)
        rep_bytes = self.session.send_command(cmd)
        rep = decode_reply(rep_bytes)

        if rep["opcode"] == REPLY_STATUS_ACK:
            return rep["status"] in (STATUS_OK, STATUS_UPLOAD_SINGLE_DONE)
        if rep["opcode"] == REPLY_ERROR_CODE:
            raise OSError(f"Upload failed: Win32 error {rep['win32_error']}")
        return False

    def download_file(
        self,
        remote_src_path: str,
        local_dest_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Downloads a single file from remote peer (Opcode 0x0A)."""
        cmd = encode_download_single(remote_src_path)
        rep_bytes = self.session.send_command(cmd)
        rep = decode_reply(rep_bytes)

        if rep["opcode"] == REPLY_ERROR_CODE:
            raise OSError(f"Download failed: Win32 error {rep['win32_error']}")

        if rep["opcode"] == REPLY_DOWNLOAD_SINGLE:
            parent = os.path.dirname(local_dest_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            with open(local_dest_path, "wb") as f:
                f.write(rep["file_data"])

            if progress_callback:
                progress_callback(len(rep["file_data"]), len(rep["file_data"]))

            # Restore timestamps
            mtime = filetime_to_unix(rep["find_data"].last_write_time)
            atime = filetime_to_unix(rep["find_data"].last_access_time)
            if mtime > 0:
                os.utime(local_dest_path, (atime if atime > 0 else mtime, mtime))
            return True

        return False

    def upload_batch(
        self,
        local_paths: list[str],
        remote_base_dir: str,
        conflict_resolver: Callable[[str, Win32FindData | None], str] | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> bool:
        """Recursively batch uploads multiple files/directories into remote_base_dir (Opcode 0x0B)."""
        records: list[BatchUploadRecord] = []

        for p in local_paths:
            if not os.path.exists(p):
                continue
            if os.path.isdir(p):
                base_parent = os.path.dirname(p)
                for root, dirs, files in os.walk(p):
                    rel_dir = os.path.relpath(root, base_parent).replace("/", "\\")
                    stat_d = os.stat(root)
                    fd_d = Win32FindData(
                        file_attributes=0x10,
                        last_write_time=unix_to_filetime(stat_d.st_mtime),
                        filename=os.path.basename(root),
                    )
                    records.append(BatchUploadRecord(rel_path=rel_dir, find_data=fd_d))

                    for fn in files:
                        fp = os.path.join(root, fn)
                        stat_f = os.stat(fp)
                        rel_f = os.path.relpath(fp, base_parent).replace("/", "\\")
                        with open(fp, "rb") as f:
                            data = f.read()
                        fd_f = Win32FindData(
                            file_attributes=128,
                            last_write_time=unix_to_filetime(stat_f.st_mtime),
                            last_access_time=unix_to_filetime(stat_f.st_atime),
                            creation_time=unix_to_filetime(stat_f.st_ctime),
                            file_size=stat_f.st_size,
                            filename=fn,
                        )
                        records.append(BatchUploadRecord(rel_path=rel_f, find_data=fd_f, data=data))
            else:
                stat_f = os.stat(p)
                fn = os.path.basename(p)
                with open(p, "rb") as f:
                    data = f.read()
                fd_f = Win32FindData(
                    file_attributes=128,
                    last_write_time=unix_to_filetime(stat_f.st_mtime),
                    last_access_time=unix_to_filetime(stat_f.st_atime),
                    creation_time=unix_to_filetime(stat_f.st_ctime),
                    file_size=stat_f.st_size,
                    filename=fn,
                )
                records.append(BatchUploadRecord(rel_path=fn, find_data=fd_f, data=data))

        # Check conflicts if conflict_resolver is provided
        if conflict_resolver:
            ext_recs = [
                BatchPrecheckExtRecord(rel_path=r.rel_path, find_data=r.find_data) for r in records
            ]
            chk_cmd = encode_precheck_batch_ext(remote_base_dir, ext_recs)
            rep_bytes = self.session.send_command(chk_cmd)
            rep = decode_reply(rep_bytes)
            if rep["opcode"] == REPLY_PRECHECK_BATCH_EXT:
                conflicts = {c.rel_path: c.peer_find_data for c in rep["conflicts"]}
                filtered_records = []
                for r in records:
                    if r.rel_path in conflicts:
                        action = conflict_resolver(r.rel_path, conflicts[r.rel_path])
                        if action.lower() in ("overwrite", "yes", "all"):
                            filtered_records.append(r)
                        elif action.lower() in ("cancel", "abort"):
                            return False
                    else:
                        filtered_records.append(r)
                records = filtered_records

        cmd = encode_upload_batch(remote_base_dir, records)
        rep_bytes = self.session.send_command(cmd)
        rep = decode_reply(rep_bytes)
        return rep["opcode"] == REPLY_STATUS_ACK and rep["status"] in (
            STATUS_OK,
            STATUS_UPLOAD_BATCH_DONE,
        )

    def download_batch(
        self,
        remote_paths: list[str],
        local_dest_dir: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> bool:
        """Batch downloads files/folders from remote peer into local_dest_dir (Opcode 0x0C)."""
        os.makedirs(local_dest_dir, exist_ok=True)
        cmd = encode_download_batch(remote_paths)
        replies = self.session.send_command_multi_reply(cmd)

        for rep_bytes in replies:
            rep = decode_reply(rep_bytes)
            op = rep["opcode"]

            if op == REPLY_DOWNLOAD_BATCH_EMPTY:
                for item in rep["records"]:
                    dest = os.path.join(
                        local_dest_dir, item.rel_path.replace("\\", "/").lstrip("/")
                    )
                    if item.find_data.is_directory:
                        os.makedirs(dest, exist_ok=True)
                    else:
                        parent = os.path.dirname(dest)
                        if parent:
                            os.makedirs(parent, exist_ok=True)
                        with open(dest, "wb") as f:
                            pass
                    mtime = filetime_to_unix(item.find_data.last_write_time)
                    if mtime > 0:
                        os.utime(dest, (mtime, mtime))

            elif op == REPLY_DOWNLOAD_BATCH_STREAM:
                for item in rep["records"]:
                    dest = os.path.join(
                        local_dest_dir, item.rel_path.replace("\\", "/").lstrip("/")
                    )
                    parent = os.path.dirname(dest)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    with open(dest, "wb") as f:
                        f.write(item.data)
                    mtime = filetime_to_unix(item.find_data.last_write_time)
                    atime = filetime_to_unix(item.find_data.last_access_time)
                    if mtime > 0:
                        os.utime(dest, (atime if atime > 0 else mtime, mtime))

        return True

    def cancel_transfer(self) -> bool:
        """Sends transfer abort/cancel signal (Opcode 0x0D)."""
        cmd = encode_cancel_transfer()
        try:
            rep_bytes = self.session.send_command(cmd, timeout=2.0)
            rep = decode_reply(rep_bytes)
            return rep["opcode"] == REPLY_STATUS_ACK
        except Exception:
            return False
