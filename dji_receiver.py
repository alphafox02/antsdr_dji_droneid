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

# Hardcoded configuration
ANTSDR_IP = "172.31.100.2"
ANTSDR_PORT = 41030
ZMQ_PUB_IP = "127.0.0.1"
ZMQ_PUB_PORT = 4221  # Port to serve DJI receiver data

# Fallback/Validation constants
MAX_HORIZONTAL_SPEED = 200.0        # m/s; above this, treat as invalid
FALLBACK_SERIAL_NUMBER = "9999999999"

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

        # 1) Fallback for blank/bogus serial number
        if len(serial_number.strip()) < 5:
            logging.debug("Serial number invalid/blank, using fallback.")
            serial_number = FALLBACK_SERIAL_NUMBER

        # 2) Drone lat/lon fallback if out of valid range
        if not (-90.0 <= drone_lat <= 90.0) or not (-180.0 <= drone_lon <= 180.0):
            logging.debug(f"Drone lat/lon out of range ({drone_lat}, {drone_lon}); falling back to 0.0.")
            drone_lat = 0.0
            drone_lon = 0.0

        # 3) Pilot lat/lon fallback if out of valid range
        if not (-90.0 <= app_lat <= 90.0) or not (-180.0 <= app_lon <= 180.0):
            logging.debug(f"Pilot lat/lon out of range ({app_lat}, {app_lon}); falling back to 0.0.")
            app_lat = 0.0
            app_lon = 0.0

        # 4) Home lat/lon fallback if out of valid range
        if not (-90.0 <= home_lat <= 90.0) or not (-180.0 <= home_lon <= 180.0):
            logging.debug(f"Home lat/lon out of range ({home_lat}, {home_lon}); falling back to 0.0.")
            home_lat = 0.0
            home_lon = 0.0

        # 5) Unrealistic speed fallback
        if horizontal_speed > MAX_HORIZONTAL_SPEED:
            logging.debug(f"Horizontal speed {horizontal_speed} m/s above max; resetting to 0.0.")
            horizontal_speed = 0.0

        # Return the dictionary (with invalid fields replaced by safe defaults)
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
            "home_lon": home_lon
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

def format_as_zmq_json(parsed_data: dict) -> list:
    """
    Formats the parsed data into a ZMQ-compatible list of messages,
    e.g. [ {"Basic ID": {...}}, {"Location/Vector Message": {...}}, ... ].
    """
    if not parsed_data:
        return []  # Means parse_data_1() had a fatal parsing error

    message_list = []

    # Basic ID Message
    basic_id_message = {
        "Basic ID": {
            "id_type": "Serial Number (ANSI/CTA-2063-A)",
            "id": parsed_data.get("serial_number", "unknown"),
            "description": parsed_data.get("device_type", "DJI Drone"),
            "RSSI": parsed_data.get("rssi", None)
        }
    }
    message_list.append(basic_id_message)

    # Location/Vector Message
    location_vector_message = {
        "Location/Vector Message": {
            "latitude": parsed_data["drone_lat"],
            "longitude": parsed_data["drone_lon"],
            "geodetic_altitude": parsed_data["geodetic_altitude"],
            "height_agl": parsed_data["height_agl"],
            "speed": parsed_data["horizontal_speed"],
            "vert_speed": parsed_data["vertical_speed"]
        }
    }
    message_list.append(location_vector_message)

    # Self-ID Message
    self_id_message = {
        "Self-ID Message": {
            "text": parsed_data.get("device_type", "DJI Drone")
        }
    }
    message_list.append(self_id_message)

    # System Message (combine pilot & home locations if valid)
    has_valid_pilot = is_valid_latlon(parsed_data["app_lat"], parsed_data["app_lon"])
    has_valid_home  = is_valid_latlon(parsed_data["home_lat"], parsed_data["home_lon"])
    if has_valid_pilot or has_valid_home:
        system_msg_dict = {}

        if has_valid_pilot:
            system_msg_dict["latitude"] = parsed_data["app_lat"]
            system_msg_dict["longitude"] = parsed_data["app_lon"]

        if has_valid_home:
            system_msg_dict["home_lat"] = parsed_data["home_lat"]
            system_msg_dict["home_lon"] = parsed_data["home_lon"]

        if system_msg_dict:
            system_message = {"System Message": system_msg_dict}
            message_list.append(system_message)

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
    """
    context = zmq.Context()
    zmq_pub_socket = context.socket(zmq.XPUB)  # XPUB for efficient subscriptions
    zmq_pub_socket.bind(f"tcp://{ZMQ_PUB_IP}:{ZMQ_PUB_PORT}")
    logging.info(f"ZMQ XPUB socket bound to tcp://{ZMQ_PUB_IP}:{ZMQ_PUB_PORT}")

    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
                client_socket.connect((ANTSDR_IP, ANTSDR_PORT))
                logging.info(f"Connected to AntSDR at {ANTSDR_IP}:{ANTSDR_PORT}")

                while True:
                    frame = client_socket.recv(1024)
                    if not frame:
                        logging.warning("Connection closed by AntSDR.")
                        break

                    package_type, data = parse_frame(frame)
                    if package_type == 0x01 and data:
                        parsed_data = parse_data_1(data)
                        zmq_message_list = format_as_zmq_json(parsed_data)
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
