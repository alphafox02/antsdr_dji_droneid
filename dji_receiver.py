#!/usr/bin/env python3
"""
dji_receiver.py
cemaxecuter 2025

Connects to AntSDR (legacy firmware) and/or accepts connections from AntSDR
(new firmware), receives DJI DroneID data, converts it to a ZMQ-compatible
JSON format, and publishes it via an efficient ZMQ XPUB socket.

Supports two firmware modes simultaneously:
- Legacy: TCP client connects to AntSDR port 41030 (binary frames)
- New firmware: TCP server accepts AntSDR connections on configurable port (text CSV)

Usage:
    python3 dji_receiver.py [--debug] [--mode legacy|new|dual]

Options:
    -d, --debug          Enable debug output to console.
    --mode MODE          Connection mode: legacy, new, or dual (default: new)
    --antsdr-ip IP       AntSDR IP for legacy mode (default: 192.168.1.10)
    --antsdr-port PORT   AntSDR port for legacy mode (default: 41030)
    --listen-port PORT   Listen port for new firmware mode (default: 52002)

Default Behavior:
    - Runs in dual mode: legacy TCP client + new firmware TCP server
    - Publishes the processed DJI DroneID data on tcp://0.0.0.0:4221 by default.
"""

import socket
import struct
import json
import logging
import math
import zmq
import time
import argparse
import os
import re
import threading
import queue
from typing import Optional, Tuple

# Configuration (overridable via env vars or command-line args)
ANTSDR_IP = os.getenv("ANTSDR_IP", "172.31.100.2")
ANTSDR_PORT = int(os.getenv("ANTSDR_PORT", "41030"))
LISTEN_IP = "0.0.0.0"
LISTEN_PORT = int(os.getenv("ANTSDR_LISTEN_PORT", "52002"))
ZMQ_PUB_IP = "127.0.0.1"
ZMQ_PUB_PORT = 4221  # Port to serve DJI receiver data

# WarDragon monitor ZMQ (provides sensor GPS via JSON).
# Override via env var WARD_MON_ZMQ if needed (e.g., "tcp://0.0.0.0:4225").
MON_ZMQ_ENDPOINT = os.getenv("WARD_MON_ZMQ", "tcp://127.0.0.1:4225")
MON_ZMQ_RECV_TIMEOUT_MS = int(os.getenv("WARD_MON_RECV_TIMEOUT_MS", "50"))

# Fallback/Validation constants
MAX_HORIZONTAL_SPEED = 200.0        # m/s; above this, treat as invalid
ALERT_ID = "drone-alert"            # standardized ID when position/serial is unknown
PROXY_URL = None  # Set by --proxy flag
MAX_DISTANCE_FROM_SENSOR_KM = 50.0  # km; if drone is further than this from sensor, likely garbage

# Cached sensor GPS from the monitor: (lat, lon, alt) or None
_last_sensor_gps: Optional[Tuple[float, float, float]] = None
_gps_lock = threading.Lock()


def parse_args():
    parser = argparse.ArgumentParser(description="DJI Receiver: Publish DJI DroneID data via ZMQ.")
    parser.add_argument("-d", "--debug", action="store_true",
                        help="Enable debug messages and logging output.")
    parser.add_argument("--mode", choices=["legacy", "new", "dual"], default="new",
                        help="Connection mode (default: new)")
    parser.add_argument("--antsdr-ip", default=None,
                        help=f"AntSDR IP for legacy mode (default: {ANTSDR_IP})")
    parser.add_argument("--antsdr-port", type=int, default=None,
                        help=f"AntSDR port for legacy mode (default: {ANTSDR_PORT})")
    parser.add_argument("--listen-port", type=int, default=None,
                        help=f"Listen port for new firmware mode (default: {LISTEN_PORT})")
    parser.add_argument("--proxy", nargs='?', const="http://172.31.100.1",
                        default=None, metavar="URL",
                        help="Enable proxy lookups (default URL: http://172.31.100.1)")
    return parser.parse_args()


def setup_logging(debug: bool):
    log_level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )


def iso_timestamp_now() -> str:
    """Return current UTC time as an ISO8601 string with 'Z' suffix."""
    return time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime())


