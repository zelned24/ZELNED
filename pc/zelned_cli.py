#!/usr/bin/env python3
"""
Zel.NeD CLI — Command-line file transfer to Nintendo 3DS
=========================================================
Usage:
    python zelned_cli.py --ip 192.168.1.47 file.cia
    python zelned_cli.py --ip 192.168.1.47 file1.cia file2.3dsx folder/
    python zelned_cli.py --ip 192.168.1.47 --throttle 400 bigfile.cia
    python zelned_cli.py --discover            # scan LAN for 3DS consoles
    python zelned_cli.py --resume-list         # list resumable transfers
    python zelned_cli.py --resume-clear        # clear all resume states
"""

import argparse
import sys
import time
import threading
from pathlib import Path

from protocol import (
    connect_to_3ds, send_session_header, send_file,
    send_directory_entry, ThrottleController, TCP_PORT, ProtocolError
)
from discovery import DiscoveryListener
from queue_manager import QueueManager, ItemStatus
from resume_state import ResumeState

# ── ANSI helpers ───────────────────────────────────────────────────────────────
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_DIM    = "\033[2m"


def _c(text, *codes):
    return "".join(codes) + str(text) + _RESET


def _bar(progress: float, width: int = 30) -> str:
    filled = int(progress * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


# ── Discovery command ─────────────────────────────────────────────────────────

def cmd_discover(timeout: int = 6):
    print(_c("⬡ Zel.NeD — Scanning LAN for 3DS consoles...", _BOLD, _CYAN))
    print(f"  Listening on UDP port 9504 for {timeout} seconds.\n")
    found = []

    def on_change():
        pass

    listener = DiscoveryListener(on_change=on_change)
    listener.start()
    for i in range(timeout):
        time.sleep(1)
        devices = listener.devices()
        sys.stdout.write(f"\r  Found: {len(devices)} device(s)  ")
        sys.stdout.flush()
    listener.stop()

    devices = listener.devices()
    print()
    if not devices:
        print(_c("  No 3DS consoles found. Make sure Zel.NeD is running on your console.", _YELLOW))
    else:
        print(_c(f"\n  ✓ {len(devices)} console(s) detected:", _GREEN, _BOLD))
        for d in devices:
            print(f"    • {_c(d.name, _BOLD)}  →  {d.ip}:{d.tcp_port}")
    print()


# ── Main transfer command ─────────────────────────────────────────────────────

def cmd_send(ip: str, paths: list, throttle_kbps: int, port: int, no_resume: bool):
    # Build queue
    queue = QueueManager()
    for p in paths:
        added = queue.add_path(Path(p))
        print(f"  Added {added} item(s) from '{p}'")

    items = queue.snapshot()
    if not items:
        print(_c("No valid files or folders to send.", _RED))
        sys.exit(1)

    total_files, _, total_bytes = queue.total_stats()
    print(_c(f"\n⬡ Zel.NeD — Sending {total_files} file(s) ({total_bytes/1_048_576:.1f} MB) to {ip}:{port}", _BOLD, _CYAN))
    print(f"  Throttle: {throttle_kbps if throttle_kbps else 'Unlimited'} KB/s\n")

    throttle = ThrottleController(throttle_kbps)

    # Connect
    print("  Connecting to 3DS...", end="", flush=True)
    try:
        sock = connect_to_3ds(ip, port)
    except (ConnectionRefusedError, TimeoutError, OSError) as e:
        print(_c(f" FAILED: {e}", _RED))
        sys.exit(1)
    print(_c(" Connected ✓", _GREEN))

    # Handshake
    try:
        send_session_header(sock, throttle_kbps, total_files, resume=not no_resume)
    except ProtocolError as e:
        print(_c(f"\n  Protocol error during handshake: {e}", _RED))
        sock.close()
        sys.exit(1)

    # Transfer each item
    session_start = time.monotonic()
    bytes_overall = 0

    for idx, item in enumerate(items, 1):
        if item.is_dir:
            print(f"  [{idx}/{len(items)}] mkdir  {_c(item.remote_path, _DIM)}")
            try:
                send_directory_entry(sock, item.remote_path)
                queue.mark_done(item)
            except ProtocolError as e:
                print(_c(f"    WARNING: {e}", _YELLOW))
            continue

        # Resume state
        rs = ResumeState(ip, item.remote_path)
        resume_offset = 0 if no_resume else rs.load()
        if resume_offset > 0:
            print(f"  [{idx}/{len(items)}] {_c('RESUME', _YELLOW)} {item.display_name}  "
                  f"from {resume_offset/1_048_576:.1f} MB / {item.size_str}")
        else:
            print(f"  [{idx}/{len(items)}] Sending {_c(item.display_name, _BOLD)}  {item.size_str}")

        queue.mark_sending(item)
        last_print = time.monotonic()

        def progress_cb(sent, total, eff_mbps, _item=item, _rs=rs):
            nonlocal bytes_overall
            queue.update_progress(_item, sent)
            _rs.save(sent)
            now = time.monotonic()
            if now - last_print[0] > 0.3:
                pct = sent / max(total, 1)
                bar = _bar(pct)
                sys.stdout.write(
                    f"\r    {bar} {pct*100:5.1f}%  "
                    f"{sent/1_048_576:6.2f}/{total/1_048_576:.2f} MB  "
                    f"{eff_mbps:.2f} MB/s eff   "
                )
                sys.stdout.flush()
                last_print[0] = now

        # Hack: mutable for nested function
        last_print = [time.monotonic()]

        try:
            send_file(sock, item.local_path, item.remote_path, throttle,
                      resume_offset=resume_offset, progress_cb=progress_cb)
            queue.mark_done(item)
            rs.clear()
            elapsed = time.monotonic() - session_start
            print(f"\n    {_c('✓ Done', _GREEN)}  ({item.size_str})")
            bytes_overall += item.file_size
        except ProtocolError as e:
            queue.mark_failed(item, str(e))
            rs.save(item.bytes_sent)
            print(_c(f"\n    ✗ Failed: {e}  (resume state saved)", _RED))
        except (ConnectionError, OSError) as e:
            queue.mark_failed(item, str(e))
            rs.save(item.bytes_sent)
            print(_c(f"\n    ✗ Connection lost: {e}  (resume state saved)", _RED))
            print(_c("    Run the same command again to resume.", _YELLOW))
            break

    sock.close()

    # Summary
    total_elapsed = time.monotonic() - session_start
    done, failed, cancelled = 0, 0, 0
    for item in queue.snapshot():
        if item.status == ItemStatus.DONE:       done += 1
        elif item.status == ItemStatus.FAILED:   failed += 1
        elif item.status == ItemStatus.CANCELLED: cancelled += 1

    avg_speed = bytes_overall / max(total_elapsed, 1) / 1024
    print(f"\n{'─'*55}")
    print(f"  {_c('✓ Done', _GREEN, _BOLD)}: {done}   {_c('✗ Failed', _RED)}: {failed}   "
          f"{_c('⊘ Cancelled', _DIM)}: {cancelled}")
    print(f"  Total time: {total_elapsed:.1f}s   "
          f"Avg speed: {avg_speed:.0f} KB/s net")
    print(f"{'─'*55}\n")

    if failed > 0:
        sys.exit(2)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="zelned_cli",
        description="⬡ Zel.NeD — High-speed wireless file transfer to Nintendo 3DS",
    )
    parser.add_argument("--ip", metavar="IP",
                        help="IP address of the 3DS console")
    parser.add_argument("--port", type=int, default=TCP_PORT,
                        help=f"TCP port (default: {TCP_PORT})")
    parser.add_argument("--throttle", type=int, default=0, metavar="KBPS",
                        help="Limit bandwidth in KB/s (0 = unlimited)")
    parser.add_argument("--no-resume", action="store_true",
                        help="Disable resume; always start from the beginning")
    parser.add_argument("--discover", action="store_true",
                        help="Scan LAN for 3DS consoles and exit")
    parser.add_argument("--resume-list", action="store_true",
                        help="List all saved resume states and exit")
    parser.add_argument("--resume-clear", action="store_true",
                        help="Delete all saved resume states and exit")
    parser.add_argument("files", nargs="*", metavar="FILE|DIR",
                        help="Files or directories to send")

    args = parser.parse_args()

    if args.discover:
        cmd_discover()
        return

    if args.resume_list:
        states = ResumeState.list_all()
        if not states:
            print("No saved resume states.")
        else:
            print(f"{'─'*55}")
            print(f"  {'Remote path':<40}  {'Offset':>10}")
            print(f"{'─'*55}")
            for s in states:
                print(f"  {s.get('remote_path','?'):<40}  {s.get('offset',0)/1_048_576:>8.1f} MB")
            print(f"{'─'*55}")
        return

    if args.resume_clear:
        ResumeState.clear_all()
        print("All resume states cleared.")
        return

    if not args.ip:
        parser.error("--ip is required for file transfer. Use --discover to find your 3DS IP.")

    if not args.files:
        parser.error("At least one file or directory is required.")

    cmd_send(
        ip=args.ip,
        paths=args.files,
        throttle_kbps=args.throttle,
        port=args.port,
        no_resume=args.no_resume,
    )


if __name__ == "__main__":
    main()
