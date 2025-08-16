#!/usr/bin/env python3
"""
dji_receiver.py
cemaxecuter 2025

Connects to AntSDR, receives DJI DroneID data, converts it to a ZMQ-compatible JSON format,
and publishes it via an efficient ZMQ XPUB socket.

Usage:
    python3 dji_receiver.py [--debug]

Options:
    -d, --debug  Enable debug output to console.

Default Behavior:
    - Prints only warnings and errors to the console if --debug is not specified.
    - Publishes the processed DJI DroneID data on tcp://0.0.0.0:4221 by default.
"""

import socket
import struct
import json
import logging
import zmq
import time
import argparse
import os
from typing import Optional, Tuple

# Hardcoded configuration
ANTSDR_IP = "172.31.100.2"
ANTSDR_PORT = 41030
ZMQ_PUB_IP = "127.0.0.1"
ZMQ_PUB_PORT = 4221  # Port to serve DJI receiver data

# WarDragon monitor ZMQ (provides sensor GPS via JSON).
# Override via env var WARD_MON_ZMQ if needed (e.g., "tcp://0.0.0.0:4225").
MON_ZMQ_ENDPOINT = os.getenv("WARD_MON_ZMQ", "tcp://127.0.0.1:4225")
MON_ZMQ_RECV_TIMEOUT_MS = int(os.getenv("WARD_MON_RECV_TIMEOUT_MS", "50"))

# Fallback/Validation constants
MAX_HORIZONTAL_SPEED = 200.0        # m/s; above this, treat as invalid
ALERT_ID = "drone-alert"            # standardized ID when position/serial is unknown

# Cached sensor GPS from the monitor: (lat, lon, alt) or None
_last_sensor_gps: Optional[Tuple[float, float, float]] = None


def parse_args():
    """
    Parses command-line arguments.
    Returns an object with 'debug' as a boolean.
    """
    parser = argparse.ArgumentParser(description="DJI Receiver: Publish DJI DroneID data via ZMQ.")
    parser.add_argument("-d", "--debug", action="store_true",
                        help="Enable debug messages and logging output.")
    return parser.parse_args()


def setup_logging(debug: bool):
    """
    Configures logging to console. Debug mode shows more verbose logs,
    otherwise only warnings and errors.

    Args:
        debug (bool): If True, set log level to DEBUG. Else, WARNING.
    """
    log_level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )


def iso_timestamp_now() -> str:
    """Return current UTC time as an ISO8601 string with 'Z' suffix."""
    return time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime())


