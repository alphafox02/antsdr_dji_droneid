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
    data = frame[5:5 + package_length-5]
    return package_type, data

def parse_data(data):
    try:
        serial_number = data[:64].decode('utf-8').rstrip('\x00')
        device_type = data[64:128].decode('utf-8').rstrip('\x00')
        device_type_8 = data[128]
        app_lat = struct.unpack('d', data[129:137])[0]
        app_lon = struct.unpack('d', data[137:145])[0]
        drone_lat = struct.unpack('d', data[145:153])[0]
        drone_lon = struct.unpack('d', data[153:161])[0]
        height = struct.unpack('d', data[161:169])[0]
        altitude = struct.unpack('d', data[169:177])[0]
        home_lat = struct.unpack('d', data[177:185])[0]
        home_lon = struct.unpack('d', data[185:193])[0]
        freq = struct.unpack('d', data[193:201])[0]
        speed_E = struct.unpack('d', data[201:209])[0]
        speed_N = struct.unpack('d', data[209:217])[0]
        speed_U = struct.unpack('d', data[217:225])[0]
        rssi = struct.unpack('h', data[225:233])[0]
    except UnicodeDecodeError:
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

def send_cot_payload(cot_xml_payload, tak_server_ip, tak_server_port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            udp_socket.sendto(cot_xml_payload.encode(), (tak_server_ip, int(tak_server_port)))
            logging.info(f"CoT XML Payload sent successfully to {tak_server_ip}:{tak_server_port}")
    except Exception as e:
        logging.error(f"Error sending CoT XML Payload: {e}")

def create_cot_xml_payload_point(latitude, longitude, drone_type, callsign_point, endpoint_point, phone_point, uid_point,
                                 group_name_point, group_role_point, geopointsrc_point,
                                 altsrc_point, battery_point, device_point, platform_point,
                                 os_point, version_point, speed_point, course_point, serial_number,
                                 additional_details=None):
    time_stamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.995Z')  # Timestamp in CoT format
    stale_time = (datetime.datetime.utcnow() + datetime.timedelta(seconds=75)).strftime('%Y-%m-%dT%H:%M:%S.995Z')

    # Determine the callsign based on the drone type
    if drone_type:
        callsign_point = drone_type.replace(" ", "_")  # Replace spaces with underscores
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
            cot_xml_payload += f'\n<{key}>{value}</{key}>'

    cot_xml_payload += '\n</detail>\n</event>'
    
    return cot_xml_payload

def tcp_client():
    config = load_config()
    if not config:
        logging.error("Configuration not loaded. Exiting.")
        return

    server_ip = config.get('server_ip', '192.168.1.10')
    server_port = int(config.get('server_port', 41030))
    tak_server_ip = config.get('tak_server_ip', '0.0.0.0')
    tak_server_port = config.get('tak_server_port', '6666')

    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    logging.basicConfig(filename='drone.log', level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

    try:
        client_socket.connect((server_ip, server_port))
        logging.debug(f"Connect server success {server_ip}:{server_port}")

        while True:
            frame = client_socket.recv(1024)
            if not frame:
                break
            package_type, data = parse_frame(frame)
            if package_type == 0x01:
                parsed_data = parse_data(data)
                logging.debug("*****************")
                logging.debug(f"Parsed Data: {parsed_data}")

                # Extract relevant information for CoT XML payload
                latitude = parsed_data.get('drone_lat', 0.0)
                longitude = parsed_data.get('drone_lon', 0.0)
                serial_number = parsed_data.get('serial_number', '')
                drone_type = parsed_data.get('device_type', '')

                # Additional details for the dot
                additional_details = {
                    'additional_field1': 'value1',
                    'additional_field2': 'value2'
                    # Add more fields as needed
                }

                # Send CoT XML payload to TAK server
                cot_xml_payload_point = create_cot_xml_payload_point(latitude, longitude, drone_type, "", "",
                                                                     "", f"{serial_number}-Drone", "Yellow",
                                                                     "Team Member", "GPS", "", "", "", "", "", "",
                                                                     "0.00000000", "", serial_number,
                                                                     additional_details=additional_details)

                send_cot_payload(cot_xml_payload_point, tak_server_ip, tak_server_port)

            logging.debug("*****************\n")

    except Exception as e:
        logging.debug(f"recv error: {e}")
    finally:
        client_socket.close()
        logging.debug("disconnect")

if __name__ == "__main__":
    tcp_client()
