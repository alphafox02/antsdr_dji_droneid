# ANTSDR E200 DJI DroneID Receiver

Detects DJI drones using the ANTSDR E200 SDR and publishes DroneID data over ZMQ for integration with [DroneID](https://github.com/alphafox02/DroneID), [DragonSync](https://github.com/alphafox02/DragonSync), Kismet, and TAK/CoT systems.

Supports both legacy and new AntSDR firmware, including **O4 encrypted drone detection** (DJI Mini 5, etc.).

## Supported Drones

| Protocol | Examples | Data Available |
|----------|----------|---------------|
| O2/O3 (unencrypted) | Mini 2, Mini 3 Pro, Air 2S, Mavic 3 | Serial, model, drone/pilot/home GPS, altitude, speed, RSSI |
| O4 (encrypted) | Mini 5, future models | Hash ID, frequency, RSSI |
| O4 + DragonScope | Mini 5, future models | Serial, drone/pilot/home GPS, altitude, speed, RSSI (requires [DragonScope](#dragonscope-o4-position-data)) |

## Quick Start

### 1. Network Setup

| Device | IP Address |
|--------|-----------|
| AntSDR E200 | `172.31.100.2` (WarDragon Pro default) or `192.168.1.10` (stock) |
| Host/WarDragon | `172.31.100.1` or `192.168.1.9` |

### 2. AntSDR Configuration (New Firmware)

These settings only need to be done once per AntSDR.

1. **Flip the boot switch** to **QSPI mode**
2. **Connect** the AntSDR's console/power USB port to your WarDragon
3. Open a terminal and connect to the serial console:
   ```bash
   sudo tio /dev/ttyUSB0
   ```
4. **Power cycle** the AntSDR — you should see boot messages scrolling. If you don't see any output, you may be on the wrong serial port (e.g., if a Sonoff or other USB device is also connected). Exit with `Ctrl+T` then `Q` and try:
   ```bash
   sudo tio /dev/ttyUSB1
   ```
   Then power cycle the AntSDR again.

5. Login as `root` / `analog`

6. Copy-paste all variables at once:
   ```bash
   fw_setenv ipaddr_eth 172.31.100.2
   fw_setenv tcp_serverip 172.31.100.1
   fw_setenv tcp_serverport 52002
   fw_setenv gain_mode fast_attack
   fw_setenv heart_beate_time 30
   fw_setenv api_host 172.31.100.1
   fw_setenv request_time 1
   fw_setenv auth_secret placeholder
   fw_setenv token_secret placeholder
   fw_setenv device_serial antsdr_e200
   fw_setenv device_mode auto
   reboot
   ```

7. After reboot, verify the settings saved:
   ```bash
   fw_printenv ipaddr_eth tcp_serverip tcp_serverport gain_mode heart_beate_time api_host request_time auth_secret token_secret device_serial device_mode
   ```

8. **Power off** the AntSDR, **disconnect** the console cable, **flip the switch back to SD mode**, **reconnect** the cable, and **power on**

> **Note:** The AntSDR may need the power/console cable disconnected and reconnected when switching between QSPI and SD mode in order to fully reboot.

| Variable | Value | Description |
|----------|-------|-------------|
| `ipaddr_eth` | `172.31.100.2` | AntSDR IP address |
| `tcp_serverip` | `172.31.100.1` | Your host/WarDragon IP (where `dji_receiver.py` runs) |
| `tcp_serverport` | `52002` | TCP port (must match `--listen-port`) |
| `gain_mode` | `fast_attack` | AD9361 AGC mode for drone detection |
| `heart_beate_time` | `30` | Heartbeat interval in seconds (keeps TCP connection alive) |
| `api_host` | `172.31.100.1` | WarDragon IP (where DragonScope proxy listens on port 80) |
| `request_time` | `1` | Seconds between O4 telemetry updates (default 30, lower = faster GPS) |
| `auth_secret` | `placeholder` | Required by firmware (any value) |
| `token_secret` | `placeholder` | Required by firmware (any value) |
| `device_serial` | `antsdr_e200` | Device identifier |
| `device_mode` | `auto` | Frequency mode (`auto` hops 5.8 GHz channels) |

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
--listen-port PORT   TCP listen port for new firmware (default: 52002)
--udp-port PORT      UDP listen port (default: 52002, set 0 to disable)
```

**Environment variables** (override defaults):
- `ANTSDR_IP` — AntSDR IP for legacy mode
- `ANTSDR_PORT` — AntSDR port for legacy mode
- `ANTSDR_LISTEN_PORT` — TCP listen port for new firmware
- `ANTSDR_UDP_LISTEN_PORT` — UDP listen port

### Ports used

| Port  | Protocol | Direction      | Purpose |
|-------|----------|----------------|---------|
| 41030 | TCP      | inbound (legacy) | Connect to legacy firmware AntSDR |
| 52002 | TCP      | inbound (new)    | Accept new-firmware AntSDR connections |
| 52002 | UDP      | inbound          | Receive forwarded frames (alternative transport) |
| 4221  | ZMQ TCP  | outbound (XPUB)  | Publish parsed drone data to subscribers |
| 4225  | ZMQ TCP  | inbound (SUB)    | Subscribe to WarDragon monitor for sensor GPS |

TCP and UDP can share port `52002` — the kernel keeps protocols separate.

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

Position data is not available from the receiver alone for O4 drones.

**Important:** DJI drones only broadcast DroneID when motors are spinning. Power-on alone only activates the OcuSync control link.

## DragonScope Firmware Setup

The DragonScope firmware (`build_sdimg_drone_o4_dragonscope.zip`, provided
to kit customers) installs on the AntSDR the same way as the public O4
firmware in this repo: extract the zip contents to the SD card root,
insert into the AntSDR with the boot switch in **SD mode**, and power on.

Two differences from the public firmware are visible to operators:

1. **First-boot env auto-configuration.** The DragonScope firmware
   sets all required `fw_setenv` variables on first boot, so the
   `tio` / serial-console setup steps in
   [AntSDR Configuration](#2-antsdr-configuration-new-firmware) are
   skipped. SSH access is available immediately after the first boot
   completes (default: `ssh root@172.31.100.2`, password `1`).

2. **UDP transport for detections.** Decoded drone frames are sent to
   the WarDragon over UDP at `${udp_dest_ip}:${udp_dest_port}`
   (default `172.31.100.1:52002`). `dji_receiver.py` listens on both
   TCP and UDP 52002 by default, so the same receiver works with
   either firmware — no flags or code changes.

### Default Environment Variables

| Variable          | DragonScope default | Public firmware default | Purpose |
|-------------------|--------------------:|------------------------:|---------|
| `ipaddr_eth`      | `172.31.100.2`      | _unset_ — set manually  | AntSDR IP |
| `tcp_serverip`    | `127.0.0.1`         | _unset_ — set manually  | Legacy TCP destination (unused on kit) |
| `tcp_serverport`  | `52002`             | _unset_ — set manually  | Legacy TCP port (unused on kit) |
| `udp_dest_ip`     | `172.31.100.1`      | _n/a_                   | WarDragon IP for telemetry |
| `udp_dest_port`   | `52002`             | _n/a_                   | UDP port (matches `dji_receiver`) |
| `gain_mode`       | `fast_attack`       | _unset_ — set manually  | AD9361 AGC mode |
| `heart_beate_time`| `30`                | _unset_ — set manually  | Heartbeat interval |
| `api_host`        | `172.31.100.1`      | _unset_ — set manually  | DragonScope proxy host (WarDragon) |
| `request_time`    | `1`                 | _unset_ — set manually  | O4 telemetry refresh (seconds) |
| `auth_secret`     | `placeholder`       | _unset_ — set manually  | Required-fill |
| `token_secret`    | `placeholder`       | _unset_ — set manually  | Required-fill |
| `device_serial`   | `dragonsdr`         | _unset_ — set manually  | Device identifier |
| `device_mode`     | `auto`              | _unset_ — set manually  | Frequency mode (5.8 GHz hop) |

To verify the values that were set, SSH in and run:

```bash
fw_printenv ipaddr_eth tcp_serverip tcp_serverport udp_dest_ip udp_dest_port \
            gain_mode heart_beate_time api_host request_time \
            auth_secret token_secret device_serial device_mode
```

To override any of these after first boot, use `fw_setenv`:

```bash
fw_setenv udp_dest_ip 192.168.1.50
fw_setenv ipaddr_eth 192.168.1.10
reboot
```

The first-boot init only runs once per device (marked by
`/mnt/jffs2/.dragonscope_initialized_v2`), so manual customization
persists across reboots.

## DragonScope (O4 Position Data)

DragonScope provides full O4 telemetry — serial number, drone GPS, pilot position, home point, altitude, and speed. When configured, O4 drones appear in dji_receiver with the same data as O2/O3.

**Requirements:**
- An O4-capable AntSDR firmware (provided separately — not the same as the firmware zips in this repo)
- A DragonScope license key and config file (provided separately)
- An internet connection on the WarDragon

O2/O3 drones are unaffected and continue to work fully offline. DragonScope runs as a service on your WarDragon and starts automatically. Without a license key configured, it runs in detection-only mode — O4 drones still appear as `drone-alert-{hash}` but without position data. Once a key is added, full telemetry activates within 30 seconds with no restart needed.

To obtain the DragonScope firmware and a license key, contact us.

### Setup

If you already have this repo cloned on your WarDragon:

```bash
cd /home/dragon/WarDragon/antsdr_dji_droneid
git pull
```

Place your `dragonscope.cfg` (provided separately with your license key) in the same directory:

```bash
cp /path/to/dragonscope.cfg /home/dragon/WarDragon/antsdr_dji_droneid/
```

Install both services:

```bash
sudo cp dji-receiver.service dragonscope.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart dji-receiver
sudo systemctl enable dragonscope
sudo systemctl start dragonscope
```

Verify DragonScope is running:

```bash
curl http://localhost/health
# {"status": "ok", "licensed": true}
```

Ensure `api_host` on the AntSDR points to the WarDragon IP (see [AntSDR Configuration](#2-antsdr-configuration-new-firmware) above). No `--proxy` flag is needed in dji_receiver.

### Files

| File | Description |
|------|-------------|
| `dragonscope.py` | O4 telemetry proxy (runs on WarDragon, listens on port 80) |
| `dragonscope.cfg` | Configuration (endpoint URL + license key, provided separately) |
| `dragonscope.service` | systemd unit file |

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
