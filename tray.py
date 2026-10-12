"""
System tray icon for Windows (pure ctypes + stdlib socket IPC, zero pip dependencies).
Supports interactive notifications, dynamic pause/resume, Explorer taskbar restarts,
and automatic cross-process coordination (unified single tray icon for multiple ODLK1 modules).
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import socket
import sys
import threading
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 64-bit safe Win32 types
LRESULT = ctypes.c_ssize_t
HWND = ctypes.c_void_p
UINT = ctypes.c_uint32
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)

user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.DestroyWindow.argtypes = [HWND]
user32.DestroyWindow.restype = wt.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.SetForegroundWindow.argtypes = [HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.CreatePopupMenu.restype = wt.HMENU
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.DestroyMenu.restype = wt.BOOL
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.SetMenuDefaultItem.argtypes = [wt.HMENU, wt.UINT, wt.UINT]
user32.SetMenuDefaultItem.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = wt.BOOL
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT

WM_USER = 0x0400
WM_TRAY = WM_USER + 20
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203

TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040

GENERIC_ALL = 0x10000000
DESKTOP_ALL = 0x01FF

TRAY_IPC_PORT = 5055


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", wt.BYTE * 8)
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uTimeoutOrVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wt.HICON)
    ]


shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR)
    ]


class TrayIcon:
    def __init__(self, title="ODLK1", icon_path=None, module_name="visualizer",
                 on_open=None, on_toggle_pause=None, on_toggle_discord=None, on_exit=None,
                 is_discord_active_fn=None):
        self.title = title
        self.icon_path = os.path.abspath(icon_path) if icon_path else None
        self.module_name = module_name  # "visualizer" or "discord"
        self.on_open = on_open
        self.on_toggle_pause = on_toggle_pause
        self.on_toggle_discord = on_toggle_discord
        self.on_exit = on_exit
        self.is_discord_active_fn = is_discord_active_fn

        self.is_paused = False
        self.is_discord_paused = False
        self.hwnd = None
        self.hicon = None
        self.nid = None
        self.class_name = None
        self._proc = None
        self._ready = threading.Event()
        self._wm_taskbar_created = 0
        self.running = True

        # IPC state
        self.is_host = False
        self.host_mutex = None
        self.host_socket = None
        self.client_socket = None
        self.remote_modules = {}  # { "module_name": { "conn": socket, "paused": bool } }
        self._ipc_lock = threading.Lock()

    def start(self):
        # Attempt to become Host on IPC port 5055
        self._try_bind_host()
        if self.is_host:
            # We are the Tray Host: start the tray icon window
            t = threading.Thread(target=self._run_host_window, daemon=True)
            t.start()
            self._ready.wait(timeout=5.0)
            # Start IPC server thread to accept secondary modules
            threading.Thread(target=self._ipc_server_loop, daemon=True).start()
        else:
            # Another module is already Host: connect as client to its tray
            self._ready.set()
            threading.Thread(target=self._ipc_client_loop, daemon=True).start()

    def _try_bind_host(self):
        if sys.platform == "win32":
            # Named Mutex: guaranteed OS-level single instance for Tray Host
            self.host_mutex = kernel32.CreateMutexW(None, True, "Local\\ODLK1_Unified_Tray_Host_Mutex")
            if kernel32.GetLastError() != 0:
                if self.host_mutex:
                    kernel32.CloseHandle(self.host_mutex)
                    self.host_mutex = None
                self.is_host = False
                return

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", TRAY_IPC_PORT))
            s.listen(5)
            self.host_socket = s
            self.is_host = True
        except OSError:
            self.is_host = False
            if self.host_mutex and sys.platform == "win32":
                kernel32.CloseHandle(self.host_mutex)
                self.host_mutex = None

    def _ipc_server_loop(self):
        """Host listens for incoming secondary module connections."""
        while self.running and self.host_socket:
            try:
                conn, _ = self.host_socket.accept()
                threading.Thread(target=self._handle_client_connection, args=(conn,), daemon=True).start()
            except Exception:
                break

    def _handle_client_connection(self, conn):
        mod_name = None
        f = conn.makefile("r", encoding="utf-8")
        try:
            for line in f:
                if not line.strip():
                    continue
                msg = json.loads(line)
                mtype = msg.get("type")
                if mtype == "register":
                    mod_name = msg.get("module")
                    with self._ipc_lock:
                        self.remote_modules[mod_name] = {
                            "conn": conn,
                            "paused": bool(msg.get("paused", False))
                        }
                    self._update_tray_tip()
                elif mtype == "state":
                    if mod_name:
                        with self._ipc_lock:
                            if mod_name in self.remote_modules:
                                self.remote_modules[mod_name]["paused"] = bool(msg.get("paused", False))
                        self._update_tray_tip()
        except Exception:
            pass
        finally:
            if mod_name:
                with self._ipc_lock:
                    self.remote_modules.pop(mod_name, None)
                self._update_tray_tip()
            try:
                conn.close()
            except Exception:
                pass

    def _ipc_client_loop(self):
        """Client connects to Host and waits for commands; promotes if Host exits."""
        while self.running:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(("127.0.0.1", TRAY_IPC_PORT))
                self.client_socket = s
                # Register ourselves
                reg = {
                    "type": "register",
                    "module": self.module_name,
                    "paused": self.is_discord_paused if self.module_name == "discord" else self.is_paused
                }
                s.sendall(json.dumps(reg).encode("utf-8") + b"\n")
                f = s.makefile("r", encoding="utf-8")
                for line in f:
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    cmd = msg.get("cmd")
                    if cmd == "open" and self.on_open:
                        self.on_open()
                    elif cmd in ("toggle_pause", "pause", "resume") and self.on_toggle_pause:
                        new_p = msg.get("paused") if "paused" in msg else not self.is_paused
                        self.is_paused = new_p
                        self.on_toggle_pause(new_p)
                        self.set_paused(new_p)
                    elif cmd in ("toggle_discord", "pause", "resume") and self.on_toggle_discord:
                        new_p = msg.get("paused") if "paused" in msg else not self.is_discord_paused
                        self.is_discord_paused = new_p
                        self.on_toggle_discord(new_p)
                        self.set_discord_paused(new_p)
                    elif cmd == "exit":
                        if self.on_exit:
                            self.on_exit()
                        self.running = False
                        break
            except Exception:
                pass

            if not self.running:
                break

            # Host disconnected or failed! Try to promote ourselves to Host
            time.sleep(0.5)
            self._try_bind_host()
            if self.is_host:
                # Promotion successful: start tray icon
                t = threading.Thread(target=self._run_host_window, daemon=True)
                t.start()
                threading.Thread(target=self._ipc_server_loop, daemon=True).start()
                break

    def _send_remote_command(self, mod_name, payload):
        with self._ipc_lock:
            info = self.remote_modules.get(mod_name)
            if info and info.get("conn"):
                try:
                    data = payload if isinstance(payload, dict) else {"cmd": payload}
                    info["conn"].sendall(json.dumps(data).encode("utf-8") + b"\n")
                except Exception:
                    pass

    def set_paused(self, paused: bool):
        self.is_paused = paused
        if self.is_host:
            self._update_tray_tip()
        elif self.client_socket:
            try:
                self.client_socket.sendall(json.dumps({"type": "state", "paused": paused}).encode("utf-8") + b"\n")
            except Exception:
                pass

    def set_discord_paused(self, paused: bool):
        self.is_discord_paused = paused
        if self.is_host:
            self._update_tray_tip()
        elif self.client_socket:
            try:
                self.client_socket.sendall(json.dumps({"type": "state", "paused": paused}).encode("utf-8") + b"\n")
            except Exception:
                pass

    def _update_tray_tip(self):
        if not self.nid or not self.hwnd:
            return
        parts = ["ODLK1"]
        if self.on_open or "visualizer" in self.remote_modules:
            v_paused = self.is_paused if self.on_open else self.remote_modules["visualizer"].get("paused", False)
            parts.append("3D [P]" if v_paused else "3D [R]")
        if self.on_toggle_discord or "discord" in self.remote_modules:
            d_paused = self.is_discord_paused if self.on_toggle_discord else self.remote_modules["discord"].get("paused", False)
            parts.append("DS [P]" if d_paused else "DS [R]")
        tip = " - ".join(parts)
        self.nid.szTip = tip[:127]
        self.nid.uFlags = NIF_TIP
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))

    def stop(self):
        self.running = False
        if self.host_mutex and sys.platform == "win32":
            try:
                kernel32.ReleaseMutex(self.host_mutex)
                kernel32.CloseHandle(self.host_mutex)
            except Exception:
                pass
            self.host_mutex = None
        if self.host_socket:
            try:
                self.host_socket.close()
            except Exception:
                pass
        if self.client_socket:
            try:
                self.client_socket.close()
            except Exception:
                pass
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def _ensure_desktop(self):
        try:
            hwinsta = user32.OpenWindowStationW("WinSta0", False, GENERIC_ALL)
            if hwinsta:
                user32.SetProcessWindowStation(hwinsta)
            hdesk = user32.OpenDesktopW("Default", 0, False, DESKTOP_ALL)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

    def _load_icon(self):
        if self.icon_path and os.path.isfile(self.icon_path):
            hicon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if hicon:
                return hicon
        return user32.LoadIconW(None, ctypes.c_void_p(32512))

    def _add_notify_icon(self):
        if not self.hwnd:
            return False
        if not self.hicon:
            self.hicon = self._load_icon()

        self.nid = NOTIFYICONDATAW()
        self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.nid.hWnd = self.hwnd
        self.nid.uID = 1
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.uCallbackMessage = WM_TRAY
        self.nid.hIcon = self.hicon
        self.nid.szTip = "ODLK1 (Running)"

        res = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid)))
        self._update_tray_tip()
        return res

    def _show_menu(self):
        hmenu = user32.CreatePopupMenu()

        has_visualizer = bool(self.on_open) or ("visualizer" in self.remote_modules)
        has_discord = bool(self.on_toggle_discord) or ("discord" in self.remote_modules)

        # Visualizer Section
        if has_visualizer:
            user32.AppendMenuW(hmenu, MF_STRING, 101, "Open Visualizer")
            user32.SetMenuDefaultItem(hmenu, 101, 0)
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            
            v_paused = self.is_paused if self.on_open else self.remote_modules["visualizer"].get("paused", False)
            pause_label = "Resume Visualizer" if v_paused else "Pause Visualizer"
            user32.AppendMenuW(hmenu, MF_STRING, 102, pause_label)

        # Discord Section
        if has_discord:
            if has_visualizer:
                user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            d_paused = self.is_discord_paused if self.on_toggle_discord else self.remote_modules["discord"].get("paused", False)
            ds_label = "Discord Presence: Resume" if d_paused else "Discord Presence: Pause"
            user32.AppendMenuW(hmenu, MF_STRING, 104, ds_label)

        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)

        if has_visualizer and has_discord:
            user32.AppendMenuW(hmenu, MF_STRING, 110, "Exit Visualizer")
            user32.AppendMenuW(hmenu, MF_STRING, 111, "Exit Discord")
            user32.AppendMenuW(hmenu, MF_STRING, 112, "Exit All")
        else:
            user32.AppendMenuW(hmenu, MF_STRING, 103, "Exit")

        pos = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pos))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pos.x, pos.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(hmenu)

        if cmd == 101:
            if self.on_open:
                self.on_open()
            else:
                self._send_remote_command("visualizer", "open")
        elif cmd == 102:
            if self.on_toggle_pause:
                self.is_paused = not self.is_paused
                self.on_toggle_pause(self.is_paused)
                self.set_paused(self.is_paused)
            elif "visualizer" in self.remote_modules:
                cur = self.remote_modules["visualizer"].get("paused", False)
                new_state = not cur
                self.remote_modules["visualizer"]["paused"] = new_state
                self._update_tray_tip()
                self._send_remote_command("visualizer", {"cmd": "toggle_pause", "paused": new_state})
        elif cmd == 104:
            if self.on_toggle_discord:
                self.is_discord_paused = not self.is_discord_paused
                self.on_toggle_discord(self.is_discord_paused)
                self.set_discord_paused(self.is_discord_paused)
            elif "discord" in self.remote_modules:
                cur = self.remote_modules["discord"].get("paused", False)
                new_state = not cur
                self.remote_modules["discord"]["paused"] = new_state
                self._update_tray_tip()
                self._send_remote_command("discord", {"cmd": "toggle_discord", "paused": new_state})
        elif cmd == 110:  # Exit Visualizer
            if self.on_open:
                if self.on_exit:
                    self.on_exit()
                self.stop()
            else:
                self._send_remote_command("visualizer", "exit")
        elif cmd == 111:  # Exit Discord
            if self.on_toggle_discord:
                if self.on_exit:
                    self.on_exit()
                self.stop()
            else:
                self._send_remote_command("discord", "exit")
        elif cmd in (103, 112):  # Exit / Exit All
            with self._ipc_lock:
                for mod in list(self.remote_modules.keys()):
                    self._send_remote_command(mod, "exit")
            if self.on_exit:
                self.on_exit()
            self.stop()

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                if self.on_open:
                    self.on_open()
                elif "visualizer" in self.remote_modules:
                    self._send_remote_command("visualizer", "open")
                else:
                    self._show_menu()
            elif lparam == WM_RBUTTONUP:
                self._show_menu()
            return 0
        elif msg == self._wm_taskbar_created and self._wm_taskbar_created != 0:
            self._add_notify_icon()
            return 0
        elif msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        elif msg == WM_DESTROY:
            if self.nid:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run_host_window(self):
        self._ensure_desktop()
        self.class_name = f"ODLK1_Tray_Master_{int(time.time() * 1000)}"
        self._proc = WNDPROC(self._wnd_proc)

        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.lpszClassName = self.class_name
        wc.hInstance = hinst

        if not user32.RegisterClassW(ctypes.byref(wc)):
            self._ready.set()
            return

        self.hwnd = user32.CreateWindowExW(
            0, self.class_name, "ODLK1 Unified Tray Window", 0, 0, 0, 0, 0, None, None, hinst, None
        )
        if not self.hwnd:
            self._ready.set()
            return

        self._wm_taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._add_notify_icon()
        self._ready.set()

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self.class_name:
            user32.UnregisterClassW(self.class_name, hinst)
