from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import UTC, datetime
from collections.abc import Sequence

from . import __version__
from .device import M01Device, M01Error
from .protocol import Measurement


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None else float(value)


def _ena_default() -> int:
    value = os.getenv("M01_ENA_GPIO", "-1").strip().lower()
    if value in {"", "none", "off", "-1"}:
        return -1
    return int(value)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_measurement(measurement: Measurement) -> str:
    return json.dumps(
        {
            "meters": measurement.meters,
            "millimeters": measurement.millimeters,
            "timestamp": _timestamp(),
        },
        separators=(",", ":"),
    )


def _add_device_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", default=os.getenv("M01_PORT", "/dev/serial0"))
    parser.add_argument("--baud", type=int, default=_env_int("M01_BAUD", 9600))
    parser.add_argument(
        "--ena-gpio",
        type=int,
        default=_ena_default(),
        help="BCM GPIO for ENA; use -1 when ENA is tied high or unavailable",
    )


def _open_device(args: argparse.Namespace) -> M01Device:
    ena_gpio = None if args.ena_gpio < 0 else args.ena_gpio
    return M01Device(port=args.port, baudrate=args.baud, ena_gpio=ena_gpio)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="m01",
        description=(
            "Raspberry Pi UART driver and Bluetooth RFCOMM bridge "
            "for the M01 laser rangefinder"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    measure = sub.add_parser("measure", help="take one measurement")
    _add_device_args(measure)
    measure.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of a human-readable line",
    )

    stream = sub.add_parser("stream", help="stream measurements to stdout")
    _add_device_args(stream)
    stream.add_argument("--hz", type=float, default=_env_float("M01_HZ", 5.0))
    stream.add_argument("--json", action="store_true", help="emit newline-delimited JSON")

    laser = sub.add_parser("laser", help="switch the laser emitter on or off")
    _add_device_args(laser)
    laser.add_argument("state", choices=("on", "off"))

    reset = sub.add_parser("reset", help="reset the module by pulsing ENA")
    _add_device_args(reset)

    rfcomm = sub.add_parser(
        "rfcomm-server",
        help="serve measurements over Bluetooth Classic RFCOMM",
    )
    _add_device_args(rfcomm)
    rfcomm.add_argument("--channel", type=int, default=_env_int("M01_RFCOMM_CHANNEL", 1))
    rfcomm.add_argument("--hz", type=float, default=_env_float("M01_HZ", 5.0))

    return parser


def _run_measure(args: argparse.Namespace) -> int:
    with _open_device(args) as device:
        device.laser_on()
        try:
            measurement = device.measure_once()
        finally:
            device.laser_off()

    if args.json:
        print(_json_measurement(measurement))
    else:
        print(f"{measurement.meters:.3f} m ({measurement.millimeters} mm)")
    return 0


def _run_stream(args: argparse.Namespace) -> int:
    if args.hz <= 0:
        raise M01Error("--hz must be greater than zero")

    period = 1.0 / args.hz
    with _open_device(args) as device:
        device.laser_on()
        try:
            next_tick = time.monotonic()
            while True:
                measurement = device.measure_once()
                if args.json:
                    print(_json_measurement(measurement), flush=True)
                else:
                    print(f"{measurement.meters:.3f}", flush=True)

                next_tick += period
                time.sleep(max(0.0, next_tick - time.monotonic()))
        except KeyboardInterrupt:
            return 0
        finally:
            device.laser_off()


def _run_laser(args: argparse.Namespace) -> int:
    with _open_device(args) as device:
        if args.state == "on":
            device.laser_on()
        else:
            device.laser_off()
    return 0


def _run_reset(args: argparse.Namespace) -> int:
    with _open_device(args) as device:
        device.reset()
    print("M01 reset complete")
    return 0


def _run_rfcomm(args: argparse.Namespace) -> int:
    from .rfcomm import RFCOMMServer

    with _open_device(args) as device:
        server = RFCOMMServer(device, channel=args.channel, hz=args.hz)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.stop()
    return 0


def _raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main(argv: Sequence[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "measure": _run_measure,
        "stream": _run_stream,
        "laser": _run_laser,
        "reset": _run_reset,
        "rfcomm-server": _run_rfcomm,
    }

    try:
        return handlers[args.command](args)
    except M01Error as exc:
        print(f"m01: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"m01: operating-system error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
