"""
ODLK1 Voxel main application with system tray and background worker.
Runs silently with no console windows.
"""
import argparse
import os
import sys
import threading
import time
import webbrowser

# In pythonw on Windows, stdout and stderr are None; redirect to avoid crashes
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

# On Windows, immediately hide any console window
if sys.platform == "win32":
    import ctypes
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)

import bridge
import mock_sender
import sniffer
from tray import TrayIcon


def main():
    ap = argparse.ArgumentParser(description="ODLK1 Voxel Runner")
    ap.add_argument("--demo", action="store_true", help="run with mock generator instead of real ODLK")
    ap.add_argument("--no-open", action="store_true", help="do not open browser at startup")
    ap.add_argument("--http-port", type=int, default=8080)
    ap.add_argument("--udp-port", type=int, default=5005)
    args = ap.parse_args()

    # Single-instance check
    mutex = None
    if sys.platform == "win32":
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ctypes.set_last_error(0)
        mutex = k32.CreateMutexW(None, True, "Local\\ODLK1_Voxel_App_Singleton")
        if ctypes.get_last_error() == 183:
            webbrowser.open(f"http://127.0.0.1:{args.http_port}/")
            return

    # Start HTTP & SSE server (bridge)
    httpd = bridge.start_server(http_port=args.http_port, udp_port=args.udp_port, quiet=True)

    stop_event = threading.Event()
    pause_event = threading.Event()

    def worker():
        if args.demo:
            class MockArgs:
                host = "127.0.0.1"
                port = args.udp_port
                hz = 60.0
                steps = 200
                hold = 1.5
                restart_steps = 500_000
                seed = None
            mock_sender.stream_loop(MockArgs(), stop_event=stop_event, pause_event=pause_event)
        else:
            class SnifferArgs:
                process = "odlk*"
                pid = None
                hz = 60.0
                host = "127.0.0.1"
                port = args.udp_port
                address = None
                offset = None
                module = None
                elem_size = 1
                elem_size_set = False
                base = 0
                base_set = False
                auto = False
                print_every = 0
            sniffer.stream_loop(SnifferArgs(), stop_event=stop_event, pause_event=pause_event)

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

    visualizer_url = f"http://127.0.0.1:{args.http_port}/"

    def on_open():
        webbrowser.open(visualizer_url)

    def on_toggle_pause(paused=None):
        if paused is None:
            should_pause = not pause_event.is_set()
        else:
            should_pause = bool(paused)

        if should_pause:
            pause_event.set()
            tray.set_paused(True)
        else:
            pause_event.clear()
            tray.set_paused(False)

    def on_exit():
        stop_event.set()
        try:
            httpd.shutdown()
        except Exception:
            pass

    base_dir = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(base_dir, "docs", "icon.ico")

    tray = TrayIcon(
        title="ODLK1 Voxel",
        icon_path=icon_path,
        module_name="visualizer",
        on_open=on_open,
        on_toggle_pause=on_toggle_pause,
        on_exit=on_exit
    )
    tray.start()

    if not args.no_open:
        webbrowser.open(visualizer_url)

    try:
        while tray.running:
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        on_exit()
        tray.stop()
        if mutex and sys.platform == "win32":
            try:
                ctypes.windll.kernel32.ReleaseMutex(mutex)
                ctypes.windll.kernel32.CloseHandle(mutex)
            except Exception:
                pass


if __name__ == "__main__":
    main()
