#!/usr/bin/env python3
"""
DragonScope — O4 telemetry proxy for WarDragon kits.

Provides full O4 position data (serial, GPS, pilot position, home point,
altitude, speed) so O4 drones appear in dji_receiver with the same data
as O2/O3. No --proxy flag needed.

Configuration via dragonscope.cfg (JSON):
  remote:      URL of the remote endpoint
  license_key: API key for authentication
  listen_port: port to listen on (default 80)
  listen_addr: address to bind (default 0.0.0.0)
"""
import os
import hashlib
import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen


# --- Config ---

def load_config():
    """Load config from dragonscope.cfg next to this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_paths = [
        os.path.join(script_dir, "dragonscope.cfg"),
        "/home/dragon/WarDragon/proxy/dragonscope.cfg",
        "./dragonscope.cfg",
    ]
    for path in cfg_paths:
        try:
            with open(path) as f:
                cfg = json.load(f)
                cfg["_path"] = path
                return cfg
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return {}


# --- Device fingerprint ---

def get_device_id():
    """Hardware fingerprint tied to physical board. Works on Pi (ARM) and x86_64."""
    parts = []
    # Pi CPU serial — unique per board, survives SD card clone
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("Serial"):
                    parts.append(line.split(":")[-1].strip())
                    break
    except Exception:
        pass
    # x86_64: DMI product UUID — unique per motherboard, set by manufacturer
    if not parts:
        try:
            uuid = open("/sys/class/dmi/id/product_uuid").read().strip()
            if uuid and uuid != "Not Settable":
                parts.append(uuid)
        except Exception:
            pass
    # x86_64 fallback: machine-id + board serial
    if not parts:
        try:
            parts.append(open("/etc/machine-id").read().strip())
        except Exception:
            pass
        try:
            serial = open("/sys/class/dmi/id/board_serial").read().strip()
            if serial and serial != "Not Specified":
                parts.append(serial)
        except Exception:
            pass
    # MAC address — always include as additional entropy
    try:
        for iface in sorted(os.listdir("/sys/class/net/")):
            if iface == "lo":
                continue
            addr = open(f"/sys/class/net/{iface}/address").read().strip()
            if addr and addr != "00:00:00:00:00:00":
                parts.append(addr)
                break
    except Exception:
        pass
    if not parts:
        return None
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


# --- State ---

config = {}
api_key = None
remote_url = None
device_id = None


def call_remote(hex_payload):
    """Forward to remote endpoint, return raw response."""
    url = f"{remote_url}/api/o4online/decrypt?hex={hex_payload}"
    req = Request(url)
    req.add_header("User-Agent", "DragonScope/1.0")
    if api_key:
        req.add_header("x-api-key", api_key)
    if device_id:
        req.add_header("x-device-id", device_id)
    try:
        resp = urlopen(req, timeout=15)
        return resp.read()
    except Exception as e:
        detail = ""
        if hasattr(e, 'code'):
            detail = f" (HTTP {e.code})"
        if hasattr(e, 'read'):
            try:
                detail += f" {e.read().decode()}"
            except Exception:
                pass
        print(f"  Remote error: {e}{detail}")
        return None


class ProxyHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/o4online/decrypt':
            self._handle_query(parsed)
        elif parsed.path == '/health':
            self._respond(json.dumps({
                "status": "ok",
                "licensed": api_key is not None,
            }).encode(), "application/json")
        else:
            self._respond(b'{"error": "not found"}', "application/json", 404)

    def _handle_query(self, parsed):
        params = parse_qs(parsed.query)
        hex_payload = params.get('hex', [None])[0]
        if not hex_payload:
            self._respond(b'{"sn": ""}', "application/json")
            return

        if not api_key:
            self._respond(b'{"sn": ""}', "application/json")
            return

        raw_response = call_remote(hex_payload)
        if raw_response:
            try:
                parsed_resp = json.loads(raw_response)
                serial = parsed_resp.get("sn", "")
                lat = parsed_resp.get("lat", "")
                lon = parsed_resp.get("lon", "")
                if serial and lat and lat != "0" and lat != "0.0":
                    print(f"  INFP: {serial} lat={lat} lon={lon}")
                elif serial:
                    print(f"  CRYP: {serial}")
            except Exception:
                pass

            self._respond(raw_response, "application/json")
        else:
            self._respond(b'{"sn": ""}', "application/json")

    def _respond(self, body, content_type, status=200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def log_message(self, fmt, *args):
        pass


class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


CONFIG_CHECK_INTERVAL = 30  # seconds between config reloads


def reload_config():
    """Reload config from disk. Picks up new license keys without restart."""
    global config, api_key, remote_url
    config = load_config()
    remote = config.get("remote", "").rstrip('/')
    key = config.get("license_key")

    if remote and remote != "https://CHANGE_ME":
        remote_url = remote
    else:
        remote_url = None

    if key and key != "CHANGE_ME":
        if key != api_key and api_key is not None:
            print(f"  License key updated")
        api_key = key
    else:
        api_key = None


def config_watcher():
    """Periodically check if config has been updated."""
    while True:
        time.sleep(CONFIG_CHECK_INTERVAL)
        old_key = api_key
        reload_config()
        if api_key and not old_key:
            print(f"  License activated — O4 telemetry enabled")


def main():
    global config, api_key, remote_url, device_id

    config = load_config()
    device_id = get_device_id()
    port = config.get("listen_port", 80)
    host = config.get("listen_addr", "0.0.0.0")

    reload_config()

    print(f"DragonScope")
    print(f"  Config:   {config.get('_path', 'none')}")
    print(f"  Remote:   {remote_url or 'not configured'}")
    print(f"  Listen:   {host}:{port}")
    print(f"  License:  {'loaded' if api_key else 'NONE (detection only)'}")
    print(f"  Device:   {device_id or 'unknown'}")
    if not api_key:
        print(f"  Waiting for license key in dragonscope.cfg (checking every {CONFIG_CHECK_INTERVAL}s)")
    print()

    # Background thread watches for config changes
    import threading
    t = threading.Thread(target=config_watcher, daemon=True)
    t.start()

    server = ThreadedServer((host, port), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDone.")


if __name__ == "__main__":
    main()
