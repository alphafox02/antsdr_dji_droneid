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

SSH into the AntSDR and set the TCP destination:

```bash
fw_setenv tcp_serverip <YOUR_HOST_IP>
fw_setenv tcp_serverport 52002
reboot
```

### 3. Run the Receiver

```bash
python3 dji_receiver.py -d
```

This starts in **dual mode** by default — it simultaneously:
- Listens on port `52002` for new firmware connections (TCP server)
- Connects to the AntSDR on port `41030` for legacy firmware (TCP client)

Drone data is published on **ZMQ port 4221**.

### Command-Line Options

```
-d, --debug          Enable debug output
--mode MODE          legacy, new, or dual (default: dual)
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

Requires nightly Kismet builds. Only works with legacy firmware (port 41030).

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

## Service Management (Legacy Firmware)

```bash
# Stop the drone detection daemon on the AntSDR
./service_controller.sh stop

# Start it again
./service_controller.sh start
```

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
