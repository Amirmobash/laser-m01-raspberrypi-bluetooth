# M01 Laser-Entfernungsmesser am Raspberry Pi — UART + Bluetooth RFCOMM

Dieses Repository enthält einen kompakten Python-Treiber und eine Bluetooth-Bridge für das **M01 Laser-Distanzmodul mit 6-poliger Platine** am Raspberry Pi. Der Raspberry Pi kommuniziert über UART mit dem Sensor, wertet die binären `0xAA`-Messframes aus und kann die Messwerte über **Bluetooth Classic RFCOMM** als JSON-Zeilen bereitstellen.

**English:** [README.md](README.md)

Das Projekt bleibt bewusst überschaubar: ein Treiber, eine CLI, ein RFCOMM-Server, Tests und eine systemd-Unit. Keine unnötige Web-Anwendung und kein veralteter Bluetooth-Python-Wrapper im Laufzeitpfad.

## Funktionen

- Einzelmessung über UART.
- Kontinuierliche Messwertausgabe mit einstellbarer Frequenz.
- Laser ein- und ausschalten.
- Hardware-Reset über `ENA`, wenn dieser Pin mit einem Raspberry-Pi-GPIO verbunden ist.
- Bluetooth-RFCOMM-Server auf Kanal 1 oder einem frei wählbaren Kanal.
- Zeilenbasiertes JSON für Android, Linux, Notebooks oder andere Rechner.
- systemd-Betrieb ohne fest codierten Benutzer `pi`.

## Hardware und Verdrahtung

Die Kommunikation arbeitet mit **3,3-V-Logik**. TX und RX werden gekreuzt.

![Verdrahtung M01 zu Raspberry Pi](assets/m01-raspberry-pi-wiring.svg)

| M01-Signal | Raspberry Pi | Header-Pin | Hinweis |
|---|---|---:|---|
| `MIN / 3V3` | `3V3` | 1 oder 17 | Strombedarf der eigenen Modulversion prüfen. |
| `GND` | `GND` | z. B. 6 | Gemeinsame Masse ist zwingend erforderlich. |
| `TXD` | `GPIO15 / RXD` | 10 | TX des Moduls geht auf RX des Pi. |
| `RXD` | `GPIO14 / TXD` | 8 | RX des Moduls kommt von TX des Pi. |
| `ENA` | `GPIO5` | 29 | Optional, wenn ENA am Kabelbaum verfügbar ist. |

Beim dokumentierten Referenzmodul war Rot = 3,3 V, Schwarz = GND, Gelb = TXD und Grün = RXD. **Kabelfarben nicht ungeprüft übernehmen**; Hersteller und Revisionen können abweichen.

Falls das Modul an der 3,3-V-Schiene des Raspberry Pi instabil läuft, eine ausreichend dimensionierte geregelte 3,3-V-Versorgung verwenden und die Masse verbinden.

## UART am Raspberry Pi aktivieren

```bash
sudo raspi-config
```

Bei der seriellen Schnittstelle die Login-Shell über UART deaktivieren und die serielle Hardware aktivieren. Danach neu starten.

Standardmäßig wird `/dev/serial0` verwendet.

Den eigenen Benutzer zur Gruppe `dialout` hinzufügen:

```bash
sudo usermod -aG dialout "$USER"
```

Danach einmal ab- und wieder anmelden.

## Installation

Zielsystem: Raspberry Pi OS Bookworm oder neuer, Python 3.11+.

