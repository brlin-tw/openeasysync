# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""High-level file transfer engine providing single/batch upload and download APIs."""

import os
import struct
from collections.abc import Callable, Iterable

from openeasysync.client.session import SessionManager
from openeasysync.constants import (
    ENTRY_KIND_DRIVE,
    FILE_ATTRIBUTE_DIRECTORY,
    OP_FILE_CHUNK,
    OP_TRANSFER_COMPLETE,
    OP_UPLOAD_SINGLE,
    REPLY_DOWNLOAD_BATCH_EMPTY,
    REPLY_DOWNLOAD_BATCH_STREAM,
    REPLY_DOWNLOAD_CHUNK,
    REPLY_DOWNLOAD_SINGLE,
    REPLY_ERROR_CODE,
    REPLY_LISTING,
    REPLY_PRECHECK_BATCH_EXT,
    REPLY_ROOTS,
    REPLY_STATUS_ACK,
    STATUS_ACTIVE_DIR_ACK,
    STATUS_ERROR,
    STATUS_EXISTS,
    STATUS_OK,
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
    encode_notify_active_dir,
    encode_precheck_batch_ext,
    encode_precheck_single,
    encode_query_root,
    encode_rename_dir,
    encode_rename_file,
)
from openeasysync.protocol.directory_entry import (
    Entry552,
    Win32FindData,
    filetime_to_unix,
    unix_to_filetime,
)
from openeasysync.protocol.replies import decode_reply, format_win32_error


