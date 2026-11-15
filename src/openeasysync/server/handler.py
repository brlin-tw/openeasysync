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
    OP_ABORT_UPLOAD,
    OP_CANCEL_KEEP_PARTIAL,
    OP_CANCEL_TRANSFER,
    OP_CREATE_DIR,
    OP_DELETE_DIR,
    OP_DELETE_FILE,
    OP_DOWNLOAD_BATCH,
    OP_DOWNLOAD_SINGLE,
    OP_EXTENDED_XML_MAX,
    OP_EXTENDED_XML_MIN,
    OP_FILE_CHUNK,
    OP_LIST_DIR,
    OP_NOTIFY_ACTIVE_DIR,
    OP_PRECHECK_BATCH,
    OP_PRECHECK_BATCH_EXT,
    OP_PRECHECK_SINGLE,
    OP_QUERY_ROOT,
    OP_QUERY_SPACE,
    OP_RENAME_DIR,
    OP_RENAME_FILE,
    OP_SET_MAC_MODE,
    OP_TRANSFER_COMPLETE,
    OP_UPLOAD_SINGLE,
    OP_VERIFY_PEER_NAME,
    OP_VNIC_SWITCH,
    OP_XML_CONTROL,
    STATUS_ACTIVE_DIR_ACK,
    STATUS_CONFLICT,
    STATUS_DISK_FULL,
    STATUS_EXISTS,
    STATUS_MAC_MODE_ACK,
    STATUS_OK,
    STATUS_UPLOAD_BATCH_DONE,
    STATUS_UPLOAD_SINGLE_DONE,
    STATUS_ZERO_LENGTH,
)
from openeasysync.protocol.commands import decode_command, encode_extended_xml
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
    encode_chunk_ack,
    encode_download_batch_done,
    encode_download_batch_empty,
    encode_download_batch_stream,
    encode_download_single_reply,
    encode_error_code,
    encode_listing_reply,
    encode_precheck_batch_ext_reply,
    encode_precheck_batch_reply,
    encode_roots_reply,
    encode_status_ack,
    encode_upload_batch_error,
)
from openeasysync.protocol.xml_msg import build_oti_msg, parse_oti_msg


