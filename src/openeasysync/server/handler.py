# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Peer server request dispatcher executing local filesystem operations for the remote peer."""

import errno
import os
import shutil
import sys
from collections.abc import Callable
from typing import Any

from openeasysync.constants import (
    DRIVE_FIXED,
    DRIVE_REMOVABLE,
    FILE_ATTRIBUTE_READONLY,
    OP_ABORT_BATCH,
    OP_ABORT_UPLOAD,
    OP_BATCH_CLEAR_READONLY,
    OP_BATCH_DOWNLOAD_IDS_16,
    OP_BATCH_DOWNLOAD_IDS_32,
    OP_CANCEL_KEEP_PARTIAL,
    OP_CANCEL_SPLITTER,
    OP_CREATE_BATCH_ZERO_FILES,
    OP_CREATE_DIR,
    OP_DELETE_DIR,
    OP_DELETE_FILE,
    OP_DOWNLOAD_BATCH,
    OP_DOWNLOAD_SINGLE,
    OP_EXTENDED_XML_OPCODES,
    OP_FILE_CHUNK,
    OP_FORMAT_WIN32_ERRORS,
    OP_LIST_DIR,
    OP_NOTIFY_ACTIVE_DIR,
    OP_PRECHECK_BATCH,
    OP_PRECHECK_BATCH_EXT,
    OP_QUERY_CURRENT_DIR,
    OP_QUERY_ROOT,
    OP_RENAME_DIR,
    OP_RENAME_FILE,
    OP_SET_FILE_ATTRS,
    OP_TRANSFER_COMPLETE,
    OP_UI_NOTIFICATION,
    OP_UPLOAD_SINGLE,
    OP_VERIFY_PEER_NAME,
    OP_VNIC_SWITCH,
    OP_XML_CONTROL,
    PEER_MODE_MAC,
    PEER_MODE_POSIX,
    PEER_MODE_WINDOWS,
    STATUS_ACTIVE_DIR_ACK,
    STATUS_CONFLICT,
    STATUS_DISK_FULL,
    STATUS_ERROR,
    STATUS_EXISTS,
    STATUS_INLINE_SMALL_FILE,
    STATUS_OK,
    STATUS_UPLOAD_SINGLE_DONE,
    STATUS_ZERO_LENGTH,
)
from openeasysync.protocol.commands import (
    VNIC_CMD_ACCEPT_SWITCH,
    VNIC_CMD_ME_SWITCH_NONE_ROUTER,
    VNIC_CMD_ME_SWITCH_ROUTER,
    VNIC_CMD_PLEASE_SWITCH,
    VNIC_CMD_REJECT_SWITCH,
    decode_command,
    encode_extended_xml,
    parse_extra_xml_command,
)
from openeasysync.protocol.directory_entry import (
    Entry552,
    filetime_to_unix,
    make_file_entry,
)
from openeasysync.protocol.path_mapping import PathMapper
from openeasysync.protocol.replies import (
    encode_download_batch_done,
    encode_download_batch_empty,
    encode_download_chunk,
    encode_download_unreadable,
    encode_error_code,
    encode_formatted_errors,
    encode_listing_reply,
    encode_precheck_batch_ext_reply,
    encode_precheck_batch_reply,
    encode_roots_reply,
    encode_status_ack,
    encode_upload_batch_error,
    encode_upload_error,
    format_win32_error,
)
from openeasysync.protocol.xml_msg import build_oti_msg, parse_oti_msg


