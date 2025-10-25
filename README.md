# laser-m01-raspberrypi-bluetooth

Raspberry Pi Python driver for the **M01 laser distance module (6‑pin board)** with UART, plus a Bluetooth (RFCOMM/Serial) server so phones/laptops can read distance live or send commands. Includes a CLI, systemd services, and a simple client.

> This repo adapts the great ESP32 demo by **Andres-ros/laser-m01-esp32** to Raspberry Pi + Python + Bluetooth workflow.

---

## Repository layout

```
laser-m01-raspberrypi-bluetooth/
├─ README.md
├─ LICENSE
├─ requirements.txt
├─ src/
│  └─ laser_m01/
│     ├─ __init__.py
│     ├─ m01.py              # UART driver & parser
│     ├─ main.py             # CLI tool: measure / laser on-off / reset
│     └─ bt_server.py        # Bluetooth SPP (RFCOMM) server
├─ clients/
│  ├─ python_spp_client.py   # Example cross‑platform client
│  └─ android_ble_notes.md   # Notes for BLE alternative (if you prefer BLE)
├─ systemd/
│  ├─ laser-m01.service      # Run the CLI in streaming mode on boot (optional)
│  └─ laser-m01-bt.service   # Run the Bluetooth server on boot
└─ examples/
   └─ stream_to_csv.sh       # Log distance to CSV
```

---

## Wiring (Pi ↔ M01, 3.3V logic)

* **M01 VCC (VIN/MIN)** → Raspberry Pi **3V3**
* **M01 GND**           → Raspberry Pi **GND**
* **M01 TXD**           → Raspberry Pi **GPIO15 / RXD** (physical pin 10)
* **M01 RXD**           → Raspberry Pi **GPIO14 / TXD** (physical pin 8)
* **M01 ENA** (if available) → Any 3V3 logic GPIO (we use **GPIO5** in code) or tie to 3V3

> Important: TX of module → RX of Pi, and RX of module ← TX of Pi. Common ground is required.

Enable UART on the Pi: `sudo raspi-config` → *Interface Options* → *Serial* → disable login shell over serial, **enable hardware serial**. Reboot.

---

## Installation (Raspberry Pi OS)

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv bluetooth bluez bluez-tools pi-bluetooth
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

If you want the services:

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
# choose one or both
sudo systemctl enable --now laser-m01.service
sudo systemctl enable --now laser-m01-bt.service
```

---

## Python package files

### requirements.txt

```
pyserial>=3.5
PyBluez>=0.23
```

### src/laser_m01/**init**.py

```python
__all__ = ["M01", "open_default", "M01Error"]
from .m01 import M01, open_default, M01Error
```

### src/laser_m01/m01.py

```python
#!/usr/bin/env python3
"""
M01 laser distance module UART driver for Raspberry Pi.
Assumes ASCII command interface similar to ESP32 demo:
  Q = single measurement
  L = laser ON
  K = laser OFF
  R = module reset (ENA low→high pulse handled in software via GPIO)
Outputs typically look like b"1.234\r\n" (meters). If your unit speaks at 115200
or returns different frames, adjust BAUDRATE and parse_line().
"""
from __future__ import annotations
import time
import re
from dataclasses import dataclass
from typing import Optional

import serial  # pyserial
try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except Exception:
    _HAS_GPIO = False

FLOAT_RE = re.compile(rb"([0-9]+\.[0-9]+)")

class M01Error(Exception):
    pass

