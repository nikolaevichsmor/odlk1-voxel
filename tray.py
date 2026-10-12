"""
System tray icon for Windows (pure ctypes, no third-party dependencies).
Supports interactive notifications, dynamic pause/resume, and Explorer taskbar restarts.
"""
import ctypes
import ctypes.wintypes as wt
import os
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
    def __init__(self, title="ODLK1 Voxel", icon_path=None, on_open=None, on_toggle_pause=None, on_exit=None):
        self.title = title
        self.icon_path = os.path.abspath(icon_path) if icon_path else None
        self.on_open = on_open
        self.on_toggle_pause = on_toggle_pause
        self.on_exit = on_exit
        self.is_paused = False
        self.hwnd = None
        self.hicon = None
        self.nid = None
        self.class_name = None
        self._proc = None
        self._ready = threading.Event()
        self._wm_taskbar_created = 0
        self.running = True

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        self._ready.wait(timeout=5.0)

    def set_paused(self, paused: bool):
        self.is_paused = paused
        tip = f"{self.title} (Paused)" if paused else f"{self.title} (Running)"
        if self.nid and self.hwnd:
            self.nid.szTip = tip[:127]
            self.nid.uFlags = NIF_TIP
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))

    def stop(self):
        self.running = False
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def _ensure_desktop(self):
        """Ensure thread is attached to the user's interactive desktop WinSta0\\Default."""
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

        tip = f"{self.title} (Paused)" if self.is_paused else f"{self.title} (Running)"
        self.nid = NOTIFYICONDATAW()
        self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.nid.hWnd = self.hwnd
        self.nid.uID = 1
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.uCallbackMessage = WM_TRAY
        self.nid.hIcon = self.hicon
        self.nid.szTip = tip[:127]

        return bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid)))

    def _show_menu(self):
        hmenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hmenu, MF_STRING, 101, "Open Visualizer")
        user32.SetMenuDefaultItem(hmenu, 101, 0)
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        pause_label = "Resume" if self.is_paused else "Pause"
        user32.AppendMenuW(hmenu, MF_STRING, 102, pause_label)
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
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
        elif cmd == 102:
            if self.on_toggle_pause:
                self.on_toggle_pause()
        elif cmd == 103:
            if self.on_exit:
                self.on_exit()
            self.stop()

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                if self.on_open:
                    self.on_open()
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

    def _run(self):
        self._ensure_desktop()
        self.class_name = f"ODLK1_Voxel_Tray_{int(time.time() * 1000)}"
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
            0, self.class_name, "ODLK1 Voxel Tray Window", 0, 0, 0, 0, 0, None, None, hinst, None
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