# ---------------------------------------------------------------------------
# Legacy binary frame parser (old firmware on port 41030)
# ---------------------------------------------------------------------------

def parse_frame(frame: bytes):
    """Parses the raw binary frame from legacy AntSDR firmware."""
    try:
        package_type = frame[2]
        package_length = struct.unpack('<H', frame[3:5])[0]
        data = frame[5:5 + package_length - 5]
        return package_type, data
    except struct.error:
        logging.error("Failed to parse frame.")
        return None, None


def parse_data_1(data: bytes) -> dict:
    """
    Parses data of package type 0x01 from legacy firmware binary format.
    Returns a dictionary with the fields needed to build a ZMQ-compatible JSON structure.
    """
    try:
        serial_number = data[:64].decode('utf-8', errors='replace').rstrip('\x00')
        device_type   = data[64:128].decode('utf-8', errors='replace').rstrip('\x00')

        app_lat = struct.unpack('<d', data[129:137])[0]
        app_lon = struct.unpack('<d', data[137:145])[0]

        drone_lat = struct.unpack('<d', data[145:153])[0]
        drone_lon = struct.unpack('<d', data[153:161])[0]

        height_agl        = struct.unpack('<d', data[161:169])[0]
        geodetic_altitude = struct.unpack('<d', data[169:177])[0]

        home_lat = struct.unpack('<d', data[177:185])[0]
        home_lon = struct.unpack('<d', data[185:193])[0]

        freq = struct.unpack('<d', data[193:201])[0]

        speed_e = struct.unpack('<d', data[201:209])[0]
        speed_n = struct.unpack('<d', data[209:217])[0]
        speed_u = struct.unpack('<d', data[217:225])[0]

        rssi = struct.unpack('<h', data[225:227])[0]

        horizontal_speed = (speed_e**2 + speed_n**2)**0.5

        if len(serial_number.strip()) < 5:
            logging.debug("Serial number invalid/blank; marking as drone-alert.")
            serial_number = ALERT_ID

        if not (-90.0 <= drone_lat <= 90.0) or not (-180.0 <= drone_lon <= 180.0):
            logging.debug(f"Drone lat/lon out of range ({drone_lat}, {drone_lon}).")

        if not (-90.0 <= app_lat <= 90.0) or not (-180.0 <= app_lon <= 180.0):
            logging.debug(f"Pilot lat/lon out of range ({app_lat}, {app_lon}); falling back to 0.0.")
            app_lat = 0.0
            app_lon = 0.0

        if not (-90.0 <= home_lat <= 90.0) or not (-180.0 <= home_lon <= 180.0):
            logging.debug(f"Home lat/lon out of range ({home_lat}, {home_lon}); falling back to 0.0.")
            home_lat = 0.0
            home_lon = 0.0

        if horizontal_speed > MAX_HORIZONTAL_SPEED:
            logging.debug(f"Horizontal speed {horizontal_speed} m/s above max; resetting to 0.0.")
            horizontal_speed = 0.0

        return {
            "serial_number": serial_number,
            "device_type": device_type,
            "app_lat": app_lat,
            "app_lon": app_lon,
            "drone_lat": drone_lat,
            "drone_lon": drone_lon,
            "height_agl": height_agl,
            "geodetic_altitude": geodetic_altitude,
            "horizontal_speed": horizontal_speed,
            "vertical_speed": speed_u,
            "rssi": rssi,
            "home_lat": home_lat,
            "home_lon": home_lon,
            "freq": freq
        }

    except (UnicodeDecodeError, struct.error) as e:
        logging.error(f"Error parsing legacy data: {e}")
        return {}


# ---------------------------------------------------------------------------
# New firmware text line parser (dji_O,... CSV format)
# ---------------------------------------------------------------------------

