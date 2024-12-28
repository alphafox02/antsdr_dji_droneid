import argparse
import socket
import struct
import logging
import xml.etree.ElementTree as ET
import datetime

# Load configuration from a separate config file
def load_config():
    config = {}
    try:
        with open('config.txt', 'r') as file:
            for line in file:
                key, value = line.strip().split('=')
                config[key.strip()] = value.strip()
    except Exception as e:
        logging.error(f"Error loading config: {e}")
    return config

def parse_frame(frame):
    frame_header = frame[:2]
    package_type = frame[2]
    length_bytes = frame[3:5]
    package_length = struct.unpack('H', length_bytes)[0]
    data = frame[5:5 + package_length - 5]
    return package_type, data

def parse_data(data):
    try:
        serial_number = data[:64].decode('utf-8').rstrip('\x00')
        device_type   = data[64:128].decode('utf-8').rstrip('\x00')
        device_type_8 = data[128]
        app_lat       = struct.unpack('d', data[129:137])[0]
        app_lon       = struct.unpack('d', data[137:145])[0]
        drone_lat     = struct.unpack('d', data[145:153])[0]
        drone_lon     = struct.unpack('d', data[153:161])[0]
        height        = struct.unpack('d', data[161:169])[0]
        altitude      = struct.unpack('d', data[169:177])[0]
        home_lat      = struct.unpack('d', data[177:185])[0]
        home_lon      = struct.unpack('d', data[185:193])[0]
        freq          = struct.unpack('d', data[193:201])[0]
        speed_E       = struct.unpack('d', data[201:209])[0]
        speed_N       = struct.unpack('d', data[209:217])[0]
        speed_U       = struct.unpack('d', data[217:225])[0]
        rssi          = struct.unpack('h', data[225:227])[0]  # fix indexing
    except UnicodeDecodeError:
        # If we fail to decode, it may indicate encrypted or partial data
        device_type   = "Got a DJI drone with encryption"
        device_type_8 = 255

    return {
        'serial_number': serial_number,
        'device_type': device_type,
        'device_type_8': device_type_8,
        'app_lat': app_lat,
        'app_lon': app_lon,
        'drone_lat': drone_lat,
        'drone_lon': drone_lon,
        'height': height,
        'altitude': altitude,
        'home_lat': home_lat,
        'home_lon': home_lon,
        'freq': freq,
        'speed_E': speed_E,
        'speed_N': speed_N,
        'speed_U': speed_U,
        'RSSI': rssi
    }

def create_cot_xml_payload_point(latitude, longitude, drone_type, callsign_point, endpoint_point, phone_point,
                                 uid_point, group_name_point, group_role_point, geopointsrc_point,
                                 altsrc_point, battery_point, device_point, platform_point,
                                 os_point, version_point, speed_point, course_point, serial_number,
                                 additional_details=None):
    time_stamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.995Z')  
    stale_time = (datetime.datetime.utcnow() + datetime.timedelta(seconds=75)).strftime('%Y-%m-%dT%H:%M:%S.995Z')

    # Determine the callsign based on the drone type (or use a fallback)
    if drone_type:
        callsign_point = drone_type.replace(" ", "_")
    else:
        callsign_point = "Drone"

    # Constructing the XML payload
    cot_xml_payload = f'''<?xml version="1.0"?>
<event version="2.0" uid="{uid_point}" type="a-f-G-U-C" time="{time_stamp}" start="{time_stamp}" stale="{stale_time}" how="m-g">
    <point lat="{latitude}" lon="{longitude}" hae="999999" ce="35.0" le="999999" />
    <detail>
        <contact endpoint="{endpoint_point}" phone="{phone_point}" callsign="{callsign_point}" />
        <uid Droid="{serial_number}" />
        <__group name="{group_name_point}" role="{group_role_point}" />
        <precisionlocation geopointsrc="{geopointsrc_point}" altsrc="{altsrc_point}" />
        <status battery="{battery_point}" />
        <takv device="{device_point}" platform="{platform_point}" os="{os_point}" version="{version_point}" />
        <track speed="{speed_point}" course="{course_point}" />
        <color argb="-256"/>
        <usericon iconsetpath="-256"/>'''

    # Include additional details if provided
    if additional_details:
        for key, value in additional_details.items():
            cot_xml_payload += f'\n        <{key}>{value}</{key}>'

    cot_xml_payload += '\n    </detail>\n</event>'
    return cot_xml_payload