@dataclass
class M01:
    port: str = "/dev/serial0"      # Pi's PL011 UART
    baudrate: int = 9600              # some units use 115200
    ena_gpio: Optional[int] = 5       # None if wired high
    timeout: float = 0.5

    def __post_init__(self):
        self.ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        if self.ena_gpio is not None and _HAS_GPIO:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.ena_gpio, GPIO.OUT, initial=GPIO.HIGH)

    def close(self):
        try:
            self.ser.close()
        finally:
            if self.ena_gpio is not None and _HAS_GPIO:
                GPIO.cleanup(self.ena_gpio)

    # --- low-level helpers ---
    def _write(self, data: bytes):
        self.ser.write(data)
        self.ser.flush()

    def _readline(self) -> bytes:
        return self.ser.readline()

    # --- module commands ---
    def laser_on(self):
        self._write(b"L")

    def laser_off(self):
        self._write(b"K")

    def measure_once(self) -> float:
        """Trigger a single measurement and parse distance in meters."""
        self._write(b"Q")
        raw = self._readline()
        if not raw:
            raise M01Error("No data from module; check wiring and baudrate.")
        m = FLOAT_RE.search(raw)
        if not m:
            raise M01Error(f"Unrecognized frame: {raw!r}")
        return float(m.group(1))

    def reset(self):
        if self.ena_gpio is None or not _HAS_GPIO:
            # fallback: send 'R' and hope module interprets; or user wired ENA to 3V3
            self._write(b"R")
            return
        # Pulse ENA low then high
        GPIO.output(self.ena_gpio, GPIO.LOW)
        time.sleep(0.05)
        GPIO.output(self.ena_gpio, GPIO.HIGH)
        time.sleep(0.2)


def open_default(**kw) -> M01:
    return M01(**kw)
```

### src/laser_m01/main.py (CLI)

```python
#!/usr/bin/env python3
import argparse, sys, time, json
from .m01 import open_default, M01Error

MODES = {"once", "stream"}

def _print(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description="M01 laser module CLI")
    ap.add_argument("mode", choices=MODES, help="once = single reading; stream = continuous readings")
    ap.add_argument("--port", default="/dev/serial0")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--ena-gpio", type=int, default=5)
    ap.add_argument("--hz", type=float, default=5.0, help="streaming rate")
    args = ap.parse_args()

    m01 = open_default(port=args.port, baudrate=args.baud, ena_gpio=args.ena_gpio)
    try:
        if args.mode == "once":
            m01.laser_on()
            d = m01.measure_once()
            _print({"meters": d, "timestamp": time.time()})
        else:
            m01.laser_on()
            period = 1.0 / max(args.hz, 0.1)
            while True:
                try:
                    d = m01.measure_once()
                    _print({"meters": d, "timestamp": time.time()})
                except M01Error as e:
                    _print({"error": str(e), "timestamp": time.time()})
                time.sleep(period)
    finally:
        m01.laser_off()
        m01.close()

if __name__ == "__main__":
    main()
```

### src/laser_m01/bt_server.py (Bluetooth SPP) Amir Mobasheraghdam

```python
#!/usr/bin/env python3
"""Bluetooth RFCOMM (Serial Port Profile) server for M01 readings.
Clients (Android, PC, etc.) connect to SPP and receive newline‑delimited JSON.
They can also send ASCII commands: Q (once), S (start stream), P (pause), L, K, R.
"""
import sys, time, json, threading
from bluetooth import BluetoothSocket, RFCOMM, advertise_service, SERIAL_PORT_CLASS, SERIAL_PORT_PROFILE
from .m01 import open_default, M01Error

SERVICE_NAME = "M01 Laser"
SERVICE_UUID = "00001101-0000-1000-8000-00805F9B34FB"  # SPP

class Server:
    def __init__(self, port="/dev/serial0", baud=9600, ena_gpio=5, hz=5.0):
        self.dev = open_default(port=port, baudrate=baud, ena_gpio=ena_gpio)
        self.hz = hz
        self.streaming = False
        self._stop = False

    def start(self):
        self.dev.laser_on()
        sock = BluetoothSocket(RFCOMM)
        sock.bind(("", 1))
        sock.listen(1)
        advertise_service(sock, SERVICE_NAME, service_id=SERVICE_UUID,
                          service_classes=[SERVICE_UUID, SERIAL_PORT_CLASS],
                          profiles=[SERIAL_PORT_PROFILE])
        print("[BT] Waiting for RFCOMM client…")
        try:
            client, addr = sock.accept()
            print(f"[BT] Client connected: {addr}")
            t = threading.Thread(target=self._tx_loop, args=(client,), daemon=True)
            t.start()
            # RX loop
            while not self._stop:
                data = client.recv(1)
                if not data:
                    break
                cmd = data.decode(errors="ignore").strip().upper()
                if cmd == "Q":
                    self._send_one(client)
                elif cmd == "S":
                    self.streaming = True
                elif cmd == "P":
                    self.streaming = False
                elif cmd == "L":
                    self.dev.laser_on()
                elif cmd == "K":
                    self.dev.laser_off()
                elif cmd == "R":
                    self.dev.reset()
        finally:
            self.dev.laser_off()
            self.dev.close()
            try:
                sock.close()
            except Exception:
                pass

    def _tx_loop(self, client):
        period = 1.0 / max(self.hz, 0.1)
        while not self._stop:
            if self.streaming:
                try:
                    d = self.dev.measure_once()
                    msg = json.dumps({"meters": d, "ts": time.time()}) + "\n"
                except M01Error as e:
                    msg = json.dumps({"error": str(e), "ts": time.time()}) + "\n"
                try:
                    client.send(msg.encode())
                except Exception:
                    break
            time.sleep(period)

    def _send_one(self, client):
        try:
            d = self.dev.measure_once()
            msg = json.dumps({"meters": d, "ts": time.time()}) + "\n"
        except M01Error as e:
            msg = json.dumps({"error": str(e), "ts": time.time()}) + "\n"
        client.send(msg.encode())