def parse_new_fw_line(line: str) -> dict:
    """
    Parse a dji_O,... text line from the new firmware (drone_dji_rid_decode).
    Returns same dict structure as parse_data_1() for unified downstream handling.
    """
    line = line.strip()
    if not line.startswith('dji_O,'):
        return {}

    # Strip trailing semicolons and trailing comma before semicolon
    line = line.rstrip(';').rstrip(',').rstrip(';')

    parts = line.split(',')
    if len(parts) < 14:
        logging.debug(f"New FW line too few fields ({len(parts)}): {line[:80]}")
        return {}

    try:
        protocol = parts[1]
        freq = float(parts[2])
        rssi = int(parts[3])
        field4 = parts[4]
        field5 = parts[5]

        drone_lon = float(parts[6])
        drone_lat = float(parts[7])
        pilot_lon = float(parts[8])
        pilot_lat = float(parts[9])
        home_lon = float(parts[10])
        home_lat = float(parts[11])

        height_parts = parts[12].split('|')
        geodetic_altitude = float(height_parts[0]) * 10.0
        height_agl = float(height_parts[1]) if len(height_parts) > 1 else 0.0

        speed_parts = parts[13].split('|')
        speed_e = float(speed_parts[0])
        speed_n = float(speed_parts[1]) if len(speed_parts) > 1 else 0.0
        speed_u = float(speed_parts[2]) if len(speed_parts) > 2 else 0.0
        horizontal_speed = ((speed_e ** 2 + speed_n ** 2) ** 0.5) / 100.0
        vertical_speed = speed_u / 100.0

    except (ValueError, IndexError) as e:
        logging.debug(f"New FW parse error: {e} in line: {line[:80]}")
        return {}

    # Parse field4 which always has parenthesized data:
    #   O2/O3: "DJI Mini 2(63)" — model name + type code
    #   O4:    "dji(15529374)"   — prefix + encrypted hash
    f4_match = re.match(r'^(.+?)\((.+)\)$', field4)
    if f4_match:
        f4_name = f4_match.group(1)
        f4_inner = f4_match.group(2)
    else:
        f4_name = field4
        f4_inner = ""

    if protocol == "4":
        # O4 encrypted drone: try proxy for decrypted serial, fallback to hash
        serial_number = f"drone-alert-{f4_inner}" if f4_inner else ALERT_ID
        device_type = "DJI Encrypted (O4)"
        if PROXY_URL and f4_inner:
            try:
                import urllib.request
                resp = urllib.request.urlopen(
                    f"{PROXY_URL}/serials/{f4_inner}", timeout=1)
                data = json.loads(resp.read())
                if data.get("serial"):
                    serial_number = data["serial"]
                    device_type = "DJI O4 (Decrypted)"
                    logging.info(f"O4 decrypted: {serial_number} (drone={f4_inner})")
            except Exception:
                pass  # No proxy or no serial — use drone-alert fallback
    else:
        # O2/O3 decoded drone: field5 is the serial, field4 name is the model
        serial_number = field5 if len(field5.strip()) >= 5 else ALERT_ID
        device_type = f4_name if f4_name else "DJI Drone"

    # Apply same validation as legacy parser
    if not (-90.0 <= drone_lat <= 90.0) or not (-180.0 <= drone_lon <= 180.0):
        logging.debug(f"Drone lat/lon out of range ({drone_lat}, {drone_lon}).")

    if not (-90.0 <= pilot_lat <= 90.0) or not (-180.0 <= pilot_lon <= 180.0):
        pilot_lat = 0.0
        pilot_lon = 0.0

    if not (-90.0 <= home_lat <= 90.0) or not (-180.0 <= home_lon <= 180.0):
        home_lat = 0.0
        home_lon = 0.0

    if horizontal_speed > MAX_HORIZONTAL_SPEED:
        horizontal_speed = 0.0

    return {
        "serial_number": serial_number,
        "device_type": device_type,
        "app_lat": pilot_lat,
        "app_lon": pilot_lon,
        "drone_lat": drone_lat,
        "drone_lon": drone_lon,
        "height_agl": height_agl,
        "geodetic_altitude": geodetic_altitude,
        "horizontal_speed": horizontal_speed,
        "vertical_speed": vertical_speed,
        "rssi": rssi,
        "home_lat": home_lat,
        "home_lon": home_lon,
        "freq": freq
    }


# ---------------------------------------------------------------------------
# Common helpers (unchanged)
# ---------------------------------------------------------------------------