class TransferManager:
    """Provides high-level file management and transfer methods over an active session."""

    def __init__(self, session: SessionManager):
        self.session = session
        self._next_item_id = 1

    def _get_next_item_id(self) -> int:
        cur = self._next_item_id
        self._next_item_id = (self._next_item_id + 1) & 0x7FFFFFFF or 1
        return cur

    def query_roots(self) -> list[Entry552]:
        """Queries remote peer root drives/volumes (Opcode 0x01)."""
        cmd = encode_query_root()
        rep_bytes = self.session.send_command(
            cmd, expected_opcodes=(REPLY_ROOTS, REPLY_LISTING, REPLY_ERROR_CODE)
        )
        rep = decode_reply(rep_bytes)
        if rep["opcode"] == REPLY_ROOTS:
            entries: list[Entry552] = []
            for letter, dtype in rep["roots"]:
                fd = Win32FindData(
                    filename=f"{letter}:\\", file_attributes=FILE_ATTRIBUTE_DIRECTORY
                )
                entry = Entry552(
                    entry_kind=ENTRY_KIND_DRIVE,
                    find_data=fd,
                    drive_type=dtype,
                    total_bytes=0,
                    total_free_bytes=0,
                )
                entries.append(entry)
            return entries
        if rep["opcode"] == REPLY_LISTING:
            return rep["entries"]
        if rep["opcode"] == REPLY_STATUS_ACK and rep.get("status") == STATUS_ERROR:
            win32_err = rep.get("win32_error", 0)
            raise OSError(f"Remote error querying roots: {format_win32_error(win32_err)}")
        if rep["opcode"] == REPLY_ERROR_CODE:
            win32_err = rep.get("win32_error", 0)
            raise OSError(f"Remote error querying roots: {format_win32_error(win32_err)}")
        return []

    def notify_active_dir(self, remote_path: str) -> bool:
        """Notifies remote peer of active local directory (Opcode 0x02)."""
        cmd = encode_notify_active_dir(remote_path)
        rep_bytes = self.session.send_command(cmd)
        rep = decode_reply(rep_bytes)
        return rep["opcode"] == REPLY_STATUS_ACK and rep["status"] in (
            STATUS_OK,
            STATUS_ACTIVE_DIR_ACK,
        )

    def list_dir(self, remote_path: str) -> list[Entry552]:
        """Lists directory contents on remote peer (Opcode 0x04)."""
        cmd = encode_list_dir(remote_path)
        rep_bytes = self.session.send_command(cmd, expected_opcodes=(REPLY_LISTING,))
        rep = decode_reply(rep_bytes)
        if rep["opcode"] == REPLY_LISTING:
            return rep["entries"]
        if rep["opcode"] == REPLY_STATUS_ACK and rep.get("status") == STATUS_ERROR:
            win32_err = rep.get("win32_error", 0)
            err_desc = format_win32_error(win32_err)
            raise OSError(f"{remote_path} : {err_desc}")
        if rep["opcode"] == REPLY_ERROR_CODE:
            win32_err = rep.get("win32_error", 0)
            err_desc = format_win32_error(win32_err)
            raise OSError(f"{remote_path} : {err_desc}")
        return []

    def create_dir(self, remote_path: str, req_id: int = 1) -> bool:
        """Creates a directory on remote peer (Opcode 0x07)."""
        cmd = encode_create_dir(remote_path, req_id=req_id)
        rep_bytes = self.session.send_command(cmd, expected_opcodes=(REPLY_STATUS_ACK,))
        rep = decode_reply(rep_bytes)
        return rep["opcode"] == REPLY_STATUS_ACK and rep["status"] == STATUS_OK

    def delete_file(self, remote_path: str, req_id: int = 1) -> bool:
        """Deletes a file on remote peer (Opcode 0x08)."""
        cmd = encode_delete_file(remote_path, req_id=req_id)
        rep_bytes = self.session.send_command(cmd, expected_opcodes=(REPLY_STATUS_ACK,))
        rep = decode_reply(rep_bytes)
        return rep["opcode"] == REPLY_STATUS_ACK and rep["status"] == STATUS_OK

    def delete_dir(self, remote_path: str, req_id: int = 1) -> bool:
        """Recursively deletes a directory on remote peer (Opcode 0x06)."""
        cmd = encode_delete_dir(remote_path, req_id=req_id)
        rep_bytes = self.session.send_command(cmd, expected_opcodes=(REPLY_STATUS_ACK,))
        rep = decode_reply(rep_bytes)
        return rep["opcode"] == REPLY_STATUS_ACK and rep["status"] == STATUS_OK

    def rename_file(self, old_path: str, new_path: str, req_id: int = 1) -> bool:
        """Renames a file on remote peer (Opcode 0x09)."""
        cmd = encode_rename_file(old_path, new_path, req_id=req_id)
        rep_bytes = self.session.send_command(cmd, expected_opcodes=(REPLY_STATUS_ACK,))
        rep = decode_reply(rep_bytes)
        return rep["opcode"] == REPLY_STATUS_ACK and rep["status"] == STATUS_OK

    def rename_dir(self, old_path: str, new_path: str, req_id: int = 1) -> bool:
        """Renames a directory on remote peer (Opcode 0x09)."""
        cmd = encode_rename_dir(old_path, new_path, req_id=req_id)
        rep_bytes = self.session.send_command(cmd, expected_opcodes=(REPLY_STATUS_ACK,))
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

    def _stream_upload(
        self,
        dest_path_str: str,
        file_size: int,
        overwrite: bool,
        chunk_iter: Iterable[bytes],
        chunk_size: int = 64000,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Streams a file upload using Opcodes 0x0A (sync ACK), 0x0C (async), and 0x0E."""
        dest_path_bytes = dest_path_str.encode("utf-16le")
        if len(dest_path_bytes) > 1024:
            raise ValueError("Destination path exceeds 1024 bytes (EasySuite stack limit)")

        req_id = self._get_next_item_id()
        item_id = req_id

        # 1. Pack Opcode 0x0A: <B B I Q H
        header = struct.pack(
            "<B B I Q H",
            OP_UPLOAD_SINGLE,
            0x01 if overwrite else 0x00,
            req_id,
            file_size,
            len(dest_path_bytes),
        )
        cmd_packet = header + dest_path_bytes

        # 2. Wait for Opcode 0x0A ACK (0xA1): Expect 24-byte reply
        rep_bytes = self.session.send_command(cmd_packet, expected_opcodes=(REPLY_STATUS_ACK,))
        if len(rep_bytes) < 24 or rep_bytes[0] != REPLY_STATUS_ACK:
            raise OSError(
                f"Invalid Opcode 0x0A reply (expected 24-byte 0xA1, got {len(rep_bytes)} bytes)"
            )

        req_opcode, status, reply_req_id = struct.unpack_from("<BBI", rep_bytes, 1)

        if status == 7:
            if progress_callback:
                progress_callback(0, 0)
            return True

        if status == 2:
            raise FileExistsError(f"Destination file already exists on peer: {dest_path_str}")

        if status == 6:
            raise OSError(f"Remote disk is full: {dest_path_str}")

        if status == 1:
            win32_err = struct.unpack_from("<I", rep_bytes, 7)[0]
            raise OSError(f"Upload failed: {dest_path_str} : {format_win32_error(win32_err)}")

        if status != 0:
            raise OSError(f"Upload failed with status {status}: {dest_path_str}")

        # 3. Stream Chunks asynchronously via Opcode 0x0C without awaiting per-chunk replies
        self.session.clear_upload_error()
        total_chunks = (file_size + chunk_size - 1) // chunk_size if file_size > 0 else 1

        bytes_sent = 0
        for chunk_index, chunk_data in enumerate(chunk_iter, start=1):
            self.session.check_upload_error()
            chunk_header = struct.pack(
                "<B I I I I",
                OP_FILE_CHUNK,
                item_id,
                chunk_index,
                total_chunks,
                len(chunk_data),
            )
            self.session.send_packet(chunk_header + chunk_data)

            bytes_sent += len(chunk_data)
            if progress_callback:
                progress_callback(bytes_sent, file_size)

        # 4. Finalize via Opcode 0x0E (no reply emitted) and ensure TX drains cleanly
        self.session.check_upload_error()
        self.session.send_packet(struct.pack("<B I H", OP_TRANSFER_COMPLETE, item_id, 0))
        self.session.flush_tx()
        return True

    def _upload_bytes(
        self,
        remote_dest_path: str,
        file_data: bytes,
        overwrite: bool = True,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Internal helper streaming in-memory file bytes using Opcodes 0x0A, 0x0C, and 0x0E."""
        file_size = len(file_data)
        dest_path_str = remote_dest_path.replace("/", "\\")
        chunk_size = 64000
        total_chunks = (file_size + chunk_size - 1) // chunk_size if file_size > 0 else 1

        def _iter_slices() -> Iterable[bytes]:
            for chunk_index in range(1, total_chunks + 1):
                start = (chunk_index - 1) * chunk_size
                end = min(start + chunk_size, file_size)
                yield file_data[start:end]

        return self._stream_upload(
            dest_path_str=dest_path_str,
            file_size=file_size,
            overwrite=overwrite,
            chunk_iter=_iter_slices(),
            chunk_size=chunk_size,
            progress_callback=progress_callback,
        )

    def upload_file(
        self,
        local_path: str,
        remote_dest_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
        overwrite: bool = False,
    ) -> bool:
        """Uploads a single file to remote peer using Opcodes 0x0A, 0x0C, and 0x0E."""
        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        file_size = os.path.getsize(local_path)
        filename = os.path.basename(local_path)

        if remote_dest_path.endswith("\\") or remote_dest_path.endswith("/"):
            cleaned_dest = remote_dest_path.rstrip("\\/")
            dest_path_str = f"{cleaned_dest}\\{filename}"
        elif "\\" in remote_dest_path or "/" in remote_dest_path:
            dest_norm = remote_dest_path.replace("/", "\\")
            dest_base = os.path.basename(dest_norm.rstrip("\\"))
            if "." in filename and "." not in dest_base:
                cleaned_dest = dest_norm.rstrip("\\")
                dest_path_str = f"{cleaned_dest}\\{filename}"
            else:
                dest_path_str = dest_norm
        else:
            cleaned_dest = remote_dest_path.rstrip("\\")
            dest_path_str = f"{cleaned_dest}\\{filename}"

        chunk_size = 64000
        total_chunks = (file_size + chunk_size - 1) // chunk_size if file_size > 0 else 1

        with open(local_path, "rb") as f:

            def _iter_file_chunks() -> Iterable[bytes]:
                for _ in range(total_chunks):
                    yield f.read(chunk_size)

            return self._stream_upload(
                dest_path_str=dest_path_str,
                file_size=file_size,
                overwrite=overwrite,
                chunk_iter=_iter_file_chunks(),
                chunk_size=chunk_size,
                progress_callback=progress_callback,
            )

    def download_file(
        self,
        remote_src_path: str,
        local_dest_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Downloads a single file from remote peer (Opcode 0x0B)."""
        req_id = self._get_next_item_id()

        # 1. Pack Opcode 0x0B Wire Framing
        cmd_packet = encode_download_single(
            remote_path=remote_src_path,
            local_path=local_dest_path,
            req_id=req_id,
        )

        parent = os.path.dirname(local_dest_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        self.session.drain_replies()
        self.session.send_packet(cmd_packet)

        # 2. Handle Server Reply (0xA1 vs. 0xA3)
        reply = self.session.recv_reply(timeout=10.0)
        opcode = reply[0]

        if opcode == REPLY_STATUS_ACK:
            req_opcode, status, reply_req_id = struct.unpack_from("<B B I", reply, 1)
            if status == 1:
                win32_err = struct.unpack_from("<I", reply, 7)[0]
                raise FileNotFoundError(f"Download failed on peer with Win32 error {win32_err}")
            elif status == 7:
                # Empty (0-byte) file
                with open(local_dest_path, "wb") as f:
                    pass
                if progress_callback:
                    progress_callback(0, 0)
                return True
            elif status == 9:
                # Small file inline delivery (<= 64,512 bytes)
                data_len = struct.unpack_from("<I", reply, 0x14)[0]
                file_data = reply[0x18 : 0x18 + data_len]
                with open(local_dest_path, "wb") as f:
                    f.write(file_data)
                if progress_callback:
                    progress_callback(len(file_data), len(file_data))
                return True
            elif status == 0:
                # Large file (> 64,512 bytes): proceed to receive 0xA3 chunks
                file_size = struct.unpack_from("<Q", reply, 0x0B)[0]
                self._receive_a3_chunks(local_dest_path, file_size, progress_callback)
                return True
            else:
                raise OSError(f"Download failed with unexpected status {status}: {remote_src_path}")

        elif opcode == REPLY_DOWNLOAD_CHUNK:
            self._receive_a3_chunks(local_dest_path, 0, progress_callback, initial_chunk=reply)
            return True

        elif opcode == REPLY_DOWNLOAD_SINGLE:  # legacy 0xA4 fallback
            rep = decode_reply(reply)
            with open(local_dest_path, "wb") as f:
                f.write(rep["file_data"])
            if progress_callback:
                progress_callback(len(rep["file_data"]), len(rep["file_data"]))
            return True

        return False

    def _receive_a3_chunks(
        self,
        local_dest: str,
        file_size: int,
        progress_callback: Callable[[int, int], None] | None = None,
        initial_chunk: bytes | None = None,
    ) -> None:
        parent = os.path.dirname(local_dest)
        if parent:
            os.makedirs(parent, exist_ok=True)

        bytes_received = 0
        with open(local_dest, "wb") as f:
            if initial_chunk is not None:
                assert initial_chunk[0] == REPLY_DOWNLOAD_CHUNK
                item_id, chunk_idx, total_chunks, chunk_len = struct.unpack_from(
                    "<I I I I", initial_chunk, 1
                )
                chunk_data = initial_chunk[17 : 17 + chunk_len]
                f.write(chunk_data)
                bytes_received += len(chunk_data)
                if progress_callback:
                    progress_callback(bytes_received, file_size)
                if chunk_idx + 1 >= total_chunks:
                    return

            while True:
                chunk_pkt = self.session.recv_reply(timeout=15.0)
                assert chunk_pkt[0] == REPLY_DOWNLOAD_CHUNK
                item_id, chunk_idx, total_chunks, chunk_len = struct.unpack_from(
                    "<I I I I", chunk_pkt, 1
                )
                chunk_data = chunk_pkt[17 : 17 + chunk_len]
                f.write(chunk_data)
                bytes_received += len(chunk_data)
                if progress_callback:
                    progress_callback(bytes_received, file_size)
                if chunk_idx + 1 >= total_chunks:
                    break

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

        for rec in records:
            remote_target = os.path.join(remote_base_dir, rec.rel_path).replace("/", "\\")
            if rec.find_data.is_directory:
                self.create_dir(remote_target)
            else:
                parent = os.path.dirname(remote_target)
                if parent:
                    self.create_dir(parent)
                if not self._upload_bytes(remote_target, rec.data, overwrite=True):
                    return False
        return True

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