def send_cot_payload(cot_xml_payload, tak_server_ip, tak_server_port,
                     is_multicast=False, multicast_ip="239.2.3.1", multicast_port=6969):
    """
    Send the CoT XML payload either to a unicast TAK server or
    to a multicast group, depending on the flags.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            if is_multicast:
                # Configure the socket for multicast
                ttl = 1
                udp_socket.setsockopt(socket.IPPROTO_IP,
                                      socket.IP_MULTICAST_TTL,
                                      struct.pack('b', ttl))

                # Send to the specified multicast group
                udp_socket.sendto(cot_xml_payload.encode(), (multicast_ip, multicast_port))
                logging.info(f"CoT XML Payload sent via multicast to {multicast_ip}:{multicast_port}")
            else:
                # Unicast to the TAK server
                udp_socket.sendto(cot_xml_payload.encode(), (tak_server_ip, int(tak_server_port)))
                logging.info(f"CoT XML Payload sent to {tak_server_ip}:{tak_server_port}")
    except Exception as e:
        logging.error(f"Error sending CoT XML Payload: {e}")

def parse_args():
    parser = argparse.ArgumentParser(description="Drone -> ATAK converter.")
    parser.add_argument('--multicast', action='store_true',
                        help="Enable multicast mode (sends data via multicast).")
    parser.add_argument('--multicast_ip', default='239.2.3.1',
                        help="Multicast IP address (default: 239.2.3.1).")
    parser.add_argument('--multicast_port', type=int, default=6969,
                        help="Multicast port (default: 6969).")
    parser.add_argument('--no-tcp', action='store_true',
                        help="Disable TCP server connection.")
    return parser.parse_args()

def tcp_client(args):
    """
    Connect to the server via TCP and receive frames, then parse and forward
    CoT payloads. If args.no_tcp is True, we skip connecting.
    """
    config = load_config()
    logging.basicConfig(filename='drone.log', level=logging.DEBUG,
                        format='%(asctime)s - %(levelname)s - %(message)s')

    # If --no-tcp was given, just log and return
    if args.no_tcp:
        logging.info("TCP connection disabled by command-line flag.")
        return

    # Load defaults, or use config if available
    server_ip = config.get('server_ip', '192.168.1.10')
    server_port = int(config.get('server_port', 41030))
    
    # Even if we’re in multicast mode, we might still connect to TCP.  
    # The CoT (either unicast or multicast) is handled in `send_cot_payload`.
    tak_server_ip = config.get('tak_server_ip', '0.0.0.0')
    tak_server_port = config.get('tak_server_port', '6666')

    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        client_socket.connect((server_ip, server_port))
        logging.debug(f"Connected to server {server_ip}:{server_port}")

        while True:
            frame = client_socket.recv(1024)
            if not frame:
                break

            package_type, data = parse_frame(frame)
            if package_type == 0x01:
                parsed_data = parse_data(data)
                logging.debug("*****************")
                logging.debug(f"Parsed Data: {parsed_data}")

                # Extract relevant info for CoT
                latitude  = parsed_data.get('drone_lat', 0.0)
                longitude = parsed_data.get('drone_lon', 0.0)
                serial_number = parsed_data.get('serial_number', '')
                drone_type    = parsed_data.get('device_type', '')

                # Additional details for the CoT event
                additional_details = {
                    'additional_field1': 'value1',
                    'additional_field2': 'value2'
                }

                # Create CoT XML payload
                cot_xml_payload_point = create_cot_xml_payload_point(
                    latitude, longitude, drone_type,
                    callsign_point="", endpoint_point="", phone_point="",
                    uid_point=f"{serial_number}-Drone",
                    group_name_point="Yellow",
                    group_role_point="Team Member",
                    geopointsrc_point="GPS", 
                    altsrc_point="", 
                    battery_point="", 
                    device_point="", 
                    platform_point="", 
                    os_point="", 
                    version_point="", 
                    speed_point="0.00000000", 
                    course_point="", 
                    serial_number=serial_number,
                    additional_details=additional_details
                )

                # Now decide how to send (unicast or multicast)
                send_cot_payload(
                    cot_xml_payload_point,
                    tak_server_ip,
                    tak_server_port,
                    is_multicast=args.multicast,
                    multicast_ip=args.multicast_ip,
                    multicast_port=args.multicast_port
                )
            logging.debug("*****************\n")

    except Exception as e:
        logging.debug(f"recv error: {e}")
    finally:
        client_socket.close()
        logging.debug("Disconnected from server.")

def main():
    args = parse_args()
    # If we only want to *send* CoT (multicast or otherwise) without TCP,
    # we skip tcp_client. For demonstration, we'll always call tcp_client,
    # and inside it we check if no-tcp is set.
    tcp_client(args)

    # If you want a script that can *also* run in a pure multicast loop
    # *without* reading from the TCP socket, you could place that logic here.

if __name__ == "__main__":
    main()