def is_valid_latlon(lat: float, lon: float) -> bool:
    """Check if latitude and longitude are within valid ranges AND not exactly zero."""
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and lat != 0.0 and lon != 0.0


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def setup_monitor_sub(endpoint: str) -> Optional[zmq.Socket]:
    """Create a SUB socket to the WarDragon system monitor (publishes JSON with gps_data)."""
    try:
        ctx = zmq.Context.instance()
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.SUBSCRIBE, b"")
        sub.setsockopt(zmq.RCVTIMEO, MON_ZMQ_RECV_TIMEOUT_MS)
        sub.connect(endpoint)
        return sub
    except Exception as e:
        logging.debug(f"Monitor ZMQ connect failed: {e}")
        return None


def poll_monitor_for_gps(sub_sock: Optional[zmq.Socket]) -> None:
    """Non-blocking poll of the monitor socket. If a valid GPS arrives, update cache."""
    global _last_sensor_gps
    if not sub_sock:
        return
    try:
        for _ in range(5):
            msg = sub_sock.recv_string(flags=zmq.NOBLOCK)
            try:
                obj = json.loads(msg)
                gpsd = obj.get("gps_data", {})
                lat = gpsd.get("latitude")
                lon = gpsd.get("longitude")
                alt = gpsd.get("altitude")
                if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) \
                   and -90.0 <= float(lat) <= 90.0 and -180.0 <= float(lon) <= 180.0:
                    with _gps_lock:
                        _last_sensor_gps = (float(lat), float(lon), float(alt) if isinstance(alt, (int, float)) else 0.0)
            except Exception:
                continue
    except zmq.Again:
        pass
    except Exception as e:
        logging.debug(f"Monitor ZMQ recv failed: {e}")


def get_sensor_gps() -> Optional[Tuple[float, float, float]]:
    """Thread-safe read of cached sensor GPS."""
    with _gps_lock:
        return _last_sensor_gps


def format_as_zmq_json(parsed_data: dict,
                       monitor_gps: Optional[Tuple[float, float, float]] = None) -> list:
    """
    Formats the parsed data into a ZMQ-compatible list of messages.
    Works identically for legacy binary and new firmware text input.
    """
    if not parsed_data:
        return []

    message_list = []

    d_lat = parsed_data["drone_lat"]
    d_lon = parsed_data["drone_lon"]
    have_valid_drone_pos = is_valid_latlon(d_lat, d_lon)

    used_sensor = False
    use_sensor_fallback = False

    if monitor_gps is not None:
        ml, mo, _ = monitor_gps
        sensor_valid = is_valid_latlon(ml, mo)

        if not have_valid_drone_pos:
            use_sensor_fallback = True
            logging.debug(f"Drone position invalid ({d_lat}, {d_lon}), will use sensor fallback")
        elif sensor_valid:
            distance_km = haversine_distance_km(d_lat, d_lon, ml, mo)
            if distance_km > MAX_DISTANCE_FROM_SENSOR_KM:
                use_sensor_fallback = True
                logging.debug(f"Drone position ({d_lat}, {d_lon}) is {distance_km:.1f}km from sensor - likely garbage, using sensor fallback")

        if use_sensor_fallback and sensor_valid:
            d_lat, d_lon = ml, mo
            used_sensor = True
        elif use_sensor_fallback and not sensor_valid:
            logging.warning(f"Sensor GPS invalid ({ml}, {mo}) - cannot use as fallback. Check WarDragon GPS on port 4225.")

    # Basic ID Message — preserve drone-alert-{hash} for O4 encrypted drones
    basic_id_value = parsed_data.get("serial_number", "unknown")
    if used_sensor and not basic_id_value.startswith("drone-alert-"):
        # Only override to generic alert if it's not already an O4 hash ID
        basic_id_value = ALERT_ID

    basic_id_message = {
        "Basic ID": {
            "id_type": "Serial Number (ANSI/CTA-2063-A)",
            "id": basic_id_value,
            "description": parsed_data.get("device_type", "DJI Drone"),
            "RSSI": parsed_data.get("rssi", None)
        }
    }
    message_list.append(basic_id_message)

    location_vector_message = {
        "Location/Vector Message": {
            "latitude": d_lat,
            "longitude": d_lon,
            "geodetic_altitude": parsed_data["geodetic_altitude"],
            "height_agl": parsed_data["height_agl"],
            "speed": parsed_data["horizontal_speed"],
            "vert_speed": parsed_data["vertical_speed"]
        }
    }
    message_list.append(location_vector_message)

    self_id_text = parsed_data.get("device_type", "DJI Drone")
    if used_sensor:
        self_id_text += " (alert)"
    message_list.append({"Self-ID Message": {"text": self_id_text}})

    has_valid_pilot = is_valid_latlon(parsed_data["app_lat"], parsed_data["app_lon"])
    has_valid_home  = is_valid_latlon(parsed_data["home_lat"], parsed_data["home_lon"])
    if has_valid_pilot or has_valid_home:
        sysmsg = {}
        if has_valid_pilot:
            sysmsg["latitude"] = parsed_data["app_lat"]
            sysmsg["longitude"] = parsed_data["app_lon"]
        if has_valid_home:
            sysmsg["home_lat"] = parsed_data["home_lat"]
            sysmsg["home_lon"] = parsed_data["home_lon"]
        if sysmsg:
            message_list.append({"System Message": sysmsg})

    message_list.append({"Frequency Message": {"frequency": parsed_data.get("freq", None)}})

    return message_list


