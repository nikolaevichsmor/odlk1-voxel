"""
BOINC/ODLK task stats for a given ODLK process PID.

Sources:
  * boinccmd --get_tasks          - WU name, progress, elapsed time, CPU time, remaining;
  * slots/N/output.txt -> result file in projects/...  - found squares
    (the file is created empty at start and appended to on every find);
  * slots/N/input.txt  - first line "<k> <N>", N = job size (for the "checked" estimate).
"""
import os
import re
import subprocess
import sys

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k32.OpenProcess.restype = wt.HANDLE
    _k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    _k32.CloseHandle.argtypes = [wt.HANDLE]

    def process_path(pid):
        h = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wt.DWORD(len(buf))
            if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return buf.value
        finally:
            _k32.CloseHandle(h)
        return None
else:
    def process_path(pid):
        try:
            return os.readlink(f"/proc/{pid}/exe")
        except OSError:
            return None

BOINCCMD_CANDIDATES = [
    r"C:\Program Files\BOINC\boinccmd.exe",
    r"C:\Other\BOINC\boinccmd.exe",
    "/usr/bin/boinccmd",
]


def data_dir_from_pid(pid):
    """...\\data.BOINC\\projects\\<proj>\\odlk.exe -> ...\\data.BOINC"""
    p = process_path(pid)
    if not p:
        return None
    parts = os.path.normpath(p).split(os.sep)
    low = [x.lower() for x in parts]
    if "projects" in low:
        return os.sep.join(parts[:low.index("projects")])
    return None


def find_boinccmd(explicit=None):
    if explicit:
        return explicit
    for c in BOINCCMD_CANDIDATES:
        if os.path.isfile(c):
            return c
    return "boinccmd"


def get_tasks(boinccmd, data_dir):
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    out = subprocess.run([boinccmd, "--get_tasks"], cwd=data_dir, capture_output=True,
                         text=True, timeout=10, creationflags=flags).stdout
    tasks, cur = [], None
    for line in out.splitlines():
        if re.match(r"^\d+\) -+", line):
            cur = {}
            tasks.append(cur)
        elif cur is not None and ":" in line:
            k, v = line.split(":", 1)
            cur[k.strip()] = v.strip()
    return tasks


ODLK_SQUARE_BYTES = 212  # 10 rows of "d d d d d d d d d d\r\n" + "\r\n"


def result_path(slot_dir):
    """slots/N/output.txt is a <soft_link> to the real result file in projects/..."""
    p = os.path.join(slot_dir, "output.txt")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            m = re.search(r"<soft_link>(.*?)</soft_link>", f.read(512))
        return os.path.normpath(os.path.join(slot_dir, m.group(1))) if m else p
    except OSError:
        return None


def _f(d, k):
    try:
        return float(d[k])
    except (KeyError, ValueError):
        return None


class BoincTask:
    """boinccmd cache: queried at most once per `period` seconds."""

    def __init__(self, boinccmd, data_dir=None, period=2.0):
        self.boinccmd, self.data_dir, self.period = boinccmd, data_dir, period
        self.pid = None
        self.at = 0.0
        self.task = None
        self.dir = None

    def get(self, pid, now):
        if pid != self.pid or now - self.at >= self.period:
            self.pid, self.at = pid, now
            self.dir = self.data_dir or data_dir_from_pid(pid)
            self.task = None
            if self.dir:
                self.task = next((t for t in get_tasks(self.boinccmd, self.dir)
                                  if t.get("PID") == str(pid)), None)
        return self.task, self.dir


def collect(task, data_dir, counters):
    """task - block from boinccmd; counters - (N, checked, generated, bytes_in_buffer) from the sniffer."""
    st = {"wu": None, "progress": None, "elapsed": None, "cpu": None, "remaining": None,
          "found": None, "checked": None, "total": None}
    if task:
        st.update(wu=task.get("WU name") or task.get("name"),
                  progress=_f(task, "fraction done"),
                  elapsed=_f(task, "elapsed task time"),
                  cpu=_f(task, "current CPU time"),
                  remaining=_f(task, "estimated CPU time remaining"))
    if counters and counters[0] > 0:
        total, checked, _generated, buffered = counters
        st["total"], st["checked"] = total, checked
        st["progress"] = checked / total  # more precise than fraction_done (which updates once per 1000)
        if task and task.get("slot") is not None and buffered >= 0:
            path = result_path(os.path.join(data_dir, "slots", task["slot"]))
            try:
                size = os.path.getsize(path) if path else 0
            except OSError:
                size = 0
            st["found"] = (size + buffered) // ODLK_SQUARE_BYTES
    return st