class PeerServerHandler:
    """Handles incoming Client Commands from a remote peer and returns reply payloads."""

    def __init__(self, path_mapper: PathMapper | None = None):
        self.mapper = path_mapper or PathMapper()
        self.is_cancelled = False
        self.peer_active_dir = ""
        self._active_uploads: dict[int, dict[str, Any]] = {}

    def handle_command(self, payload: bytes) -> list[bytes]:
        """Dispatches a command payload and returns a list of reply payload(s)."""
        if not payload:
            return [encode_error_code(1)]  # ERROR_INVALID_FUNCTION

        cmd = decode_command(payload)
        opcode = cmd["opcode"]

        if opcode == OP_QUERY_ROOT:
            return self._handle_query_root()

        if opcode in (OP_NOTIFY_ACTIVE_DIR, OP_VERIFY_PEER_NAME):
            self.peer_active_dir = cmd.get("path", "")
            return [
                encode_status_ack(req_opcode=OP_NOTIFY_ACTIVE_DIR, status=STATUS_ACTIVE_DIR_ACK)
            ]

        if opcode == OP_LIST_DIR:
            return self._handle_list_dir(cmd["path"])

        if opcode == OP_DELETE_FILE:
            return [self._handle_delete_file(cmd["path"])]

        if opcode == OP_DELETE_DIR:
            return [self._handle_delete_dir(cmd["path"])]

        if opcode == OP_CREATE_DIR:
            return [self._handle_create_dir(cmd["path"])]

        if opcode in (OP_RENAME_FILE, OP_RENAME_DIR):
            return [self._handle_rename(cmd["old_path"], cmd["new_path"])]

        if opcode == OP_UPLOAD_SINGLE:
            return self._handle_upload_single(cmd)

        if opcode == OP_FILE_CHUNK:
            return self._handle_file_chunk(cmd)

        if opcode == OP_TRANSFER_COMPLETE:
            return self._handle_transfer_complete(cmd)

        if opcode in (OP_ABORT_UPLOAD, OP_CANCEL_TRANSFER):
            if "item_id" in cmd and len(payload) == 9:
                return self._handle_abort_upload(cmd)
            self.is_cancelled = True
            return [encode_status_ack(STATUS_OK)]

        if opcode in (OP_CANCEL_KEEP_PARTIAL, OP_PRECHECK_SINGLE):
            if "item_id" in cmd and len(payload) == 9:
                return self._handle_cancel_keep_partial(cmd)
            return [self._handle_precheck_single(cmd.get("path", ""))]

        if opcode == OP_DOWNLOAD_SINGLE:
            return [self._handle_download_single(cmd["path"])]

        if opcode == OP_DOWNLOAD_BATCH:
            return self._handle_download_batch(cmd["paths"])

        if opcode == OP_QUERY_SPACE:
            return [self._handle_query_space(cmd["path"])]

        if opcode == OP_SET_MAC_MODE:
            return [encode_status_ack(STATUS_MAC_MODE_ACK)]

        if opcode == OP_PRECHECK_BATCH:
            return [self._handle_precheck_batch(cmd["dest_dir"], cmd["rel_paths"])]

        if opcode == OP_PRECHECK_BATCH_EXT:
            return [self._handle_precheck_batch_ext(cmd["dest_dir"], cmd["records"])]

        if opcode == OP_XML_CONTROL:
            return [self._handle_xml_control(cmd["xml"])]

        if OP_EXTENDED_XML_MIN <= opcode <= OP_EXTENDED_XML_MAX:
            return [self._handle_extended_xml(opcode, cmd.get("xml", ""))]

        return [encode_error_code(1)]  # ERROR_INVALID_FUNCTION

    def _handle_query_root(self) -> list[bytes]:
        roots: list[tuple[str, int]] = []
        if sys.platform == "win32":
            import string

            for letter in string.ascii_uppercase:
                root = f"{letter}:\\"
                if os.path.exists(root):
                    roots.append((letter, DRIVE_FIXED))
        else:
            roots.append(("C", DRIVE_FIXED))
            # Check /media or /mnt for mounted removable drives
            for mnt_dir in ("/media", "/mnt"):
                if os.path.isdir(mnt_dir):
                    try:
                        for entry in os.listdir(mnt_dir):
                            full_mnt = os.path.join(mnt_dir, entry)
                            if os.path.ismount(full_mnt):
                                roots.append(("D", DRIVE_REMOVABLE))
                                break
                    except Exception:
                        pass

        ack = encode_status_ack(req_opcode=OP_QUERY_ROOT, status=STATUS_OK)
        reply = encode_roots_reply(roots)
        return [ack, reply]

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

    def _handle_file_chunk(self, cmd: dict[str, Any]) -> list[bytes]:
        item_id = cmd["item_id"]
        chunk_index = cmd["chunk_index"]
        chunk_data = cmd["chunk_data"]
        upload_ctx = self._active_uploads.get(item_id)
        if not upload_ctx:
            return [
                encode_upload_batch_error(win32_error=6, failed_path="")
            ]  # ERROR_INVALID_HANDLE
        try:
            upload_ctx["file"].write(chunk_data)
            upload_ctx["file"].flush()
            upload_ctx["written_bytes"] += len(chunk_data)
            return [encode_chunk_ack(item_id=item_id, chunk_index=chunk_index)]
        except Exception:
            return [
                encode_upload_batch_error(win32_error=112, failed_path=upload_ctx.get("path", ""))
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

    def _handle_extended_xml(self, opcode: int, xml_content: str) -> bytes:
        """Handles incoming 0x32..0x39 Extended XML commands from peer."""
        if opcode in (0x32, 0x33) and not xml_content:
            return encode_status_ack(STATUS_OK)

        if opcode in (OP_VNIC_SWITCH, 0x32) and "PleaseSwitchAsVirtualNIC" in xml_content:
            return encode_extended_xml(
                opcode, "<ExtraXmlCommand>RejectSwitchAsVirtualNIC</ExtraXmlCommand>"
            )
        return encode_extended_xml(opcode, "<ExtraXmlCommand>OK</ExtraXmlCommand>")