class PeerServerHandler:
    """Handles incoming Client Commands from a remote peer and returns reply payloads."""

    def __init__(
        self,
        path_mapper: PathMapper | None = None,
        *,
        allow_vnic_switch: bool = False,
        on_vnic_switch_request: Callable[[str], bool] | None = None,
        on_vnic_mode_entered: Callable[[bool], None] | None = None,
    ):
        self.mapper = path_mapper or PathMapper()
        self.allow_vnic_switch = allow_vnic_switch
        self.on_vnic_switch_request = on_vnic_switch_request
        self.on_vnic_mode_entered = on_vnic_mode_entered
        self.vnic_switch_accepted = False
        self.vnic_peer_is_router: bool | None = None
        self.is_cancelled = False
        self.peer_active_dir = ""
        self.peer_mode = PEER_MODE_WINDOWS
        self._active_uploads: dict[int, dict[str, Any]] = {}
        self._batch_item_ids: set[int] = set()
        self._batch_upload_paths: set[str] = set()

    def handle_command(self, payload: bytes) -> list[bytes]:
        """Dispatches a command payload and returns a list of reply payload(s)."""
        if not payload:
            return [encode_error_code(1)]  # ERROR_INVALID_FUNCTION

        cmd = decode_command(payload)
        opcode = cmd["opcode"]

        if opcode == OP_QUERY_ROOT:
            return self._handle_query_root(flags=cmd.get("flags", 0))

        if opcode in (OP_NOTIFY_ACTIVE_DIR, OP_VERIFY_PEER_NAME):
            self.peer_active_dir = cmd.get("path", "")
            return [
                encode_status_ack(req_opcode=OP_NOTIFY_ACTIVE_DIR, status=STATUS_ACTIVE_DIR_ACK)
            ]

        if opcode == OP_QUERY_CURRENT_DIR:
            return self._handle_refresh_dir()

        if opcode == OP_LIST_DIR:
            return self._handle_list_dir(cmd["path"])

        if opcode == OP_DELETE_FILE:
            return [self._handle_delete_file(cmd["path"], req_id=cmd.get("req_id", 0))]

        if opcode == OP_DELETE_DIR:
            return [self._handle_delete_dir(cmd["path"], req_id=cmd.get("req_id", 0))]

        if opcode == OP_CREATE_DIR:
            return [self._handle_create_dir(cmd["path"], req_id=cmd.get("req_id", 0))]

        if opcode in (OP_RENAME_FILE, OP_RENAME_DIR):
            return [
                self._handle_rename(cmd["old_path"], cmd["new_path"], req_id=cmd.get("req_id", 0))
            ]

        if opcode == OP_UPLOAD_SINGLE:
            return self._handle_upload_single(cmd)

        if opcode == OP_FILE_CHUNK:
            return self._handle_file_chunk(cmd)

        if opcode == OP_TRANSFER_COMPLETE:
            return self._handle_transfer_complete(cmd)

        if opcode == OP_ABORT_UPLOAD:
            return self._handle_abort_upload(cmd)

        if opcode == OP_CANCEL_KEEP_PARTIAL:
            return self._handle_cancel_keep_partial(cmd)

        if opcode == OP_DOWNLOAD_SINGLE:
            return self._handle_download_single(cmd)

        if opcode in (OP_DOWNLOAD_BATCH, OP_BATCH_DOWNLOAD_IDS_16, OP_BATCH_DOWNLOAD_IDS_32):
            return self._handle_download_batch(
                cmd.get("records", []), req_id=cmd.get("req_id", 1)
            )

        if opcode in (OP_ABORT_BATCH, OP_CANCEL_SPLITTER):
            self.is_cancelled = True
            if opcode == OP_ABORT_BATCH:
                self._batch_item_ids.clear()
                self._batch_upload_paths.clear()
            return []

        if opcode == OP_UI_NOTIFICATION:
            return []

        if opcode == OP_BATCH_CLEAR_READONLY:
            return [
                self._handle_batch_clear_readonly(
                    cmd.get("records", []), req_id=cmd.get("req_id", 1)
                )
            ]

        if opcode == OP_CREATE_BATCH_ZERO_FILES:
            return [
                self._handle_create_batch_zero_files(
                    cmd.get("records", []), req_id=cmd.get("req_id", 1)
                )
            ]

        if opcode in (OP_PRECHECK_BATCH, OP_PRECHECK_BATCH_EXT):
            return [
                self._handle_precheck_batch(
                    opcode=opcode,
                    records=cmd.get("records", []),
                    req_id=cmd.get("req_id", 1),
                    total_size=cmd.get("total_size", 0),
                )
            ]

        if opcode == OP_SET_FILE_ATTRS:
            self._handle_set_file_attrs(cmd)
            return []

        if opcode == OP_FORMAT_WIN32_ERRORS:
            return [self._handle_format_errors(cmd.get("error_codes", []))]

        if opcode == OP_XML_CONTROL:
            return [self._handle_xml_control(cmd["xml"])]

        if opcode in OP_EXTENDED_XML_OPCODES:
            reply = self._handle_extended_xml(opcode, cmd.get("xml", ""))
            return [reply] if reply is not None else []

        return [encode_error_code(1)]  # ERROR_INVALID_FUNCTION

    def _handle_query_root(self, flags: int = 0) -> list[bytes]:
        if flags & PEER_MODE_MAC:
            self.peer_mode = PEER_MODE_MAC
        elif flags & PEER_MODE_POSIX or sys.platform != "win32":
            self.peer_mode = PEER_MODE_POSIX
        else:
            self.peer_mode = PEER_MODE_WINDOWS

        if sys.platform == "win32":
            import string

            roots: list[tuple[str, int] | tuple[str, int, str, str]] = []
            for letter in string.ascii_uppercase:
                root = f"{letter}:\\"
                if os.path.exists(root):
                    roots.append((letter, DRIVE_FIXED))
            reply = encode_roots_reply(roots, peer_mode=PEER_MODE_WINDOWS)
        else:
            roots = [("/", DRIVE_FIXED, "/", "/")]
            reply = encode_roots_reply(roots, peer_mode=self.peer_mode)

        ack = encode_status_ack(req_opcode=OP_QUERY_ROOT, status=STATUS_OK)
        return [ack, reply]

    def _handle_refresh_dir(self) -> list[bytes]:
        if not self.peer_active_dir:
            return [encode_status_ack(status=STATUS_EXISTS, req_opcode=OP_QUERY_CURRENT_DIR)]
        replies = self._handle_list_dir(self.peer_active_dir)
        if len(replies) == 2:
            # Rewrite req_opcode to OP_QUERY_CURRENT_DIR
            replies[0] = encode_status_ack(req_opcode=OP_QUERY_CURRENT_DIR, status=STATUS_OK)
            return replies
        return [encode_status_ack(status=STATUS_EXISTS, req_opcode=OP_QUERY_CURRENT_DIR)]

    def _handle_list_dir(self, dir_path: str) -> list[bytes]:
        local_path = self.mapper.wire_to_local(dir_path)
        if not os.path.exists(local_path):
            return [encode_error_code(3, req_opcode=OP_LIST_DIR)]  # ERROR_PATH_NOT_FOUND
        if not os.path.isdir(local_path):
            return [encode_error_code(267, req_opcode=OP_LIST_DIR)]  # ERROR_DIRECTORY

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
            entries.sort(key=lambda x: (not x.is_dir, x.filename.lower()))
            ack = encode_status_ack(req_opcode=OP_LIST_DIR, status=STATUS_OK)
            rep = encode_listing_reply(entries)
            return [ack, rep]
        except PermissionError:
            return [encode_error_code(5, req_opcode=OP_LIST_DIR)]  # ERROR_ACCESS_DENIED
        except Exception:
            return [encode_error_code(3, req_opcode=OP_LIST_DIR)]

    def _handle_delete_file(self, file_path: str, req_id: int = 0) -> bytes:
        local_path = self.mapper.wire_to_local(file_path)
        try:
            if os.path.isfile(local_path) or os.path.islink(local_path):
                os.remove(local_path)
                return encode_status_ack(STATUS_OK, req_opcode=OP_DELETE_FILE, req_id=req_id)
            return encode_error_code(2, req_opcode=OP_DELETE_FILE, req_id=req_id)  # ERROR_FILE_NOT_FOUND
        except PermissionError:
            return encode_error_code(5, req_opcode=OP_DELETE_FILE, req_id=req_id)  # ERROR_ACCESS_DENIED
        except Exception:
            return encode_error_code(2, req_opcode=OP_DELETE_FILE, req_id=req_id)

    def _handle_delete_dir(self, dir_path: str, req_id: int = 0) -> bytes:
        local_path = self.mapper.wire_to_local(dir_path)
        try:
            if os.path.isdir(local_path):
                shutil.rmtree(local_path)
                return encode_status_ack(STATUS_OK, req_opcode=OP_DELETE_DIR, req_id=req_id)
            return encode_error_code(3, req_opcode=OP_DELETE_DIR, req_id=req_id)  # ERROR_PATH_NOT_FOUND
        except PermissionError:
            return encode_error_code(5, req_opcode=OP_DELETE_DIR, req_id=req_id)
        except Exception:
            return encode_error_code(3, req_opcode=OP_DELETE_DIR, req_id=req_id)

    def _handle_create_dir(self, dir_path: str, req_id: int = 0) -> bytes:
        local_path = self.mapper.wire_to_local(dir_path)
        try:
            os.makedirs(local_path, exist_ok=True)
            return encode_status_ack(STATUS_OK, req_opcode=OP_CREATE_DIR, req_id=req_id)
        except PermissionError:
            return encode_error_code(5, req_opcode=OP_CREATE_DIR, req_id=req_id)
        except Exception:
            return encode_error_code(3, req_opcode=OP_CREATE_DIR, req_id=req_id)

    def _handle_rename(self, old_path: str, new_path: str, req_id: int = 0) -> bytes:
        local_old = self.mapper.wire_to_local(old_path)
        local_new = self.mapper.wire_to_local(new_path)
        try:
            os.rename(local_old, local_new)
            return encode_status_ack(STATUS_OK, req_opcode=OP_RENAME_FILE, req_id=req_id)
        except PermissionError:
            return encode_error_code(5, req_opcode=OP_RENAME_FILE, req_id=req_id)
        except FileNotFoundError:
            return encode_error_code(2, req_opcode=OP_RENAME_FILE, req_id=req_id)
        except Exception:
            return encode_error_code(3, req_opcode=OP_RENAME_FILE, req_id=req_id)

    def _handle_upload_single(self, cmd: dict[str, Any]) -> list[bytes]:
        dest_path = cmd["dest_path"]
        file_size = cmd.get("file_size", 0)
        req_id = cmd.get("req_id", 1)
        overwrite = cmd.get("overwrite", False)

        # Legacy backward compatibility if full payload was bundled
        if "file_data" in cmd and cmd["file_data"]:
            file_data = cmd["file_data"]
            find_data = cmd.get("find_data")
            local_dest = self.mapper.wire_to_local(dest_path)
            try:
                parent_dir = os.path.dirname(local_dest)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                with open(local_dest, "wb") as f:
                    f.write(file_data)
                if find_data:
                    mtime = filetime_to_unix(find_data.last_write_time)
                    atime = filetime_to_unix(find_data.last_access_time)
                    if mtime > 0:
                        os.utime(local_dest, (atime if atime > 0 else mtime, mtime))
                return [
                    encode_status_ack(
                        STATUS_UPLOAD_SINGLE_DONE, req_opcode=OP_UPLOAD_SINGLE, req_id=req_id
                    )
                ]
            except PermissionError:
                return [
                    encode_status_ack(
                        status=1, req_opcode=OP_UPLOAD_SINGLE, req_id=req_id, win32_error=5
                    )
                ]
            except Exception:
                return [
                    encode_status_ack(
                        status=1, req_opcode=OP_UPLOAD_SINGLE, req_id=req_id, win32_error=112
                    )
                ]

        local_dest = self.mapper.wire_to_local(dest_path)

        # 1. Inspect destination file state (Section 4.2.4 item 3)
        if os.path.exists(local_dest) and not overwrite:
            return [
                encode_status_ack(
                    status=STATUS_CONFLICT, req_opcode=OP_UPLOAD_SINGLE, req_id=req_id
                )
            ]

        # 2. Check disk free space (Section 4.2.4 item 4)
        try:
            parent_dir = os.path.dirname(local_dest) or "."
            os.makedirs(parent_dir, exist_ok=True)
            free_bytes = shutil.disk_usage(parent_dir).free
            if free_bytes < file_size:
                return [
                    encode_status_ack(
                        status=STATUS_DISK_FULL, req_opcode=OP_UPLOAD_SINGLE, req_id=req_id
                    )
                ]
        except Exception:
            pass

        # 3. If file_size == 0 (zero-length file) (Section 4.2.4 item 5)
        if file_size == 0:
            try:
                with open(local_dest, "wb"):
                    pass
                return [
                    encode_status_ack(
                        status=STATUS_ZERO_LENGTH, req_opcode=OP_UPLOAD_SINGLE, req_id=req_id
                    )
                ]
            except PermissionError:
                return [
                    encode_status_ack(
                        status=1, req_opcode=OP_UPLOAD_SINGLE, req_id=req_id, win32_error=5
                    )
                ]
            except Exception:
                return [
                    encode_status_ack(
                        status=1, req_opcode=OP_UPLOAD_SINGLE, req_id=req_id, win32_error=3
                    )
                ]

        # 4. If file_size > 0 (Section 4.2.4 item 6)
        flags = cmd.get("flags", 0)
        is_batch = (
            bool(flags & 0x02)
            or (req_id in self._batch_item_ids)
            or (local_dest in self._batch_upload_paths)
        )
        self._batch_item_ids.discard(req_id)
        self._batch_upload_paths.discard(local_dest)
        try:
            parent_dir = os.path.dirname(local_dest)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            f = open(local_dest, "wb")
            item_id = req_id
            self._active_uploads[item_id] = {
                "file": f,
                "path": local_dest,
                "total_bytes": file_size,
                "written_bytes": 0,
                "is_batch": is_batch,
            }
            return [encode_status_ack(status=STATUS_OK, req_opcode=OP_UPLOAD_SINGLE, req_id=req_id)]
        except PermissionError:
            return [
                encode_status_ack(
                    status=1, req_opcode=OP_UPLOAD_SINGLE, req_id=req_id, win32_error=5
                )
            ]
        except Exception:
            return [
                encode_status_ack(
                    status=1, req_opcode=OP_UPLOAD_SINGLE, req_id=req_id, win32_error=3
                )
            ]

    def _encode_chunk_write_error(self, item_id: int, win32_error: int, is_batch: bool) -> bytes:
        if is_batch:
            return encode_upload_batch_error(item_id=item_id, win32_error=win32_error)
        return encode_upload_error(item_id=item_id, win32_error=win32_error)

    def _handle_file_chunk(self, cmd: dict[str, Any]) -> list[bytes]:
        item_id = cmd["item_id"]
        chunk_index = cmd["chunk_index"]
        total_chunks = cmd.get("total_chunks", 0)
        chunk_data = cmd["chunk_data"]
        upload_ctx = self._active_uploads.get(item_id)
        if not upload_ctx:
            is_batch = item_id in self._batch_item_ids
            self._batch_item_ids.discard(item_id)
            return [
                self._encode_chunk_write_error(item_id=item_id, win32_error=6, is_batch=is_batch)
            ]  # ERROR_INVALID_HANDLE
        try:
            upload_ctx["file"].write(chunk_data)
            upload_ctx["file"].flush()
            upload_ctx["written_bytes"] += len(chunk_data)
            if total_chunks > 0 and chunk_index >= total_chunks:
                upload_ctx["file"].close()
                self._active_uploads.pop(item_id, None)
            return []
        except OSError as exc:
            is_batch = bool(upload_ctx.get("is_batch"))
            win32_err = 112 if exc.errno == errno.ENOSPC else 5
            try:
                upload_ctx["file"].close()
            except Exception:
                pass
            self._active_uploads.pop(item_id, None)
            return [
                self._encode_chunk_write_error(
                    item_id=item_id, win32_error=win32_err, is_batch=is_batch
                )
            ]
        except Exception:
            is_batch = bool(upload_ctx.get("is_batch"))
            try:
                upload_ctx["file"].close()
            except Exception:
                pass
            self._active_uploads.pop(item_id, None)
            return [
                self._encode_chunk_write_error(item_id=item_id, win32_error=112, is_batch=is_batch)
            ]

    def _handle_transfer_complete(self, cmd: dict[str, Any]) -> list[bytes]:
        item_id = cmd.get("item_id", 0)
        upload_ctx = self._active_uploads.pop(item_id, None)
        if upload_ctx:
            try:
                upload_ctx["file"].flush()
                upload_ctx["file"].close()
            except Exception:
                pass
        return []

    def _handle_abort_upload(self, cmd: dict[str, Any]) -> list[bytes]:
        item_id = cmd.get("item_id", 0)
        upload_ctx = self._active_uploads.pop(item_id, None)
        if upload_ctx:
            try:
                upload_ctx["file"].close()
                if os.path.exists(upload_ctx["path"]):
                    os.remove(upload_ctx["path"])
            except Exception:
                pass
        return []

    def _handle_cancel_keep_partial(self, cmd: dict[str, Any]) -> list[bytes]:
        item_id = cmd.get("item_id", 0)
        upload_ctx = self._active_uploads.pop(item_id, None)
        if upload_ctx:
            try:
                upload_ctx["file"].close()
            except Exception:
                pass
        return []

    def _handle_download_single(self, cmd: dict[str, Any] | str) -> list[bytes]:
        req_id = 1
        if isinstance(cmd, dict):
            src_path = cmd.get("path") or cmd.get("src_path", "")
            req_id = cmd.get("req_id", 1)
        else:
            src_path = str(cmd)

        local_src = self.mapper.wire_to_local(src_path)
        try:
            if not os.path.isfile(local_src):
                return [
                    encode_status_ack(
                        status=STATUS_ERROR,
                        req_opcode=OP_DOWNLOAD_SINGLE,
                        req_id=req_id,
                        win32_error=2,  # ERROR_FILE_NOT_FOUND
                    )
                ]

            stat = os.stat(local_src)
            file_size = stat.st_size

            # Mode 1: Zero-Length File
            if file_size == 0:
                return [
                    encode_status_ack(
                        status=STATUS_ZERO_LENGTH,
                        req_opcode=OP_DOWNLOAD_SINGLE,
                        req_id=req_id,
                        file_size=0,
                    )
                ]

            # Mode 2: Small File (<= 64,512 bytes) inline delivery
            if file_size <= 64512:
                with open(local_src, "rb") as f:
                    file_data = f.read()
                return [
                    encode_status_ack(
                        status=STATUS_INLINE_SMALL_FILE,
                        req_opcode=OP_DOWNLOAD_SINGLE,
                        req_id=req_id,
                        file_size=file_size,
                        flags=2,
                        file_data=file_data,
                    )
                ]

            # Mode 3: Large File (> 64,512 bytes) streaming chunks
            initial_ack = encode_status_ack(
                status=STATUS_OK,
                req_opcode=OP_DOWNLOAD_SINGLE,
                req_id=req_id,
                file_size=file_size,
            )
            replies = [initial_ack]

            chunk_size = 64000
            total_chunks = (file_size + chunk_size - 1) // chunk_size
            with open(local_src, "rb") as f:
                for chunk_idx in range(1, total_chunks + 1):
                    chunk_data = f.read(chunk_size)
                    replies.append(
                        encode_download_chunk(
                            item_id=req_id,
                            chunk_index=chunk_idx,
                            total_chunks=total_chunks,
                            chunk_data=chunk_data,
                        )
                    )
            return replies

        except PermissionError:
            return [
                encode_status_ack(
                    status=STATUS_ERROR,
                    req_opcode=OP_DOWNLOAD_SINGLE,
                    req_id=req_id,
                    win32_error=5,  # ERROR_ACCESS_DENIED
                )
            ]
        except Exception:
            return [
                encode_status_ack(
                    status=STATUS_ERROR,
                    req_opcode=OP_DOWNLOAD_SINGLE,
                    req_id=req_id,
                    win32_error=2,
                )
            ]

    def _handle_download_batch(
        self, records: list[tuple[int, str]], req_id: int = 1
    ) -> list[bytes]:
        replies: list[bytes] = []
        empty_ids: list[int] = []
        unreadable_ids: list[int] = []

        for item_id, remote_path in records:
            local_p = self.mapper.wire_to_local(remote_path)
            if not os.path.exists(local_p):
                unreadable_ids.append(item_id)
                continue

            if os.path.isdir(local_p):
                empty_ids.append(item_id)
                continue

            try:
                file_size = os.path.getsize(local_p)
                if file_size == 0:
                    empty_ids.append(item_id)
                    continue

                chunk_size = 64000
                total_chunks = (file_size + chunk_size - 1) // chunk_size
                with open(local_p, "rb") as f:
                    for chunk_idx in range(1, total_chunks + 1):
                        chunk_data = f.read(chunk_size)
                        replies.append(
                            encode_download_chunk(
                                item_id=item_id,
                                chunk_index=chunk_idx,
                                total_chunks=total_chunks,
                                chunk_data=chunk_data,
                            )
                        )
            except OSError:
                unreadable_ids.append(item_id)

        if empty_ids:
            replies.append(encode_download_batch_empty(empty_ids, req_id=req_id))
        if unreadable_ids:
            replies.append(encode_download_unreadable(unreadable_ids, req_id=req_id))
        replies.append(encode_download_batch_done(req_id=req_id, status=0))
        return replies

    def _handle_batch_clear_readonly(
        self, records: list[tuple[int, str]], req_id: int = 1
    ) -> bytes:
        for _item_id, remote_path in records:
            local_p = self.mapper.wire_to_local(remote_path)
            if os.path.exists(local_p):
                try:
                    os.chmod(local_p, os.stat(local_p).st_mode | 0o200)
                except OSError:
                    pass
        return encode_status_ack(STATUS_OK, req_opcode=OP_BATCH_CLEAR_READONLY, req_id=req_id)

    def _handle_create_batch_zero_files(
        self, records: list[tuple[int, str]], req_id: int = 1
    ) -> bytes:
        try:
            for _item_id, remote_path in records:
                local_p = self.mapper.wire_to_local(remote_path)
                parent = os.path.dirname(local_p)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(local_p, "wb"):
                    pass
            return encode_status_ack(STATUS_OK, req_opcode=OP_CREATE_BATCH_ZERO_FILES, req_id=req_id)
        except PermissionError:
            return encode_error_code(5, req_opcode=OP_CREATE_BATCH_ZERO_FILES, req_id=req_id)
        except OSError:
            return encode_error_code(3, req_opcode=OP_CREATE_BATCH_ZERO_FILES, req_id=req_id)

    def _handle_precheck_batch(
        self,
        opcode: int,
        records: list[tuple[int, str]],
        req_id: int = 1,
        total_size: int = 0,
    ) -> bytes:
        insufficient_space = False
        try:
            check_dir = self.mapper.virtual_root or "/"
            if records:
                first_local = self.mapper.wire_to_local(records[0][1])
                parent = os.path.dirname(first_local)
                if parent and os.path.exists(parent):
                    check_dir = parent
            insufficient_space = shutil.disk_usage(check_dir).free < total_size
        except OSError:
            pass

        for item_id, dest_path in records:
            self._batch_item_ids.add(item_id)
            self._batch_upload_paths.add(self.mapper.wire_to_local(dest_path))

        conflict_ids = [
            item_id
            for item_id, dest_path in records
            if os.path.exists(self.mapper.wire_to_local(dest_path))
        ]

        if opcode == OP_PRECHECK_BATCH_EXT:
            return encode_precheck_batch_ext_reply(
                conflict_ids,
                req_id=req_id,
                flag1=1 if insufficient_space else 0,
            )
        return encode_precheck_batch_reply(
            conflict_ids,
            req_id=req_id,
            insufficient_space=insufficient_space,
        )

    def _handle_set_file_attrs(self, cmd: dict[str, Any]) -> None:
        local_p = self.mapper.wire_to_local(cmd["path"])
        if not os.path.exists(local_p):
            return
        mtime = filetime_to_unix(cmd.get("last_write_time", 0))
        atime = filetime_to_unix(cmd.get("last_access_time", 0))
        if mtime > 0:
            try:
                os.utime(local_p, (atime if atime > 0 else mtime, mtime))
            except OSError:
                pass
        attrs = cmd.get("attributes", 0)
        try:
            mode = os.stat(local_p).st_mode
            if attrs & FILE_ATTRIBUTE_READONLY:
                os.chmod(local_p, mode & ~0o222)
            else:
                os.chmod(local_p, mode | 0o200)
        except OSError:
            pass

    def _handle_format_errors(self, error_codes: list[int]) -> bytes:
        formatted: list[tuple[int, str]] = []
        for code in error_codes:
            if self.peer_mode in (PEER_MODE_MAC, PEER_MODE_POSIX):
                try:
                    msg = os.strerror(code)
                except ValueError:
                    msg = format_win32_error(code)
            else:
                msg = format_win32_error(code)
            formatted.append((code, msg))
        return encode_formatted_errors(formatted)

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

    def _handle_extended_xml(self, opcode: int, xml_content: str) -> bytes | None:
        """Handles incoming 0x32..0x39 Extended XML commands from peer."""
        if opcode in (0x32, 0x33) and not xml_content:
            return encode_status_ack(STATUS_OK)

        extra_cmd = parse_extra_xml_command(xml_content) or ""

        if opcode in (OP_VNIC_SWITCH, 0x32) and (
            extra_cmd == VNIC_CMD_PLEASE_SWITCH or VNIC_CMD_PLEASE_SWITCH in xml_content
        ):
            accepted = (
                self.on_vnic_switch_request(xml_content)
                if self.on_vnic_switch_request is not None
                else self.allow_vnic_switch
            )
            self.vnic_switch_accepted = bool(accepted)
            reply_cmd = VNIC_CMD_ACCEPT_SWITCH if accepted else VNIC_CMD_REJECT_SWITCH
            return encode_extended_xml(opcode, f"<ExtraXmlCommand>{reply_cmd}</ExtraXmlCommand>")

        if extra_cmd in (VNIC_CMD_ME_SWITCH_ROUTER, VNIC_CMD_ME_SWITCH_NONE_ROUTER):
            is_router = extra_cmd == VNIC_CMD_ME_SWITCH_ROUTER
            self.vnic_peer_is_router = is_router
            if self.on_vnic_mode_entered is not None:
                self.on_vnic_mode_entered(is_router)
            return encode_extended_xml(opcode, "<ExtraXmlCommand>OK</ExtraXmlCommand>")

        if extra_cmd in (VNIC_CMD_ACCEPT_SWITCH, VNIC_CMD_REJECT_SWITCH, "OK"):
            return None

        return encode_extended_xml(opcode, "<ExtraXmlCommand>OK</ExtraXmlCommand>")
