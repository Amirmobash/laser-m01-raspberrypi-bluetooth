from __future__ import annotations

import json
import socket
import threading
import time
from datetime import UTC, datetime

from .device import M01Device, M01Error
from .protocol import Measurement


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _measurement_payload(measurement: Measurement) -> dict[str, object]:
    return {
        "type": "measurement",
        "meters": measurement.meters,
        "millimeters": measurement.millimeters,
        "timestamp": _utc_now(),
    }


class RFCOMMServer:
    """Single-client Bluetooth Classic RFCOMM bridge for the M01 driver.

    Commands are intentionally tiny so the server works with generic serial
    terminal apps:

    Q = one measurement
    S = start streaming
    P = pause streaming
    L = laser on
    K = laser off
    R = reset (requires ENA GPIO)
    H = command help
    """

    def __init__(self, device: M01Device, *, channel: int = 1, hz: float = 5.0) -> None:
        if channel < 1 or channel > 30:
            raise ValueError("RFCOMM channel must be between 1 and 30")
        if hz <= 0:
            raise ValueError("stream rate must be positive")

        self.device = device
        self.channel = channel
        self.hz = hz
        self._stop = threading.Event()
        self._streaming = threading.Event()
        self._send_lock = threading.Lock()

    def stop(self) -> None:
        self._stop.set()
        self._streaming.clear()

    def serve_forever(self) -> None:
        if not hasattr(socket, "AF_BLUETOOTH") or not hasattr(socket, "BTPROTO_RFCOMM"):
            raise RuntimeError("this Python build does not provide Linux Bluetooth sockets")

        server = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        server.bind(("", self.channel))
        server.listen(1)
        server.settimeout(1.0)

        print(f"RFCOMM listening on channel {self.channel}", flush=True)

        try:
            while not self._stop.is_set():
                try:
                    client, address = server.accept()
                except socket.timeout:
                    continue

                print(f"RFCOMM client connected: {address}", flush=True)
                try:
                    self._serve_client(client)
                finally:
                    self._streaming.clear()
                    try:
                        self.device.laser_off()
                    except M01Error:
                        pass
                    try:
                        client.close()
                    except OSError:
                        pass
                    print("RFCOMM client disconnected", flush=True)
        finally:
            server.close()

    def _serve_client(self, client: socket.socket) -> None:
        client.settimeout(1.0)
        client_stop = threading.Event()
        self._streaming.clear()
        tx_thread = threading.Thread(
            target=self._stream_loop,
            args=(client, client_stop),
            daemon=True,
        )
        tx_thread.start()

        try:
            self._send_json(
                client,
                {
                    "type": "hello",
                    "service": "M01 laser rangefinder",
                    "commands": "Q=measure S=stream P=pause L=on K=off R=reset H=help",
                },
            )

            while not self._stop.is_set():
                try:
                    data = client.recv(128)
                except socket.timeout:
                    continue
                except OSError:
                    break

                if not data:
                    break

                for byte in data:
                    char = chr(byte).upper()
                    if char.isspace():
                        continue
                    if not self._handle_command(client, char):
                        return
        finally:
            client_stop.set()
            self._streaming.clear()
            tx_thread.join(timeout=1.0)

    def _handle_command(self, client: socket.socket, command: str) -> bool:
        try:
            if command == "Q":
                self.device.laser_on()
                measurement = self.device.measure_once()
                self._send_json(client, _measurement_payload(measurement))
            elif command == "S":
                self.device.laser_on()
                self._streaming.set()
                self._send_json(client, {"type": "state", "streaming": True})
            elif command == "P":
                self._streaming.clear()
                self._send_json(client, {"type": "state", "streaming": False})
            elif command == "L":
                self.device.laser_on()
                self._send_json(client, {"type": "state", "laser": True})
            elif command == "K":
                self._streaming.clear()
                self.device.laser_off()
                self._send_json(client, {"type": "state", "laser": False, "streaming": False})
            elif command == "R":
                self._streaming.clear()
                self.device.reset()
                self._send_json(client, {"type": "state", "reset": True})
            elif command == "H":
                self._send_json(
                    client,
                    {
                        "type": "help",
                        "commands": {
                            "Q": "one measurement",
                            "S": "start streaming",
                            "P": "pause streaming",
                            "L": "laser on",
                            "K": "laser off",
                            "R": "reset module (ENA GPIO required)",
                            "H": "show this help",
                        },
                    },
                )
            else:
                self._send_json(client, {"type": "error", "error": f"unknown command: {command}"})
        except M01Error as exc:
            self._send_json(client, {"type": "error", "error": str(exc), "timestamp": _utc_now()})
        except OSError:
            return False

        return True

    def _stream_loop(self, client: socket.socket, client_stop: threading.Event) -> None:
        period = 1.0 / self.hz
        next_tick = time.monotonic()

        while not self._stop.is_set() and not client_stop.is_set():
            if not self._streaming.wait(timeout=0.25):
                continue

            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.05))
                continue
            next_tick = now + period

            try:
                measurement = self.device.measure_once()
                self._send_json(client, _measurement_payload(measurement))
            except M01Error as exc:
                try:
                    self._send_json(
                        client,
                        {"type": "error", "error": str(exc), "timestamp": _utc_now()},
                    )
                except OSError:
                    return
            except OSError:
                return

    def _send_json(self, client: socket.socket, payload: dict[str, object]) -> None:
        line = (json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        with self._send_lock:
            client.sendall(line)