if __name__ == "__main__":
    Server().start()
```

---

## Example client (Python)

`clients/python_spp_client.py`

```python
#!/usr/bin/env python3
"""Connect to the Raspberry Pi's SPP service and print readings."""
import sys, time, json
from bluetooth import BluetoothSocket, RFCOMM

if len(sys.argv) < 2:
    print("Usage: python python_spp_client.py <server-mac>")
    sys.exit(1)

mac = sys.argv[1]

sock = BluetoothSocket(RFCOMM)
sock.connect((mac, 1))
# Start streaming
sock.send(b"S")
try:
    while True:
        line = b""
        while not line.endswith(b"\n"):
            chunk = sock.recv(64)
            if not chunk:
                raise SystemExit("Disconnected")
            line += chunk
        try:
            obj = json.loads(line.decode().strip())
        except Exception:
            continue
        print(obj)
except KeyboardInterrupt:
    sock.send(b"P")
finally:
    sock.close()
```

---

## Systemd service files

### systemd/laser-m01.service

```ini
[Unit]
Description=Laser M01 CLI streaming service
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/laser-m01-raspberrypi-bluetooth
ExecStart=/home/pi/laser-m01-raspberrypi-bluetooth/.venv/bin/python -m laser_m01.main stream --baud 9600 --ena-gpio 5 --hz 5
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### systemd/laser-m01-bt.service

```ini
[Unit]
Description=Laser M01 Bluetooth RFCOMM server
After=bluetooth.target
Requires=bluetooth.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/laser-m01-raspberrypi-bluetooth
ExecStart=/home/pi/laser-m01-raspberrypi-bluetooth/.venv/bin/python -m laser_m01.bt_server
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

---

## CLI usage

```bash
# One reading
python -m laser_m01.main once
# Continuous stream at 10 Hz (JSON to stdout)
python -m laser_m01.main stream --hz 10
# Different baudrate if your unit needs it
python -m laser_m01.main stream --baud 115200
```

Log to CSV:

```bash
./examples/stream_to_csv.sh > distances.csv
```

`examples/stream_to_csv.sh`

```bash
#!/usr/bin/env bash
python -m laser_m01.main stream | jq -r '"\(.timestamp),\(.meters)"'
```

---

## Bluetooth pairing & testing

1. Put Pi in discoverable mode:

   ```bash
   sudo bluetoothctl
   [bluetooth]# power on
   [bluetooth]# discoverable on
   [bluetooth]# pairable on
   [bluetooth]# agent on
   [bluetooth]# default-agent
   ```
2. From your phone/PC, pair with the Pi (PIN may be requested).Amir Mobasheraghdam
3. Use a serial terminal app (e.g., *Serial Bluetooth Terminal* on Android) to open the **SPP** service.
4. Send `S` to start streaming, `P` to pause, `Q` once, and `L/K/R` for control.

---

## Troubleshooting

* No readings? Check **crossed TX/RX**, common **GND**, **ENA** high, and try `--baud 115200`.
* Permission error on `/dev/serial0`? Add your user to `dialout`: `sudo usermod -aG dialout $USER` and reboot.
* Bluetooth service won’t start? Ensure `bluetooth.service` is active, and `PyBluez` installed.

---

## License

MIT

---

## Credits

* Original ESP32 reference and pinout notes inspired by **Andres-ros/laser-m01-esp32**.
