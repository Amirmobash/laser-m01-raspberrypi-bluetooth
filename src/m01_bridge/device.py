from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from .protocol import (
    FrameParser,
    LASER_OFF,
    LASER_ON,
    QUICK_MEASURE,
    READ_RESULT,
    Measurement,
)


class M01Error(RuntimeError):
    """Base exception for device and transport failures."""


class M01Timeout(M01Error):
    """Raised when the module does not return a valid measurement in time."""


class _EnableLine:
    def __init__(self, gpio: int | None) -> None:
        self.gpio = gpio
        self._device: Any | None = None

        if gpio is None:
            return

        try:
            from gpiozero import DigitalOutputDevice
        except ImportError as exc:
            raise M01Error(
                "ENA GPIO control requires the optional 'gpio' extra: "
                "pip install '.[gpio]'"
            ) from exc

        try:
            self._device = DigitalOutputDevice(gpio, active_high=True, initial_value=True)
        except Exception as exc:  # gpiozero chooses the platform backend at runtime
            raise M01Error(f"could not initialize ENA on BCM GPIO {gpio}: {exc}") from exc

    @property
    def available(self) -> bool:
        return self._device is not None

    def high(self) -> None:
        if self._device is not None:
            self._device.on()

    def low(self) -> None:
        if self._device is not None:
            self._device.off()

    def close(self) -> None:
        if self._device is not None:
            # Leave the module enabled before releasing the pin. If the board
            # requires ENA to remain driven, wire ENA to 3V3 instead.
            self._device.on()
            self._device.close()


class M01Device:
    """Thread-safe UART driver for the six-pin M01 laser rangefinder."""

    def __init__(
        self,
        *,
        port: str = "/dev/serial0",
        baudrate: int = 9600,
        ena_gpio: int | None = None,
        serial_timeout: float = 0.10,
        serial_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.ena_gpio = ena_gpio
        self.serial_timeout = serial_timeout
        self._lock = threading.RLock()
        self._parser = FrameParser()
        self._enable = _EnableLine(ena_gpio)

        if serial_factory is None:
            try:
                import serial
            except ImportError as exc:
                raise M01Error("pyserial is required to access the M01 UART") from exc
            serial_factory = serial.Serial

        try:
            self._serial = serial_factory(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.serial_timeout,
                write_timeout=1.0,
            )
        except Exception as exc:
            self._enable.close()
            raise M01Error(
                f"could not open {self.port} at {self.baudrate} baud: {exc}"
            ) from exc

    def __enter__(self) -> M01Device:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            try:
                self._serial.close()
            finally:
                self._enable.close()

    def _write(self, command: bytes) -> None:
        try:
            written = self._serial.write(command)
            self._serial.flush()
        except Exception as exc:
            raise M01Error(f"UART write failed: {exc}") from exc

        if written != len(command):
            raise M01Error(f"short UART write: {written}/{len(command)} bytes")

    def _clear_input(self) -> None:
        self._parser.reset()
        try:
            self._serial.reset_input_buffer()
        except AttributeError:
            # Small fakes used in tests may not implement reset_input_buffer().
            while getattr(self._serial, "in_waiting", 0):
                self._serial.read(getattr(self._serial, "in_waiting", 1))
        except Exception as exc:
            raise M01Error(f"could not clear UART input: {exc}") from exc

    def _read_measurement(self, timeout: float) -> Measurement:
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                waiting = int(getattr(self._serial, "in_waiting", 0))
                chunk = self._serial.read(min(max(waiting, 1), 128))
            except Exception as exc:
                raise M01Error(f"UART read failed: {exc}") from exc

            if not chunk:
                continue

            measurements = self._parser.feed(chunk)
            if measurements:
                return measurements[-1]

        raise M01Timeout(f"no valid M01 measurement received within {timeout:.2f}s")

    def laser_on(self) -> None:
        with self._lock:
            self._write(LASER_ON)

    def laser_off(self) -> None:
        with self._lock:
            self._write(LASER_OFF)

    def measure_once(
        self,
        *,
        timeout: float = 2.0,
        fallback_timeout: float = 0.8,
    ) -> Measurement:
        """Trigger a quick measurement and return the decoded distance.

        The reference M01 implementation first sends the quick-measure command.
        If no valid frame arrives, it asks for the most recent result as a
        fallback. Both paths use the binary 0xAA protocol.
        """

        with self._lock:
            self._clear_input()
            self._write(QUICK_MEASURE)
            try:
                return self._read_measurement(timeout)
            except M01Timeout:
                self._write(READ_RESULT)
                try:
                    return self._read_measurement(fallback_timeout)
                except M01Timeout as second_timeout:
                    raise M01Timeout(
                        "M01 did not return a valid measurement after quick-measure "
                        "and read-result commands"
                    ) from second_timeout

    def reset(self, *, low_seconds: float = 0.12, settle_seconds: float = 0.40) -> None:
        """Reset the module by pulsing ENA low, matching the reference design."""

        with self._lock:
            if not self._enable.available:
                raise M01Error(
                    "reset requires ENA to be connected to a GPIO; "
                    "otherwise power-cycle the module manually"
                )

            self._enable.low()
            time.sleep(low_seconds)
            self._enable.high()
            time.sleep(settle_seconds)
            self._clear_input()