def parse_frame(frame: bytes):
    """Parses the raw frame from AntSDR."""
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
    Parses data of package type 0x01, applying fallback logic for invalid fields.
    Returns a dictionary with the fields needed to build a ZMQ-compatible JSON structure.
    """
    try:
        # Decode strings
        serial_number = data[:64].decode('utf-8', errors='replace').rstrip('\x00')
        device_type   = data[64:128].decode('utf-8', errors='replace').rstrip('\x00')

        # Pilot home lat/lon
        app_lat = struct.unpack('<d', data[129:137])[0]
        app_lon = struct.unpack('<d', data[137:145])[0]

        # Drone lat/lon
        drone_lat = struct.unpack('<d', data[145:153])[0]
        drone_lon = struct.unpack('<d', data[153:161])[0]

        # Height and altitude
        height_agl        = struct.unpack('<d', data[161:169])[0]
        geodetic_altitude = struct.unpack('<d', data[169:177])[0]

        # Home lat/lon (Return-to-home position)
        home_lat = struct.unpack('<d', data[177:185])[0]
        home_lon = struct.unpack('<d', data[185:193])[0]

        # Frequency (New field)
        freq = struct.unpack('<d', data[193:201])[0]  # Frequency value

        # Speeds
        speed_e = struct.unpack('<d', data[201:209])[0]  # East
        speed_n = struct.unpack('<d', data[209:217])[0]  # North
        speed_u = struct.unpack('<d', data[217:225])[0]  # Vertical

        # RSSI
        rssi = struct.unpack('<h', data[225:227])[0]

        # Compute horizontal speed from east/north components
        horizontal_speed = (speed_e**2 + speed_n**2)**0.5

        # ------------------------------------------------
        # Fallback logic for invalid or nonsensical values
        # ------------------------------------------------

        # 1) Serial: if blank/bogus, mark as alert (explicitly convey unknown)
        if len(serial_number.strip()) < 5:
            logging.debug("Serial number invalid/blank; marking as drone-alert.")
            serial_number = ALERT_ID

        # 2) Drone lat/lon fallback if out of valid range -> keep as-is here;
        #    we'll decide later whether to use sensor GPS for placement.
        if not (-90.0 <= drone_lat <= 90.0) or not (-180.0 <= drone_lon <= 180.0):
            logging.debug(f"Drone lat/lon out of range ({drone_lat}, {drone_lon}).")

        # 3) Pilot lat/lon fallback if out of valid range -> clamp to 0 for invalid
        if not (-90.0 <= app_lat <= 90.0) or not (-180.0 <= app_lon <= 180.0):
            logging.debug(f"Pilot lat/lon out of range ({app_lat}, {app_lon}); falling back to 0.0.")
            app_lat = 0.0
            app_lon = 0.0

        # 4) Home lat/lon fallback if out of valid range -> clamp to 0 for invalid
        if not (-90.0 <= home_lat <= 90.0) or not (-180.0 <= home_lon <= 180.0):
            logging.debug(f"Home lat/lon out of range ({home_lat}, {home_lon}); falling back to 0.0.")
            home_lat = 0.0
            home_lon = 0.0

        # 5) Unrealistic speed fallback
        if horizontal_speed > MAX_HORIZONTAL_SPEED:
            logging.debug(f"Horizontal speed {horizontal_speed} m/s above max; resetting to 0.0.")
            horizontal_speed = 0.0

        # Return the dictionary (with invalid fields replaced by safe defaults where applicable)
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
            "freq": freq  # Keep frequency field
        }

    except (UnicodeDecodeError, struct.error) as e:
        logging.error(f"Error parsing data: {e}")
        # In case of outright parse failure, return empty to skip
        return {}


def is_valid_latlon(lat: float, lon: float) -> bool:
    """
    Check if latitude and longitude are within valid ranges
    AND not exactly zero. Used for deciding if we publish a "System Message."
    """
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and lat != 0.0 and lon != 0.0


def setup_monitor_sub(endpoint: str) -> Optional[zmq.Socket]:
    """
    Create a SUB socket to the WarDragon system monitor (publishes JSON with gps_data).
    Returns a connected SUB socket or None on failure.
    """
    try:
        ctx = zmq.Context.instance()
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.SUBSCRIBE, b"")  # subscribe to all
        sub.setsockopt(zmq.RCVTIMEO, MON_ZMQ_RECV_TIMEOUT_MS)
        sub.connect(endpoint)
        return sub
    except Exception as e:
        logging.debug(f"Monitor ZMQ connect failed: {e}")
        return None


def poll_monitor_for_gps(sub_sock: Optional[zmq.Socket]) -> None:
    """
    Non-blocking poll of the monitor socket. If a valid GPS arrives, update cache.
    Expects each message to be a JSON string with 'gps_data' having latitude/longitude.
    """
    global _last_sensor_gps
    if not sub_sock:
        return
    try:
        # Drain a few messages quickly to keep fresh
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
                    _last_sensor_gps = (float(lat), float(lon), float(alt) if isinstance(alt, (int, float)) else 0.0)
            except Exception:
                continue
    except zmq.Again:
        # no new data this cycle
        pass
    except Exception as e:
        logging.debug(f"Monitor ZMQ recv failed: {e}")


def format_as_zmq_json(parsed_data: dict,
                       monitor_gps: Optional[Tuple[float, float, float]] = None) -> list:
    """
    Formats the parsed data into a ZMQ-compatible list of messages.
    If drone position is invalid and monitor GPS is available, place marker at sensor GPS
    and set the Basic ID to 'drone-alert'. No extra fields are added.
    """
    if not parsed_data:
        return []

    message_list = []

    # Decide which position to use for the drone marker
    d_lat = parsed_data["drone_lat"]
    d_lon = parsed_data["drone_lon"]
    have_valid_drone_pos = is_valid_latlon(d_lat, d_lon)

    # If invalid drone position, try to use the sensor (monitor) GPS
    used_sensor = False
    if not have_valid_drone_pos and monitor_gps is not None:
        ml, mo, _ = monitor_gps
        if is_valid_latlon(ml, mo):
            d_lat, d_lon = ml, mo
            used_sensor = True

    # Basic ID Message
    basic_id_value = parsed_data.get("serial_number", "unknown")
    if used_sensor:
        # Force a clear, consistent alert label when we used sensor position
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

    # Location/Vector Message (no extra keys added)
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

    # Self-ID Message
    self_id_text = parsed_data.get("device_type", "DJI Drone")
    if used_sensor:
        # Keep text conservative to avoid parser breaks
        self_id_text += " (alert)"
    message_list.append({"Self-ID Message": {"text": self_id_text}})

    # System Message (pilot/home if valid)
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

    # Frequency Message (unchanged)
    message_list.append({"Frequency Message": {"frequency": parsed_data.get("freq", None)}})

    return message_list


def send_zmq_message(zmq_pub_socket: zmq.Socket, message_list: list):
    """
    Sends the ZMQ JSON-formatted message.
    Logs debug info if in debug mode.

    Args:
        zmq_pub_socket (zmq.Socket): The XPUB socket to publish to.
        message_list (list): The list of message dictionaries to convert to JSON.
    """
    try:
        json_message = json.dumps(message_list)
        zmq_pub_socket.send_string(json_message)
        logging.debug(f"Sent JSON via ZMQ: {json_message}")
    except Exception as e:
        logging.error(f"Failed to send JSON via ZMQ: {e}")


def tcp_client():
    """
    Connects to AntSDR via TCP, receives raw frames, parses them,
    and publishes the result as a ZMQ XPUB stream on the configured IP/Port.
    Also subscribes (non-blocking) to the WarDragon monitor ZMQ to cache sensor GPS.
    """
    context = zmq.Context()
    zmq_pub_socket = context.socket(zmq.XPUB)  # XPUB for efficient subscriptions
    zmq_pub_socket.bind(f"tcp://{ZMQ_PUB_IP}:{ZMQ_PUB_PORT}")
    logging.info(f"ZMQ XPUB socket bound to tcp://{ZMQ_PUB_IP}:{ZMQ_PUB_PORT}")

    # Connect to WarDragon monitor for GPS
    mon_sub = setup_monitor_sub(MON_ZMQ_ENDPOINT)
    if mon_sub:
        logging.info(f"Subscribed to WarDragon monitor at {MON_ZMQ_ENDPOINT}")
    else:
        logging.warning(f"Could not subscribe to WarDragon monitor at {MON_ZMQ_ENDPOINT}. Proceeding without sensor GPS.")

    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
                client_socket.connect((ANTSDR_IP, ANTSDR_PORT))
                logging.info(f"Connected to AntSDR at {ANTSDR_IP}:{ANTSDR_PORT}")

                while True:
                    # Opportunistically poll monitor for fresh GPS
                    poll_monitor_for_gps(mon_sub)

                    frame = client_socket.recv(1024)
                    if not frame:
                        logging.warning("Connection closed by AntSDR.")
                        break

                    package_type, data = parse_frame(frame)
                    if package_type == 0x01 and data:
                        parsed_data = parse_data_1(data)
                        zmq_message_list = format_as_zmq_json(
                            parsed_data,
                            monitor_gps=_last_sensor_gps
                        )
                        if zmq_message_list:
                            send_zmq_message(zmq_pub_socket, zmq_message_list)

        except (ConnectionRefusedError, socket.error) as e:
            logging.error(f"Connection error: {e}. Retrying in 5 seconds...")
            time.sleep(5)
            continue
        except Exception as e:
            logging.error(f"Unexpected error: {e}. Retrying in 5 seconds...")
            time.sleep(5)
            continue


def main():
    args = parse_args()
    setup_logging(args.debug)
    tcp_client()


if __name__ == "__main__":
    main()
