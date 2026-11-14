# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interactive Command-Line Interface and headless daemon for OpenEasySync."""

import argparse
import logging
import os
import sys
import time

from openeasysync.client.session import SessionManager
from openeasysync.client.transfer import TransferManager
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

    try:
        local_p = args.local_path
        remote_p = args.remote_path
        if os.path.isdir(local_p):
            print(f"Batch uploading directory '{local_p}' -> '{remote_p}'...")
            success = transfer.upload_batch([local_p], remote_p)
        else:
            print(f"Uploading file '{local_p}' -> '{remote_p}'...")
            success = transfer.upload_file(local_p, remote_p)

        if success:
            print("Transfer completed successfully.")
            return 0
        else:
            print("Transfer failed or was cancelled.", file=sys.stderr)
            return 1
    except Exception as exc:
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

    try:
        remote_p = args.remote_path
        local_p = args.local_path
        print(f"Downloading '{remote_p}' -> '{local_p}'...")
        success = transfer.download_file(remote_p, local_p)
        if success:
            print("Download completed successfully.")
            return 0
        else:
            print("Download failed.", file=sys.stderr)
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
    p_put.add_argument("local_path", help="Local source path")
    p_put.add_argument("remote_path", help="Remote destination path")
    p_put.set_defaults(func=cmd_put)

    # get
    p_get = subparsers.add_parser("get", help="Download a file from remote peer")
    p_get.add_argument("remote_path", help="Remote source path")
    p_get.add_argument("local_path", help="Local destination path")
    p_get.set_defaults(func=cmd_get)

    parsed_args = parser.parse_args(argv)
    if parsed_args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    return parsed_args.func(parsed_args)


if __name__ == "__main__":
    sys.exit(main())
