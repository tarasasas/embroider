"""Phone sync server: lets the iPhone app send an image here and get a finished .pes back.

Run it with run_server.bat (or `python server.py`). It uses the same engine as the desktop app,
so the stitches are identical. The phone and this PC must be on the same Wi-Fi network.

The phone has to send the pairing code shown when the server starts. Nothing is sent to the internet.
"""

import base64
import hmac
import io
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageOps

import digitizer as dz

PORT = int(os.environ.get("EMBROIDER_PORT", "8793"))
MAX_BODY = 40 * 1024 * 1024
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".embroider_server.json")
convert_lock = threading.Lock()  # one conversion at a time; they are CPU heavy


def load_token():
    """The pairing code is created once and kept, so the phone stays paired."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            token = json.load(f).get("token")
            if token:
                return token
    except (OSError, ValueError):
        pass
    token = secrets.token_hex(4)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"token": token}, f)
    return token


def local_addresses():
    """IPv4 addresses of this PC, the one the phone should use first (the PC's main Wi-Fi/LAN address)."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    main = None
    try:  # the address used to reach the internet = the home network (no packets are actually sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            main = s.getsockname()[0]
            found.add(main)
    except OSError:
        pass
    rest = sorted(a for a in found if not a.startswith("127.") and a != main)
    return ([main] if main else []) + rest


def _num(request, key, default, lo, hi):
    try:
        return min(max(float(request.get(key, default)), lo), hi)
    except (TypeError, ValueError):
        return default


def build_settings(request):
    """Turns the phone's request into digitizer Settings. Anything missing gets a good default."""
    s = dz.Settings()
    fabric = request.get("fabric", "T-shirt / knit")
    values, _tip = dz.FABRICS.get(fabric, dz.FABRICS["Woven cotton"])
    for key, value in values.items():
        setattr(s, key, value)
    s.width_mm = _num(request, "width_mm", 90, 5, 400)
    s.height_mm = _num(request, "height_mm", 90, 5, 400)
    s.colors = int(_num(request, "colors", 0, 0, 15))  # 0 = automatic
    s.cleanup = int(_num(request, "cleanup", s.cleanup, 0, 3))
    s.remove_background = bool(request.get("remove_background", True))
    s.brother_colors = bool(request.get("brother_colors", True))
    for key, allowed in (("fill_type", dz.FILL_TYPES), ("edge", dz.LINE_TYPES), ("outline", dz.LINE_TYPES)):
        if request.get(key) in allowed:
            setattr(s, key, request[key])
    return s


def convert_request(request):
    """Runs the full conversion and returns the JSON-ready reply."""
    raw = base64.b64decode(request["image"])
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGBA")
    result = dz.convert(img, build_settings(request))
    pattern = result.pattern

    for index, hexcolor in (request.get("recolor") or {}).items():  # colors the user changed on the phone
        i = int(index)
        if 0 <= i < len(pattern.threadlist):
            rgb = tuple(int(hexcolor.lstrip("#")[k : k + 2], 16) for k in (0, 2, 4))
            thread = pattern.threadlist[i]
            thread.set_color(*rgb)
            thread.description = dz.nearest_brother_thread(rgb)[1]

    with tempfile.TemporaryDirectory() as tmp:
        path = dz.save(pattern, os.path.join(tmp, "design.pes"))
        with open(path, "rb") as f:
            pes = f.read()
    preview = io.BytesIO()
    dz.render_preview(pattern, max_px=900).save(preview, "PNG")

    info = result.info
    threads = []
    for thread, details in zip(pattern.threadlist, info["threads"]):
        threads.append({"r": thread.get_red(), "g": thread.get_green(), "b": thread.get_blue(),
                        "name": thread.description or "", "stitches": details["stitches"]})
    return {
        "pes": base64.b64encode(pes).decode(),
        "preview": base64.b64encode(preview.getvalue()).decode(),
        "width_mm": info["width_mm"], "height_mm": info["height_mm"],
        "stitches": info["stitches"], "minutes": info["minutes"], "threads": threads,
    }


class Handler(BaseHTTPRequestHandler):
    token = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        given = self.headers.get("X-Token", "")
        if hmac.compare_digest(given.encode(), self.token.encode()):
            return True
        self._send(401, {"error": "Wrong pairing code."})
        return False

    def do_GET(self):
        if self.path != "/ping":
            self._send(404, {"error": "Not found."})
        elif self._authorized():
            self._send(200, {"ok": True, "name": socket.gethostname(), "fabrics": list(dz.FABRICS)})

    def do_POST(self):
        if self.path != "/convert":
            self._send(404, {"error": "Not found."})
            return
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > MAX_BODY:
            self.rfile.read(max(0, min(length, MAX_BODY)))
            self._send(413, {"error": "Image is too large."})
            return
        body = self.rfile.read(length)
        if not self._authorized():
            return
        try:
            request = json.loads(body)
            with convert_lock:
                reply = convert_request(request)
        except ValueError as exc:  # the digitizer's own messages, e.g. "Nothing to stitch"
            self._send(422, {"error": str(exc)})
            return
        except OSError:  # PIL could not read the image (UnidentifiedImageError is an OSError)
            self._send(422, {"error": "The PC could not read that image."})
            return
        except Exception as exc:
            print("Conversion failed:", repr(exc))
            self._send(500, {"error": f"The PC could not convert that image ({type(exc).__name__})."})
            return
        self._send(200, reply)


class ExclusiveServer(ThreadingHTTPServer):
    """Only one copy of the server may own the port. Windows otherwise lets a second copy bind
    the same port silently, and an old copy keeps answering the phone with old code."""

    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    Handler.token = load_token()
    if "--info" in sys.argv:  # just show the address and pairing code, e.g. when it runs in the background
        addresses = local_addresses()
        if addresses:
            print(f"PC address for the phone:  {addresses[0]}:{PORT}")
            for other in addresses[1:]:
                print(f"  (other network, usually not this one: {other}:{PORT})")
        print(f"Pairing code:              {Handler.token}")
        return
    try:
        server = ExclusiveServer(("0.0.0.0", PORT), Handler)
    except OSError:
        print(f"Port {PORT} is already in use. Is the server already running?")
        sys.exit(1)
    print("Embroidery phone server")
    print("-----------------------")
    addresses = local_addresses()
    if addresses:
        print(f"  PC address for the phone:  {addresses[0]}:{PORT}")
        for other in addresses[1:]:
            print(f"    (other network, usually not this one: {other}:{PORT})")
    print(f"  Pairing code:              {Handler.token}")
    print("\nKeep this window open (or run install_autostart.bat so it starts with Windows).")
    print("The first time, allow Python through Windows Firewall on private networks.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
