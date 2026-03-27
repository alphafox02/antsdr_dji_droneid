# ANTSDR E200 DJI DroneID Receiver

Detects DJI drones using the ANTSDR E200 SDR and publishes DroneID data over ZMQ for integration with [DroneID](https://github.com/alphafox02/DroneID), [DragonSync](https://github.com/alphafox02/DragonSync), Kismet, and TAK/CoT systems.

Supports both legacy and new AntSDR firmware, including **O4 encrypted drone detection** (DJI Mini 5, etc.).

## Supported Drones

| Protocol | Examples | Data Available |
|----------|----------|---------------|
| O2/O3 (unencrypted) | Mini 2, Mini 3 Pro, Air 2S, Mavic 3 | Serial, model, drone/pilot/home GPS, altitude, speed, RSSI |
| O4 (encrypted) | Mini 5, future models | Hash ID, frequency, RSSI (position encrypted) |

## Quick Start

### 1. Network Setup

| Device | IP Address |
|--------|-----------|
| AntSDR E200 | `172.31.100.2` (WarDragon Pro default) or `192.168.1.10` (stock) |
| Host/WarDragon | `172.31.100.1` or `192.168.1.9` |

### 2. AntSDR Configuration (New Firmware)

For a new AntSDR, flip the boot switch to **QSPI mode**, power on, and connect via serial console:

```bash
sudo tio /dev/ttyUSB0
```

Login as `root`/`analog`. Serial console via `tio` is the easiest way to access a new AntSDR before the network is configured — no IP address or SSH needed, just a USB cable.

Then copy-paste:

```bash
fw_setenv ipaddr_eth 172.31.100.2
fw_setenv tcp_serverip 172.31.100.1
fw_setenv tcp_serverport 52002
fw_setenv gain_mode fast_attack
fw_setenv heart_beate_time 30
fw_setenv api_host 172.31.100.1
fw_setenv auth_secret placeholder
reboot
```

Verify the settings were saved:

```bash
fw_printenv ipaddr_eth tcp_serverip tcp_serverport gain_mode heart_beate_time api_host auth_secret
```

Then power off, flip back to **SD mode**, and power on.

| Variable | Value | Description |
|----------|-------|-------------|
| `ipaddr_eth` | `172.31.100.2` | AntSDR IP address |
| `tcp_serverip` | `172.31.100.1` | Your host/WarDragon IP (where `dji_receiver.py` runs) |
| `tcp_serverport` | `52002` | TCP port (must match `--listen-port`) |
| `gain_mode` | `fast_attack` | AD9361 AGC mode for drone detection |
| `heart_beate_time` | `30` | Heartbeat interval in seconds (keeps TCP connection alive) |
| `api_host` | `172.31.100.1` | Required for O4 detection (must be non-empty) |
| `auth_secret` | `placeholder` | Required for O4 detection (must be non-empty) |

Once booted with the new firmware (SD mode), SSH access is `root`/`1`.

**Note:** The firmware defaults to `192.168.1.10` if `ipaddr_eth` is not set. All other variables above have no defaults and must be configured per device.

### 3. Run the Receiver

```bash
python3 dji_receiver.py -d
```

This listens on port `52002` for new firmware connections and publishes drone data on **ZMQ port 4221**.

Use `--mode legacy` to connect to old firmware on port 41030, or `--mode dual` for both.

### Command-Line Options

```
-d, --debug          Enable debug output
--mode MODE          legacy, new, or dual (default: new)
--antsdr-ip IP       AntSDR IP for legacy mode (default: 172.31.100.2)
--antsdr-port PORT   AntSDR port for legacy mode (default: 41030)
--listen-port PORT   Listen port for new firmware (default: 52002)
```

**Environment variables** (override defaults):
- `ANTSDR_IP` — AntSDR IP for legacy mode
- `ANTSDR_PORT` — AntSDR port for legacy mode
- `ANTSDR_LISTEN_PORT` — Listen port for new firmware

## Integration

### Pipeline

```
AntSDR E200 → dji_receiver.py (:4221 ZMQ) → zmq_decoder.py (:4224 ZMQ) → DragonSync → CoT/TAK/MQTT
```

1. **dji_receiver.py** — Receives raw data from AntSDR, publishes JSON on ZMQ port 4221
2. **[zmq_decoder.py](https://github.com/alphafox02/DroneID)** — Listens on port 4221, provides decoded results on port 4224:
   ```bash
   python3 zmq_decoder.py --dji 127.0.0.1:4221
   ```
3. **[DragonSync](https://github.com/alphafox02/DragonSync)** — Converts to CoT for TAK servers, MQTT, etc.

### Kismet Integration

```bash
kismet_cap_antsdr_droneid --source antsdr-droneid:host=<ANTSDR_IP>,port=41030 --connect localhost:3501 --tcp
```

Requires nightly Kismet builds. **Only works with legacy firmware** — the Kismet capture source (`kismet_cap_antsdr_droneid`) expects the legacy binary frame protocol on port 41030. The new firmware (`drone_dji_rid_decode`) uses a different text CSV protocol over a reversed TCP connection, which the Kismet capture source does not currently support.

## Firmware Versions

### New Firmware (`drone_dji_rid_decode`)
- AntSDR connects OUT to your host as a TCP client
- Text CSV output with full O2/O3 decode and O4 encrypted detection
- Set destination with `fw_setenv tcp_serverip` / `tcp_serverport`

### Legacy Firmware (`done_dji_release`)
- AntSDR listens as a TCP server on port 41030
- Binary frame output, FFT-based detection + OFDM decode
- SD card installation: extract firmware zip to SD root

## O4 Encrypted Drones

O4 drones (Mini 5, etc.) broadcast encrypted DroneID. The receiver can detect them and provide:
- **Hash ID** — unique per session (e.g., `drone-alert-9dc89f97`)
- **Frequency** — detection frequency with hopping pattern
- **RSSI** — signal strength for proximity estimation

Position data is encrypted and not available without a decryption API.

**Important:** DJI drones only broadcast DroneID when motors are spinning. Power-on alone only activates the OcuSync control link.

## Systemd Service (Host)

A systemd service file for `dji_receiver.py` is included in this repo. To install it on your host (e.g. WarDragon kit):

```bash
sudo cp dji-receiver.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable dji-receiver
sudo systemctl start dji-receiver
```

Check status and logs:

```bash
sudo systemctl status dji-receiver
journalctl -u dji-receiver -f
```

## AntSDR Service Management

```bash
# Stop the drone detection daemon on the AntSDR
./service_controller.sh stop

# Start it again
./service_controller.sh start
```

The script auto-detects old vs new firmware and stops/starts the correct processes. On both firmware versions, the init chain is `S55drone` → `droneangle.sh` → daemon binary. The watchdog in `droneangle.sh` respawns the daemon every second, so all three processes must be killed for a clean stop.

## Changing the AntSDR E200 IP Address

1. **Power off** the AntSDR E200.
2. **Flip the switch** to **QSPI mode**.
3. **Power on** and SSH into the **current IP**.
4. Set a new IP:
   ```bash
   fw_setenv ipaddr_eth NEW_IP_ADDRESS
   ```
5. **Power off**, flip back to **SD mode**, and power on.

When changing the AntSDR IP, also update your host interface static IP, the `--antsdr-ip` flag or `ANTSDR_IP` env var, and any Kismet references.

## Requirements

- Python 3.7+
- `pyzmq`

## Disclaimer

The use of this firmware and software may be subject to regulations in your region. Ensure compliance with local laws regarding wireless communication and drone detection.
