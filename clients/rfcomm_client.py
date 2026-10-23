#!/usr/bin/env python3
"""Minimal Linux RFCOMM client for the M01 Raspberry Pi bridge."""

from __future__ import annotations

import argparse
import socket
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Connect to the M01 RFCOMM bridge")
    parser.add_argument("address", help="Bluetooth MAC address of the Raspberry Pi")
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument(
        "--command",
        default="S",
        choices=("Q", "S", "P", "L", "K", "R", "H"),
        help="command sent immediately after connecting",
    )
    args = parser.parse_args()

    if not hasattr(socket, "AF_BLUETOOTH") or not hasattr(socket, "BTPROTO_RFCOMM"):
        print("This client requires Linux Bluetooth socket support.", file=sys.stderr)
        return 2

    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.connect((args.address, args.channel))
    sock.sendall(args.command.encode("ascii"))

    try:
        buffer = bytearray()
        while True:
            chunk = sock.recv(1024)
            if not chunk:
                break
            buffer.extend(chunk)
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                if line:
                    print(line.decode("utf-8", errors="replace"))
    except KeyboardInterrupt:
        try:
            sock.sendall(b"P")
        except OSError:
            pass
    finally:
        sock.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