```bash
sudo apt update
sudo apt install -y python3-venv bluez

git clone https://github.com/Amirmobash/laser-m01-raspberrypi-bluetooth.git
cd laser-m01-raspberrypi-bluetooth

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

Wenn `ENA` an einen GPIO angeschlossen ist und der Software-Reset genutzt werden soll:

```bash
pip install -e ".[gpio]"
```

## Erste Messung

Viele M01-Module arbeiten mit 9600 Baud. Falls keine gültigen Frames empfangen werden, ist 115200 ein sinnvoller zweiter Versuch.

```bash
m01 measure
```

Beispiel:

```text
1.534 m (1534 mm)
```

Als JSON:

```bash
m01 measure --json
```

Abweichender Port oder Baudrate:

```bash
m01 measure --port /dev/ttyAMA0 --baud 115200
```

## CLI

```bash
# Eine Messung; danach wird der Laser wieder ausgeschaltet
m01 measure

# Kontinuierliche Messung
m01 stream --hz 5

# JSON pro Zeile
m01 stream --hz 10 --json

# Laser direkt steuern
m01 laser on
m01 laser off

# Reset über ENA an BCM GPIO 5
m01 reset --ena-gpio 5

# Bluetooth-RFCOMM-Bridge
m01 rfcomm-server --channel 1 --hz 5
```

Alternativ können die Einstellungen über Umgebungsvariablen gesetzt werden:

```text
M01_PORT=/dev/serial0
M01_BAUD=9600
M01_ENA_GPIO=-1
M01_RFCOMM_CHANNEL=1
M01_HZ=5
```

`M01_ENA_GPIO=-1` bedeutet, dass ENA nicht von der Software gesteuert wird.

## Bluetooth-Protokoll

Der RFCOMM-Server akzeptiert bewusst einfache Einzelzeichen, damit er mit normalen seriellen Bluetooth-Terminals funktioniert:

| Befehl | Funktion |
|---|---|
| `Q` | Einzelmessung. |
| `S` | Streaming starten. |
| `P` | Streaming pausieren. |
| `L` | Laser ein. |
| `K` | Laser aus. |
| `R` | Reset über ENA. |
| `H` | Befehlsübersicht ausgeben. |

Die Antworten sind JSON-Objekte, jeweils eine Zeile pro Nachricht:

```json
{"type":"measurement","meters":1.534,"millimeters":1534,"timestamp":"2026-10-23T16:10:12.421Z"}
```

### Pairing und SPP-Erkennung

Pairing und Vertrauen werden mit BlueZ eingerichtet:

```bash
bluetoothctl
power on
agent on
default-agent
pairable on
discoverable on
```

Nach dem Pairing lauscht der Python-Server direkt auf dem eingestellten RFCOMM-Kanal. Manche Terminal-Apps erwarten zusätzlich einen SDP-Eintrag für das Profil **Serial Port**. Wenn die lokale SDP-Registrierung auf dem System aktiviert ist:

```bash
sudo sdptool add --channel=1 SP
```

BlueZ-Konfigurationen unterscheiden sich. Wenn `sdptool add` lokal deaktiviert ist, sollte die Bluetooth-Konfiguration nicht unnötig aufgeweicht werden. Dann einen Client verwenden, der direkt auf den bekannten RFCOMM-Kanal verbinden kann, oder SDP gezielt für die eigene Installation konfigurieren.

**iPhone/iPad:** Normale iOS-Apps stellen generisches Bluetooth-Classic-SPP nicht wie Android oder Linux zur Verfügung. Für iOS wäre eine separate BLE-GATT-Implementierung oder ein MFi-Zubehörpfad erforderlich. Diese Funktion wird hier nicht vorgetäuscht.

Ein einfacher Linux-Client liegt im Repository:

```bash
python clients/rfcomm_client.py AA:BB:CC:DD:EE:FF --command S
```

## Automatischer Start mit systemd

Für eine feste Installation:

```bash
sudo ./scripts/install.sh
```

Das Skript kopiert das Projekt nach `/opt/m01-bridge`, erstellt dort eine virtuelle Umgebung, installiert die systemd-Unit und ergänzt den aufrufenden Benutzer um die Gruppe `dialout`. Der Dienst wird absichtlich noch nicht automatisch aktiviert.

Konfiguration prüfen:

```bash
sudo nano /etc/default/m01-bridge
```

Danach den Dienst mit dem echten Raspberry-Pi-Benutzernamen aktivieren:

```bash
sudo systemctl enable --now m01-bridge@$USER.service
sudo systemctl status m01-bridge@$USER.service
```

Logs:

```bash
journalctl -u m01-bridge@$USER.service -f
```

## Verwendetes M01-Binärprotokoll

Die Implementierung orientiert sich am funktionierenden ESP32-Referenzprojekt [Andres-ros/laser-m01-esp32](https://github.com/Andres-ros/laser-m01-esp32). Das M01-Modul wird hier **nicht** mit den ASCII-Zeichen `Q`, `L`, `K` oder `R` über UART gesteuert. Diese Zeichen gehören nur zur Bedienoberfläche der CLI beziehungsweise der RFCOMM-Bridge.

Die eigentlichen Modulbefehle sind Binärframes:

```text
Laser ein:       AA 00 01 BE 00 01 00 01 C1
Laser aus:       AA 00 01 BE 00 01 00 00 C0
Schnellmessung:  AA 00 00 22 00 01 00 00 23
Ergebnis lesen:  AA 80 00 22 A2
```

Eine Messantwort wird als 13-Byte-Frame mit `0xAA` am Anfang erwartet. Der Parser prüft die Prüfsumme, akzeptiert die Messfunktionen `0x20`, `0x21` und `0x22`, dekodiert vier BCD-Distanzbytes und synchronisiert sich nach Störbytes oder beschädigten Frames wieder neu.

## Fehlersuche

**Keine Messdaten**

- `TXD` des Moduls muss auf `GPIO15/RXD` gehen.
- `RXD` des Moduls muss von `GPIO14/TXD` kommen.
- Gemeinsame Masse prüfen.
- ENA muss aktiv sein, wenn die Modulversion diesen Eingang herausführt.
- 9600 und 115200 Baud testen.
- Login-Konsole auf der seriellen Schnittstelle deaktivieren.

**Keine Berechtigung auf `/dev/serial0`**

```bash
sudo usermod -aG dialout "$USER"
```

Danach neu anmelden.

**RFCOMM läuft, aber die Terminal-App findet keinen SPP-Dienst**

Zuerst Pairing prüfen, `bluetooth.service` kontrollieren und den Abschnitt zur SPP-Erkennung oben lesen. RFCOMM-Verbindung und SDP-Diensterkennung sind bei BlueZ zwei getrennte Themen.

**Reset meldet, dass ENA nicht verfügbar ist**

Das ist beabsichtigt. Der Treiber erfindet keinen undokumentierten UART-Resetbefehl. ENA an einen GPIO anschließen und das `gpio`-Extra installieren oder das Modul manuell aus- und wieder einschalten.

## Tests

Die Protokollschicht ist ohne Hardware testbar. Abgedeckt sind unter anderem Prüfsummenfehler, ungültige BCD-Werte, Störbytes auf UART und der Fallback zum Lesen des letzten Ergebnisses.

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## Sicherheit

Das Modul ist ein Laser-Entfernungsmesser und kann einen nicht oder nur schwer sichtbaren Infrarot-Emitter verwenden. Die Sicherheitsklasse und Herstellerhinweise der tatsächlich verwendeten Modulversion beachten. Nicht auf Augen oder nahe reflektierende Flächen richten.

## Credits und Lizenz

Die binären M01-Kommandoframes und das funktionierende ESP32-Verhalten stammen aus dem MIT-lizenzierten Referenzprojekt [Andres-ros/laser-m01-esp32](https://github.com/Andres-ros/laser-m01-esp32). Die Raspberry-Pi-Implementierung in diesem Repository wurde für Python, BlueZ und den Linux-Servicebetrieb neu strukturiert.

Veröffentlicht unter der [MIT-Lizenz](LICENSE).
