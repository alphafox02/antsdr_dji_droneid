# ANTSDR E200 DJI DroneID Firmware with Network Interface Integration

Welcome to the ANTSDR E200 DJI DroneID Firmware project!

This firmware is designed to enable DJI DroneID detection on the ANTSDR E200 device and seamlessly transmit the results to a network interface and port. With just a few simple steps, users can enhance their ANTSDR E200 capabilities and utilize it as a powerful tool for drone identification and network integration.

## Features:

- **DJI DroneID Detection:** Identify DJI drones within the vicinity using the ANTSDR E200.
- **Network Interface Integration:** Transmit detection results to a specified network interface and port for further analysis or monitoring.
- **Easy Installation:** Simply copy the contents of the provided zip file to the root directory of an SD card for the ANTSDR E200 device.
- **Compatibility with Kismet:** Serve as a capture device for Kismet, allowing users to capture and analyze wireless network data alongside DJI DroneID detection.

## Usage:

1. **Preparation:**
   - Configure your LAN port to a static IP of 192.168.1.2

2. **Installation:**
   - Download the provided zip file.
   - Extract the contents to the root directory of an SD card.

3. **Execution:**
   - Insert the SD card into the ANTSDR E200 device.
   - Power on the device.

4. **Usage with Kismet (Nightly Releases):**
   - Ensure Kismet is running.
   - Execute the following command:
     ```
     kismet_cap_antsdr_droneid --source antsdr-droneid:host=192.168.1.10,port=41030 --connect localhost:3501 --tcp
     ```
   - Ensure the LAN port IP address matches the configuration.

**Note:** Nightly releases of Kismet are required for integration with the ANTSDR E200 device.


## Disclaimer:

Please note that the use of this firmware may be subject to regulations and restrictions in your region. Ensure compliance with local laws and regulations regarding wireless communication and drone detection.
