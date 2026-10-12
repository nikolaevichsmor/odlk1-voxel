"""
UDP -> browser bridge.

Browsers cannot listen to UDP, so this process:
  * listens on UDP 127.0.0.1:5005 (packets from sniffer.py / mock_sender.py),
  * serves the visualizer's static files from ./web,
  * forwards the latest frame to the browser via Server-Sent Events (/stream).

Python standard library only. If no browser is connected, packets simply
overwrite the "latest frame"; the sender is never blocked.
"""
import argparse
import json
import os
import sys
import socket
import struct
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import boinc_stats

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class FrameHub:
    def __init__(self):
        self.cond = threading.Condition()
        self.frame = None
        self.seq = 0
        self.packets = 0
        self.pid = None          # PID of the ODLK process (bytes 100..103 of the sniffer packet)
        self.stats = None        # JSON string with workunit statistics
        self.stats_seq = 0
        self.counters = None     # (N, checked, generated, buffered bytes) from the sniffer packet

    def publish(self, data: bytes, pid=None):
        with self.cond:
            self.frame = data
            self.pid = pid
            self.seq += 1
            self.packets += 1
            self.cond.notify_all()

    def publish_stats(self, js: str):
        with self.cond:
            if js != self.stats:
                self.stats = js
                self.stats_seq += 1
                self.cond.notify_all()


def udp_loop(hub: FrameHub, host: str, port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    sock.bind((host, port))
    print(f"[bridge] UDP listening on {host}:{port}")
    while True:
        try:
            data, _ = sock.recvfrom(4096)
        except OSError:  # Windows: WSAECONNRESET etc. - ignore
            continue
        if len(data) >= 100:
            pid = counters = None
            if len(data) >= 136:  # pid u32, N u64, checked u64, generated u64, buffer i64
                pid, *counters = struct.unpack_from("<IQQQq", data, 100)
            elif len(data) >= 104:
                pid = int.from_bytes(data[100:104], "little")
            hub.counters = counters
            hub.publish(bytes(data[:100]), pid)


def boinc_loop(hub: FrameHub, boinccmd: str, data_dir, period: float):
    cache = boinc_stats.BoincTask(boinccmd, data_dir)
    while True:
        st = None
        pid = hub.pid
        if pid:
            try:
                task, ddir = cache.get(pid, time.monotonic())
                st = boinc_stats.collect(task, ddir, hub.counters)
            except Exception as e:  # boinccmd unavailable etc. - just no stats
                st = None
                print(f"[bridge] stats: {e}")
        hub.publish_stats(json.dumps(st, ensure_ascii=False))
        time.sleep(period)


class Handler(SimpleHTTPRequestHandler):
    hub: FrameHub = None
    # On Windows mimetypes come from the registry (.js may be text/plain -> ES modules fail to load)
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".html": "text/html",
        ".css": "text/css",
    }

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/stream"):
            return self._stream()
        return super().do_GET()

    def _stream(self):
        hub = self.hub
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        seq = -1
        sseq = -1
        try:
            while True:
                with hub.cond:
                    hub.cond.wait_for(lambda: hub.seq != seq or hub.stats_seq != sseq, timeout=1.0)
                    frame, cur = hub.frame, hub.seq
                    stats, scur = hub.stats, hub.stats_seq
                msg = ""
                if cur != seq and frame is not None:
                    msg += f"data: {frame.hex()}\n\n"
                    seq = cur
                if scur != sseq and stats is not None:
                    msg += f"event: stats\ndata: {stats}\n\n"
                    sseq = scur
                if not msg:
                    msg = ": ping\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass


def stats_loop(hub: FrameHub):
    last = 0
    while True:
        time.sleep(5)
        n = hub.packets
        print(f"[bridge] {(n - last) / 5:.1f} packets/s")
        last = n


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, ConnectionRefusedError):
            return
        if isinstance(exc_val, OSError) and getattr(exc_val, "winerror", None) in (10053, 10054):
            return
        super().handle_error(request, client_address)


def start_server(http_host="127.0.0.1", http_port=8080, udp_host="127.0.0.1", udp_port=5005, boinccmd=None, boinc_data=None, quiet=True):
    hub = FrameHub()
    Handler.hub = hub
    threading.Thread(target=udp_loop, args=(hub, udp_host, udp_port), daemon=True).start()
    threading.Thread(target=boinc_loop, daemon=True,
                     args=(hub, boinc_stats.find_boinccmd(boinccmd), boinc_data, 0.5)).start()
    if not quiet:
        threading.Thread(target=stats_loop, args=(hub,), daemon=True).start()
    httpd = QuietThreadingHTTPServer((http_host, http_port), partial(Handler, directory=WEB_DIR))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    ap = argparse.ArgumentParser(description="ODLK1 voxel bridge: UDP -> SSE + static web")
    ap.add_argument("--udp-host", default="127.0.0.1")
    ap.add_argument("--udp-port", type=int, default=5005)
    ap.add_argument("--http-host", default="127.0.0.1")
    ap.add_argument("--http-port", type=int, default=8080)
    ap.add_argument("--open", action="store_true", help="open the browser")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--boinccmd", help="path to boinccmd.exe (auto-detected by default)")
    ap.add_argument("--boinc-data", help="BOINC data directory (defaults to the one derived from the ODLK process path)")
    args = ap.parse_args()

    if sys.platform == "win32":
        import ctypes
        mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "Local\\ODLK1_Voxel_Bridge_Lock")
        if ctypes.windll.kernel32.GetLastError() == 183:
            print("[bridge] another bridge instance is already running; exiting")
            return

    httpd = start_server(
        http_host=args.http_host,
        http_port=args.http_port,
        udp_host=args.udp_host,
        udp_port=args.udp_port,
        boinccmd=args.boinccmd,
        boinc_data=args.boinc_data,
        quiet=args.quiet
    )
    url = f"http://{args.http_host}:{args.http_port}/"
    print(f"[bridge] visualizer: {url}")
    if args.open:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        httpd.shutdown()
        print("\n[bridge] stopped")


if __name__ == "__main__":
    main()

