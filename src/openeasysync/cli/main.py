# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interactive Command-Line Interface and headless daemon for OpenEasySync."""

import argparse
import logging
import os
import sys
import time

from openeasysync.client.session import SessionManager
from openeasysync.client.transfer import SpeedTracker, TransferManager, format_rate
from openeasysync.constants import SPEED_USB20
from openeasysync.transport.detector import detect_device, list_connected_devices


def cmd_devices(args) -> int:
    """Lists all connected OTi-2108 devices and their availability status."""
    devs = list_connected_devices()
    if not devs:
        print("No OTi-2108 devices found on USB or SCSI bus.")
        return 0

    print(f"Found {len(devs)} OTi-2108 device endpoint(s):")
    print(f"{'#':<3} {'Backend':<8} {'Spec':<16} {'Status':<35} {'Description'}")
    print("-" * 80)
    for d in devs:
        avail_marker = "[AVAILABLE]" if d["is_available"] else "[BUSY/IN-USE]"
        status_disp = f"{avail_marker} {d['status']}"
        print(
            f"[{d['index']}] {d['backend']:<8} {d['spec']:<16} {status_disp:<35} {d['description']}"
        )
    return 0


def cmd_info(args) -> int:
    """Displays hardware identification and link speed information."""
    device_spec = getattr(args, "device", None)
    transport = detect_device(device_spec)
    if not transport:
        print("Error: No available OTi-2108 device detected on USB or SCSI bus.", file=sys.stderr)
        return 1

    with transport:
        try:
            info = transport.identify()
            print("--- OTi-2108 Hardware Information ---")
            print(f"Chip ID:            {info['chip_id']}")
            print(f"Firmware Revision:  {info['fw_rev']}")
            print(f"Firmware Build Date:{info['fw_date']}")
            print(f"Extended Buffer Cap:{info['extended_cap']}")

            buf_size = transport.get_buffer_size()
            print(f"Buffer Size:        {buf_size // 1024} KB")

            local_spd, peer_spd = transport.get_usb_speed()
            l_str = "USB 2.0 (480 Mbps)" if local_spd == SPEED_USB20 else "USB 1.1 (12 Mbps)"
            p_str = (
                "USB 2.0 (480 Mbps)"
                if peer_spd == SPEED_USB20
                else ("USB 1.1 (12 Mbps)" if peer_spd != 0 else "Disconnected / Idle")
            )
            print(f"Local Port Speed:   {l_str}")
            print(f"Remote Port Speed:  {p_str}")

            peer_mode = transport.get_peer_mode()
            print(f"Remote Peer Mode:   {peer_mode:#06x}")
            return 0
        except Exception as exc:
            print(f"Error communicating with device: {exc}", file=sys.stderr)
            return 1


def cmd_daemon(args) -> int:
    """Runs OpenEasySync in headless background daemon mode to service transfers."""
    device_spec = getattr(args, "device", None)
    transport = detect_device(device_spec)
    if not transport:
        print("Error: No available OTi-2108 device detected.", file=sys.stderr)
        return 1

    session = SessionManager(transport)
    print("Starting OpenEasySync peer daemon...")
    session.connect()
    print("Peer daemon running. Ready to service file transfers. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping peer daemon...")
    finally:
        session.disconnect()
    return 0