def send_zmq_message(zmq_pub_socket: zmq.Socket, message_list: list):
    try:
        json_message = json.dumps(message_list)
        zmq_pub_socket.send_string(json_message)
        logging.debug(f"Sent JSON via ZMQ: {json_message}")
    except Exception as e:
        logging.error(f"Failed to send JSON via ZMQ: {e}")


# ---------------------------------------------------------------------------
# Legacy TCP client thread (old firmware, connects TO AntSDR on port 41030)
# ---------------------------------------------------------------------------

def legacy_tcp_client(data_queue: queue.Queue, antsdr_ip: str, antsdr_port: int):
    """
    Connects to AntSDR via TCP (legacy firmware), receives binary frames,
    parses them, and puts parsed dicts into the shared queue.
    """
    logging.info(f"[Legacy] Starting TCP client to {antsdr_ip}:{antsdr_port}")

    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
                client_socket.settimeout(10)
                client_socket.connect((antsdr_ip, antsdr_port))
                client_socket.settimeout(60)  # detect dead connections
                logging.info(f"[Legacy] Connected to AntSDR at {antsdr_ip}:{antsdr_port}")

                while True:
                    frame = client_socket.recv(1024)
                    if not frame:
                        logging.warning("[Legacy] Connection closed by AntSDR.")
                        break

                    package_type, data = parse_frame(frame)
                    if package_type == 0x01 and data:
                        parsed_data = parse_data_1(data)
                        if parsed_data:
                            data_queue.put(parsed_data)

        except socket.timeout:
            logging.warning("[Legacy] Connection timed out (no data for 60s). Reconnecting...")
        except (ConnectionRefusedError, socket.error, OSError) as e:
            logging.debug(f"[Legacy] Connection error: {e}. Retrying in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            logging.error(f"[Legacy] Unexpected error: {e}. Retrying in 5 seconds...")
            time.sleep(5)


# ---------------------------------------------------------------------------
# New firmware TCP server thread (accepts connections from AntSDR)
# ---------------------------------------------------------------------------

def new_fw_connection_handler(conn: socket.socket, addr, data_queue: queue.Queue):
    """Handle a single connection from a new-firmware AntSDR."""
    logging.info(f"[NewFW] Connection from {addr}")
    conn.settimeout(90)  # heartbeat is every 30s; 90s covers 3 missed beats
    buf = ""

    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                logging.warning(f"[NewFW] Connection closed by {addr}")
                break

            buf += chunk.decode('utf-8', errors='replace')

            # Process complete lines
            while '\n' in buf:
                line, buf = buf.split('\n', 1)
                line = line.strip()
                if not line or line == '=':
                    continue

                # Only parse dji_O lines, skip debug output (ppm, decode success, etc.)
                if line.startswith('dji_O,'):
                    parsed_data = parse_new_fw_line(line)
                    if parsed_data:
                        data_queue.put(parsed_data)
                        logging.debug(f"[NewFW] Parsed: {parsed_data.get('serial_number')} "
                                      f"freq={parsed_data.get('freq')} rssi={parsed_data.get('rssi')}")

    except socket.timeout:
        logging.warning(f"[NewFW] Connection from {addr} timed out (no data for 90s)")
    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        logging.debug(f"[NewFW] Connection error from {addr}: {e}")
    except Exception as e:
        logging.error(f"[NewFW] Unexpected error from {addr}: {e}")
    finally:
        conn.close()
        logging.info(f"[NewFW] Disconnected: {addr}")


def new_fw_tcp_server(data_queue: queue.Queue, listen_port: int):
    """
    TCP server that accepts connections from new-firmware AntSDR daemons.
    Each connection is handled in its own thread to support multiple AntSDRs.
    """
    logging.info(f"[NewFW] Starting TCP server on {LISTEN_IP}:{listen_port}")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_IP, listen_port))
    srv.listen(5)
    logging.info(f"[NewFW] Listening on {LISTEN_IP}:{listen_port}")

    while True:
        try:
            conn, addr = srv.accept()
            t = threading.Thread(target=new_fw_connection_handler,
                                 args=(conn, addr, data_queue),
                                 daemon=True)
            t.start()
        except Exception as e:
            logging.error(f"[NewFW] Accept error: {e}")
            time.sleep(1)


