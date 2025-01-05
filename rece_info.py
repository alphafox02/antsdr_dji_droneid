import socket
import struct
import logging

def parse_frame(frame):
    frame_header = frame[:2]
    package_type = frame[2]
    length_bytes = frame[3:5]
    #struct.unpack parse uint16_t
    package_length = struct.unpack('H', length_bytes)[0]
    print(f"package_length: {package_length}")
    data = frame[5:5 + package_length-5]
    return package_type, data

def parse_data_1(data):
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
        device_type   = "Got a dji drone with encryption"
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

def tcp_client():
    server_ip = "192.168.1.10"
    server_port = 41030

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
            if(package_type == 0x01):
                parsed_data = parse_data_1(data)
            logging.debug("*****************")
            logging.debug(f"Package Type: {package_type}")
            for key, value in parsed_data.items():
                logging.debug(f"{key}: {value}")
            logging.debug("*****************\n")

    except Exception as e:
        logging.debug(f"recv error: {e}")
    finally:
        client_socket.close()
        logging.debug("disconnect")

if __name__ == "__main__":
    tcp_client()