def cmd_ls(args) -> int:
    """Lists files and folders on the remote peer."""
    device_spec = getattr(args, "device", None)
    transport = detect_device(device_spec)
    if not transport:
        print("Error: No available OTi-2108 device detected.", file=sys.stderr)
        return 1

    session = SessionManager(transport)
    session.connect()
    transfer = TransferManager(session)

    try:
        remote_path = args.path
        if not remote_path:
            roots = transfer.query_roots()
            print(f"{'Drive / Root':<20} {'Type':<12} {'Total Size':<15} {'Free Space':<15}")
            print("-" * 65)
            drive_types = {
                0: "Unknown",
                1: "NoRoot",
                2: "Removable",
                3: "Fixed",
                4: "Remote",
                5: "CD-ROM",
                6: "RAMDisk",
            }
            for r in roots:
                tot_str = f"{r.total_bytes / (1024**3):>10.2f} GB" if r.total_bytes > 0 else "-"
                free_str = (
                    f"{r.total_free_bytes / (1024**3):>10.2f} GB" if r.total_free_bytes > 0 else "-"
                )
                dtype_name = drive_types.get(r.drive_type, "Drive")
                print(f"{r.find_data.filename:<20} {dtype_name:<12} {tot_str:>15} {free_str:>15}")
        else:
            entries = transfer.list_dir(remote_path)
            print(f"{'Name':<35} {'Size (Bytes)':<15} {'Type':<10}")
            print("-" * 65)
            for e in entries:
                t_str = "DIR" if e.is_dir else "FILE"
                sz_str = "-" if e.is_dir else str(e.find_data.file_size)
                print(f"{e.find_data.filename:<35} {sz_str:<15} {t_str:<10}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        session.disconnect()


class _CliProgressReporter:
    """Renders upload/download percentage and speed updates to stdout."""

    def __init__(self, is_tty: bool | None = None, min_interval_s: float = 0.1) -> None:
        self._tracker = SpeedTracker()
        self._is_tty = sys.stdout.isatty() if is_tty is None else is_tty
        self._min_interval_s = min_interval_s
        self._last_print = 0.0
        self._printed_line = False

    def __call__(self, current: int, total: int, filename: str = "") -> None:
        now = time.monotonic()
        done = total == 0 or current >= total
        if not done and (now - self._last_print) < self._min_interval_s:
            return
        self._last_print = now

        pct = 100.0 if total == 0 else min(100.0, (current / total) * 100.0)
        rate_str = format_rate(self._tracker.rate_kbs(current))
        suffix = f" {filename}" if filename else ""
        msg = f"Progress: {pct:5.1f}% ({current}/{total} B) {rate_str}{suffix}"
        if self._is_tty:
            print(f"\r{msg:<72}", end="", flush=True)
            self._printed_line = True
        elif done or not self._printed_line:
            print(msg, flush=True)
            self._printed_line = True

    def finish(self) -> None:
        if self._is_tty and self._printed_line:
            print(flush=True)
            self._printed_line = False


def cmd_put(args) -> int:
    """Uploads a local file or directory to remote peer."""
    device_spec = getattr(args, "device", None)
    transport = detect_device(device_spec)
    if not transport:
        print("Error: No available OTi-2108 device detected.", file=sys.stderr)
        return 1

    session = SessionManager(transport)
    session.connect()
    transfer = TransferManager(session)
    reporter = _CliProgressReporter()

    try:
        local_p = args.local_path
        remote_p = args.remote_path
        force = bool(getattr(args, "force", False))
        if os.path.isdir(local_p):
            print(f"Batch uploading directory '{local_p}' -> '{remote_p}'...")
            success = transfer.upload_batch([local_p], remote_p, progress_callback=reporter)
        else:
            print(f"Uploading file '{local_p}' -> '{remote_p}'...")
            success = transfer.upload_file(
                local_p, remote_p, overwrite=force, progress_callback=reporter
            )
        reporter.finish()

        if success:
            print("Transfer completed successfully.")
            return 0
        else:
            print("Transfer failed or was cancelled.", file=sys.stderr)
            return 1
    except Exception as exc:
        reporter.finish()
        print(f"Error during transfer: {exc}", file=sys.stderr)
        return 1
    finally:
        session.disconnect()


def cmd_get(args) -> int:
    """Downloads a remote file or directory to local destination."""
    device_spec = getattr(args, "device", None)
    transport = detect_device(device_spec)
    if not transport:
        print("Error: No available OTi-2108 device detected.", file=sys.stderr)
        return 1

    session = SessionManager(transport)
    session.connect()
    transfer = TransferManager(session)
    reporter = _CliProgressReporter()

    try:
        remote_p = args.remote_path
        local_p = args.local_path
        print(f"Downloading '{remote_p}' -> '{local_p}'...")
        success = transfer.download_file(remote_p, local_p, progress_callback=reporter)
        reporter.finish()
        if success:
            print("Download completed successfully.")
            return 0
        else:
            print("Download failed.", file=sys.stderr)
            return 1
    except Exception as exc:
        reporter.finish()
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        session.disconnect()


def cmd_rm(args) -> int:
    """Deletes a file on the remote peer."""
    device_spec = getattr(args, "device", None)
    transport = detect_device(device_spec)
    if not transport:
        print("Error: No available OTi-2108 device detected.", file=sys.stderr)
        return 1

    session = SessionManager(transport)
    session.connect()
    transfer = TransferManager(session)

    try:
        remote_p = args.remote_path
        print(f"Deleting '{remote_p}'...")
        success = transfer.delete_file(remote_p)
        if success:
            print("File deleted successfully.")
            return 0
        else:
            print(f"Failed to delete '{remote_p}'.", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        session.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openeasysync",
        description="OpenEasySync: OTi-2108 Cross-Platform File Transfer Companion Application",
    )
    parser.add_argument(
        "-d",
        "--device",
        default=None,
        help="Specify device identifier (e.g. '1:4', '0', or '/dev/sg1'). Default: auto-select first available",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        "--debug",
        action="store_true",
        dest="debug",
        help="Enable verbose debug logging",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # devices
    p_devs = subparsers.add_parser(
        "devices", help="List all connected OTi-2108 devices and availability"
    )
    p_devs.set_defaults(func=cmd_devices)

    # info
    p_info = subparsers.add_parser("info", help="Display hardware identification and status")
    p_info.set_defaults(func=cmd_info)

    # daemon
    p_daemon = subparsers.add_parser("daemon", help="Run background daemon to service transfers")
    p_daemon.set_defaults(func=cmd_daemon)

    # ls
    p_ls = subparsers.add_parser("ls", help="List remote directory contents")
    p_ls.add_argument(
        "path", nargs="?", default="", help="Remote directory path (or empty for roots)"
    )
    p_ls.set_defaults(func=cmd_ls)

    # put
    p_put = subparsers.add_parser("put", help="Upload a file or directory to remote peer")
    p_put.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite existing file on the remote destination path",
    )
    p_put.add_argument("local_path", help="Local source path")
    p_put.add_argument("remote_path", help="Remote destination path")
    p_put.set_defaults(func=cmd_put)

    # get
    p_get = subparsers.add_parser("get", help="Download a file from remote peer")
    p_get.add_argument("remote_path", help="Remote source path")
    p_get.add_argument("local_path", help="Local destination path")
    p_get.set_defaults(func=cmd_get)

    # rm
    p_rm = subparsers.add_parser(
        "rm", aliases=["delete"], help="Delete a file on remote peer"
    )
    p_rm.add_argument("remote_path", help="Remote file path to delete")
    p_rm.set_defaults(func=cmd_rm)

    parsed_args = parser.parse_args(argv)
    if parsed_args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    return parsed_args.func(parsed_args)


if __name__ == "__main__":
    sys.exit(main())