# ---------------------------------------------------------------------------
# Main publisher loop
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    setup_logging(args.debug)

    # Apply CLI overrides
    global PROXY_URL
    antsdr_ip = args.antsdr_ip or ANTSDR_IP
    antsdr_port = args.antsdr_port or ANTSDR_PORT
    listen_port = args.listen_port or LISTEN_PORT
    PROXY_URL = args.proxy
    if PROXY_URL:
        logging.info(f"Proxy enabled: {PROXY_URL}")

    # ZMQ publisher (main thread only)
    context = zmq.Context()
    zmq_pub_socket = context.socket(zmq.XPUB)
    zmq_pub_socket.bind(f"tcp://{ZMQ_PUB_IP}:{ZMQ_PUB_PORT}")
    logging.info(f"ZMQ XPUB socket bound to tcp://{ZMQ_PUB_IP}:{ZMQ_PUB_PORT}")

    # Monitor subscription for sensor GPS
    mon_sub = setup_monitor_sub(MON_ZMQ_ENDPOINT)
    if mon_sub:
        logging.info(f"Subscribed to WarDragon monitor at {MON_ZMQ_ENDPOINT}")
    else:
        logging.warning(f"Could not subscribe to WarDragon monitor at {MON_ZMQ_ENDPOINT}. Proceeding without sensor GPS.")

    # Shared queue: both TCP handlers put parsed dicts here
    data_queue = queue.Queue()

    # Start connection threads based on mode
    if args.mode in ("legacy", "dual"):
        t_legacy = threading.Thread(target=legacy_tcp_client,
                                    args=(data_queue, antsdr_ip, antsdr_port),
                                    daemon=True)
        t_legacy.start()
        logging.info(f"[Legacy] Thread started -> {antsdr_ip}:{antsdr_port}")

    if args.mode in ("new", "dual"):
        t_new = threading.Thread(target=new_fw_tcp_server,
                                 args=(data_queue, listen_port),
                                 daemon=True)
        t_new.start()
        logging.info(f"[NewFW] Thread started <- listening on {listen_port}")

    # Main loop: consume queue, poll GPS, publish ZMQ
    logging.info(f"Running in '{args.mode}' mode. Waiting for drone data...")

    while True:
        try:
            # Poll monitor for fresh sensor GPS
            poll_monitor_for_gps(mon_sub)

            # Block up to 100ms for next parsed drone data
            try:
                parsed_data = data_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            # Format and publish
            zmq_message_list = format_as_zmq_json(
                parsed_data,
                monitor_gps=get_sensor_gps()
            )
            if zmq_message_list:
                send_zmq_message(zmq_pub_socket, zmq_message_list)

        except KeyboardInterrupt:
            logging.info("Shutting down.")
            break
        except Exception as e:
            logging.error(f"Publisher error: {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
