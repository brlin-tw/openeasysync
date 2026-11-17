# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""High-level file transfer engine providing single/batch upload and download APIs."""

import os
import struct
from collections.abc import Callable, Iterable
from typing import Any

from openeasysync.client.session import SessionManager
from openeasysync.constants import (
    ENTRY_KIND_DRIVE,
    FILE_ATTRIBUTE_DIRECTORY,
    OP_FILE_CHUNK,
    OP_TRANSFER_COMPLETE,
    OP_UPLOAD_SINGLE,
    PEER_MODE_MAC,
    PEER_MODE_POSIX,
    REPLY_DOWNLOAD_BATCH_DONE,
    REPLY_DOWNLOAD_BATCH_EMPTY,
    REPLY_DOWNLOAD_CHUNK,
    REPLY_DOWNLOAD_UNREADABLE,
    REPLY_LISTING,
    REPLY_PRECHECK_BATCH_EXT,
    REPLY_ROOTS,
    REPLY_STATUS_ACK,
    STATUS_ACTIVE_DIR_ACK,
    STATUS_ERROR,
    STATUS_OK,
)
from openeasysync.protocol.commands import (
    BatchUploadRecord,
    default_peer_flags,
    encode_abort_batch,
    encode_abort_upload,
    encode_create_batch_zero_files,
    encode_create_dir,
    encode_delete_dir,
    encode_delete_file,
    encode_download_batch,
    encode_download_single,
    encode_list_dir,
    encode_notify_active_dir,
    encode_precheck_batch_ext,
    encode_query_root,
    encode_rename_dir,
    encode_rename_file,
)
from openeasysync.protocol.directory_entry import (
    Entry552,
    Win32FindData,
    unix_to_filetime,
)
from openeasysync.protocol.replies import decode_reply, format_win32_error


class TransferManager:
    """Provides high-level file management and transfer methods over an active session."""

    def __init__(self, session: SessionManager):
        self.session = session
        self._next_item_id = 1
        self.peer_mode = default_peer_flags() & (PEER_MODE_MAC | PEER_MODE_POSIX)

    def _get_next_item_id(self) -> int:
        cur = self._next_item_id
        self._next_item_id = (self._next_item_id + 1) & 0x7FFFFFFF or 1
        return cur

    def query_roots(self, flags: int | None = None) -> list[Entry552]:
        """Queries remote peer root drives/volumes (Opcode 0x01)."""
        effective_flags = default_peer_flags() if flags is None else flags
        self.peer_mode = effective_flags & (PEER_MODE_MAC | PEER_MODE_POSIX)
        cmd = encode_query_root(flags=effective_flags)
        rep_bytes = self.session.send_command(
            cmd, expected_opcodes=(REPLY_ROOTS, REPLY_LISTING)
        )
        rep = decode_reply(rep_bytes)
        if rep["opcode"] == REPLY_ROOTS:
            entries: list[Entry552] = []
            if "root_entries" in rep:
                for letter, dtype, display_name, root_path in rep["root_entries"]:
                    fd = Win32FindData(
                        filename=root_path or display_name or f"{letter}:\\",
                        file_attributes=FILE_ATTRIBUTE_DIRECTORY,
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
        if conflict_resolver and records:
            precheck_items: list[tuple[int, str]] = []
            id_to_record: dict[int, BatchUploadRecord] = {}
            total_size = 0
            for r in records:
                item_id = self._get_next_item_id()
                remote_target = os.path.join(remote_base_dir, r.rel_path).replace("/", "\\")
                precheck_items.append((item_id, remote_target))
                id_to_record[item_id] = r
                if not r.find_data.is_directory:
                    total_size += r.find_data.file_size

            req_id = self._get_next_item_id()
            chk_cmd = encode_precheck_batch_ext(
                precheck_items, req_id=req_id, total_size=total_size
            )
            rep_bytes = self.session.send_command(
                chk_cmd, expected_opcodes=(REPLY_PRECHECK_BATCH_EXT,)
            )
            rep = decode_reply(rep_bytes)
            if rep["opcode"] == REPLY_PRECHECK_BATCH_EXT:
                if rep.get("insufficient_space"):
                    raise OSError("Insufficient disk space on remote peer")
                conflict_ids = set(rep["conflict_item_ids"])
                filtered_records = []
                for item_id, _target in precheck_items:
                    r = id_to_record[item_id]
                    if item_id in conflict_ids:
                        action = conflict_resolver(r.rel_path, None)
                        if action.lower() in ("overwrite", "yes", "all"):
                            filtered_records.append(r)
                        elif action.lower() in ("cancel", "abort"):
                            return False
                    else:
                        filtered_records.append(r)
                records = filtered_records

        zero_files: list[tuple[int, str]] = []
        for rec in records:
            remote_target = os.path.join(remote_base_dir, rec.rel_path).replace("/", "\\")
            if rec.find_data.is_directory:
                self.create_dir(remote_target)
            elif rec.find_data.file_size == 0:
                zero_files.append((self._get_next_item_id(), remote_target))
            else:
                parent = os.path.dirname(remote_target)
                if parent:
                    self.create_dir(parent)
                if not self._upload_bytes(remote_target, rec.data, overwrite=True):
                    return False

        if zero_files:
            z_cmd = encode_create_batch_zero_files(zero_files, req_id=self._get_next_item_id())
            z_rep = decode_reply(self.session.send_command(z_cmd, expected_opcodes=(REPLY_STATUS_ACK,)))
            if z_rep.get("status") != STATUS_OK:
                return False
        return True

    def _collect_remote_batch_items(
        self, remote_paths: list[str], local_dest_dir: str
    ) -> list[tuple[int, str, str, bool]]:
        """Expands remote_paths into (item_id, remote_path, local_path, is_dir) tuples."""
        items: list[tuple[int, str, str, bool]] = []
        for rp in remote_paths:
            clean_rp = rp.rstrip("\\/")
            base_name = clean_rp.replace("\\", "/").split("/")[-1]
            local_target = os.path.join(local_dest_dir, base_name)
            try:
                entries = self.list_dir(rp)
                items.append((self._get_next_item_id(), rp, local_target, True))
                stack: list[tuple[str, str, list[Entry552]]] = [(rp, local_target, entries)]
                while stack:
                    cur_r, cur_l, cur_entries = stack.pop()
                    stripped_r = cur_r.rstrip("\\/")
                    for entry in cur_entries:
                        child_r = f"{stripped_r}\\{entry.filename}"
                        child_l = os.path.join(cur_l, entry.filename)
                        if entry.is_dir:
                            items.append((self._get_next_item_id(), child_r, child_l, True))
                            stack.append((child_r, child_l, self.list_dir(child_r)))
                        else:
                            items.append((self._get_next_item_id(), child_r, child_l, False))
            except OSError:
                items.append((self._get_next_item_id(), rp, local_target, False))
        return items

    def download_batch(
        self,
        remote_paths: list[str],
        local_dest_dir: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> bool:
        """Batch downloads files/folders from remote peer into local_dest_dir (Opcode 0x10)."""
        os.makedirs(local_dest_dir, exist_ok=True)
        items = self._collect_remote_batch_items(remote_paths, local_dest_dir)
        if not items:
            return True

        id_map = {item_id: (rpath, lpath, is_dir) for item_id, rpath, lpath, is_dir in items}
        req_id = self._get_next_item_id()
        cmd = encode_download_batch(
            [(item_id, rpath) for item_id, rpath, _lpath, _is_dir in items], req_id=req_id
        )

        self.session.drain_replies()
        self.session.send_packet(cmd)

        open_files: dict[int, Any] = {}
        try:
            while True:
                rep_bytes = self.session.recv_reply(timeout=15.0)
                rep = decode_reply(rep_bytes)
                op = rep["opcode"]

                if op == REPLY_DOWNLOAD_CHUNK:
                    item_id = rep["item_id"]
                    if item_id in id_map:
                        _rpath, lpath, _is_dir = id_map[item_id]
                        if item_id not in open_files:
                            parent = os.path.dirname(lpath)
                            if parent:
                                os.makedirs(parent, exist_ok=True)
                            open_files[item_id] = open(lpath, "wb")
                        open_files[item_id].write(rep["data"])
                        if rep["chunk_index"] + 1 >= rep["total_chunks"]:
                            open_files.pop(item_id).close()

                elif op == REPLY_DOWNLOAD_BATCH_EMPTY:
                    for item_id in rep["item_ids"]:
                        if item_id in id_map:
                            _rpath, lpath, is_dir = id_map[item_id]
                            if is_dir:
                                os.makedirs(lpath, exist_ok=True)
                            else:
                                parent = os.path.dirname(lpath)
                                if parent:
                                    os.makedirs(parent, exist_ok=True)
                                with open(lpath, "wb"):
                                    pass

                elif op == REPLY_DOWNLOAD_UNREADABLE:
                    continue

                elif op == REPLY_DOWNLOAD_BATCH_DONE:
                    return rep.get("status", 0) == 0
        finally:
            for f in open_files.values():
                try:
                    f.close()
                except Exception:
                    pass

    def cancel_transfer(
        self, item_id: int = 1, win32_error: int = 0, req_id: int | None = None
    ) -> bool:
        """Sends fire-and-forget transfer abort/cancel signal (Opcode 0x0D or 0x11)."""
        err_str = format_win32_error(win32_error) if self.peer_mode else None
        if req_id is not None:
            cmd = encode_abort_batch(req_id=req_id, win32_error=win32_error, err_str=err_str)
        else:
            cmd = encode_abort_upload(item_id=item_id, win32_error=win32_error, err_str=err_str)
        try:
            self.session.send_packet(cmd)
            return True
        except Exception:
            return False
