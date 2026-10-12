"""
ODLK1 matrix sniffer (Memory Reader), strictly READ-ONLY.

The process is opened only with PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
(no pymem: it asks for PROCESS_ALL_ACCESS by default). No memory writes,
no injections. Data is sent over UDP fire-and-forget - if the renderer is closed,
the sniffer does not even notice.

Modes:
    --list                               show matching processes
    --scan                               heuristic search for a 10x10 matrix in memory (report and exit)
    --scan --pattern "3 7 0 1 ..."       search for a specific sequence (prefix from the workunit file)
    --auto                               find the "live" matrix automatically and stream it
    --offset 0x1A2B30 [--module X.exe]   stream base(module) + offset  (static address)
    --address 0x000001F2A3B4C5D0         stream an absolute address (until the workunit restarts)

UDP packet format: 100 bytes, values 0..9, empty = 255.

Supported: Windows (primary), Linux (/proc/<pid>/mem, needs root or ptrace_scope=0).
"""
import argparse
import fnmatch
import os
import re
import socket
import struct
import sys
import time

EMPTY = 255
CELLS = 100

# ============================================================================
#  Backend: Windows
# ============================================================================
if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    PROCESS_VM_READ = 0x0010
    PROCESS_QUERY_INFORMATION = 0x0400
    TH32CS_SNAPPROCESS = 0x00000002
    TH32CS_SNAPMODULE = 0x00000008
    TH32CS_SNAPMODULE32 = 0x00000010
    MEM_COMMIT = 0x1000
    MEM_IMAGE = 0x1000000
    MEM_PRIVATE = 0x20000
    PAGE_GUARD = 0x100
    READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
    STILL_ACTIVE = 259
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", wt.WCHAR * 260)]

    class MODULEENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD),
                    ("modBaseAddr", ctypes.c_void_p), ("modBaseSize", wt.DWORD),
                    ("hModule", ctypes.c_void_p), ("szModule", wt.WCHAR * 256),
                    ("szExePath", wt.WCHAR * 260)]

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", wt.DWORD), ("PartitionId", wt.WORD),
                    ("RegionSize", ctypes.c_size_t), ("State", wt.DWORD),
                    ("Protect", wt.DWORD), ("Type", wt.DWORD)]

    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k32.OpenProcess.restype = wt.HANDLE
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.ReadProcessMemory.argtypes = [wt.HANDLE, wt.LPCVOID, wt.LPVOID, ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_size_t)]
    k32.ReadProcessMemory.restype = wt.BOOL
    k32.VirtualQueryEx.argtypes = [wt.HANDLE, wt.LPCVOID, ctypes.POINTER(MEMORY_BASIC_INFORMATION),
                                   ctypes.c_size_t]
    k32.VirtualQueryEx.restype = ctypes.c_size_t
    k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Module32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    k32.Module32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]

    def list_processes():
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap in (None, INVALID_HANDLE):
            return []
        out = []
        try:
            e = PROCESSENTRY32W()
            e.dwSize = ctypes.sizeof(e)
            ok = k32.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                out.append((e.th32ProcessID, e.szExeFile))
                ok = k32.Process32NextW(snap, ctypes.byref(e))
        finally:
            k32.CloseHandle(snap)
        return out

    class Process:
        def __init__(self, pid, name):
            self.pid, self.name = pid, name
            self.h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
            if not self.h:
                err = ctypes.get_last_error()
                raise OSError(err, f"OpenProcess({pid}) failed, err={err}. "
                                   f"If BOINC runs as a service, run the sniffer as administrator.")

        def close(self):
            if self.h:
                k32.CloseHandle(self.h)
                self.h = None

        def alive(self):
            code = wt.DWORD()
            return bool(k32.GetExitCodeProcess(self.h, ctypes.byref(code))) and code.value == STILL_ACTIVE

        def read(self, addr, size):
            buf = ctypes.create_string_buffer(size)
            n = ctypes.c_size_t(0)
            ok = k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n))
            if not ok or n.value != size:
                return None
            return buf.raw

        def regions(self):
            mbi = MEMORY_BASIC_INFORMATION()
            addr = 0
            while addr < 0x7FFFFFFFFFFF:
                if not k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                    break
                base = mbi.BaseAddress or 0
                size = mbi.RegionSize
                if (mbi.State == MEM_COMMIT and (mbi.Protect & 0xFF) in READABLE
                        and not (mbi.Protect & PAGE_GUARD) and mbi.Type in (MEM_PRIVATE, MEM_IMAGE)):
                    yield base, size, ("image" if mbi.Type == MEM_IMAGE else "heap")
                if size == 0:
                    break
                addr = base + size

        def modules(self):
            snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid)
            if snap in (None, INVALID_HANDLE):
                return []
            out = []
            try:
                m = MODULEENTRY32W()
                m.dwSize = ctypes.sizeof(m)
                ok = k32.Module32FirstW(snap, ctypes.byref(m))
                while ok:
                    out.append((m.szModule, m.modBaseAddr or 0, m.modBaseSize))
                    ok = k32.Module32NextW(snap, ctypes.byref(m))
            finally:
                k32.CloseHandle(snap)
            return out

    def lower_own_priority():
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL

# ============================================================================
#  Backend: Linux
# ============================================================================
else:
    def list_processes():
        out = []
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open(f"/proc/{d}/cmdline", "rb") as f:
                    exe = f.read().split(b"\0")[0].decode(errors="replace")
                out.append((int(d), os.path.basename(exe)))
            except OSError:
                pass
        return out

    class Process:
        def __init__(self, pid, name):
            self.pid, self.name = pid, name
            self.f = open(f"/proc/{pid}/mem", "rb", buffering=0)

        def close(self):
            self.f.close()

        def alive(self):
            return os.path.exists(f"/proc/{self.pid}")

        def read(self, addr, size):
            try:
                self.f.seek(addr)
                d = self.f.read(size)
                return d if d and len(d) == size else None
            except (OSError, ValueError, OverflowError):
                return None

        def _maps(self):
            with open(f"/proc/{self.pid}/maps") as f:
                for line in f:
                    parts = line.split(maxsplit=5)
                    start, end = (int(x, 16) for x in parts[0].split("-"))
                    path = parts[5].strip() if len(parts) > 5 else ""
                    yield start, end, parts[1], int(parts[2], 16), path

        def regions(self):
            for start, end, perms, _, path in self._maps():
                if "r" in perms and path not in ("[vvar]", "[vsyscall]", "[vdso]"):
                    yield start, end - start, ("image" if path and not path.startswith("[") else "heap")

        def modules(self):
            mods = {}
            for start, end, _, off, path in self._maps():
                if path and not path.startswith("["):
                    b, e = mods.get(path, (start, end))
                    mods[path] = (min(b, start), max(e, end))
            return [(os.path.basename(p), b, e - b) for p, (b, e) in mods.items()]

    def lower_own_priority():
        try:
            os.nice(5)
        except OSError:
            pass


# ============================================================================
#  Common logic
# ============================================================================
def find_processes(pattern):
    pat = pattern.lower()
    return [(pid, name) for pid, name in list_processes()
            if fnmatch.fnmatch(name.lower(), pat) and pid != os.getpid()]


def decode(block, elem_size):
    if elem_size == 1:
        return list(block)
    return list(memoryview(block).cast({2: "H", 4: "I"}[elem_size]))


def normalize(raw, base):
    return bytes((v - base) if 0 <= v - base <= 9 else EMPTY for v in raw)


def validate(raw, base):
    """Does this look like a working DLS-10 matrix? -> (filled, marker) or None."""
    vals = []
    marker = None
    for r in raw:
        v = r - base
        if 0 <= v <= 9:
            vals.append(v)
        else:
            if marker is None:
                marker = r
            elif r != marker:
                return None
            vals.append(-1)
    if any(v < 0 for v in vals[:10]) or len(set(vals[:10])) != 10:
        return None
    lines = [range(r * 10, r * 10 + 10) for r in range(10)]
    lines += [range(c, 100, 10) for c in range(10)]
    lines += [range(0, 100, 11), range(9, 91, 9)]
    for line in lines:
        seen = 0
        for i in line:
            v = vals[i]
            if v >= 0:
                b = 1 << v
                if seen & b:
                    return None
                seen |= b
    return sum(1 for v in vals if v >= 0), marker


def first_row_regex(elem_size, base):
    """10 pairwise distinct values base..base+9 (via negative lookahead backreferences)."""
    cls = f"[\\x{base:02x}-\\x{base + 9:02x}]"
    pad = f"\\x00{{{elem_size - 1}}}" if elem_size > 1 else ""
    parts = []
    for g in range(1, 11):
        refs = "|".join("\\" + str(j) for j in range(1, g))
        neg = f"(?!{refs})" if g > 1 else ""
        parts.append(f"{neg}({cls}){pad}")
    return re.compile(("(?=" + "".join(parts) + ")").encode("ascii"), re.DOTALL)


class Candidate:
    def __init__(self, addr, elem_size, base, kind, filled=0, marker=None):
        self.addr, self.elem_size, self.base, self.kind = addr, elem_size, base, kind
        self.filled, self.marker = filled, marker
        self.live = False
        self.changes = 0


def iter_chunks(proc, max_region, chunk=4 << 20, overlap=512):
    for rbase, rsize, kind in proc.regions():
        if rsize > max_region:
            continue
        for off in range(0, rsize, chunk):
            n = min(chunk + overlap, rsize - off)
            data = proc.read(rbase + off, n)
            if data is None:
                continue
            yield rbase + off, data, min(chunk, n), kind


def scan(proc, elem_sizes, bases, pattern=None, max_region=512 << 20, limit=5000):
    found, seen = [], set()
    if pattern is not None:
        combos = [(s, None, re.compile(b"(?=" + re.escape(b"".join(v.to_bytes(s, "little", signed=v < 0)
                                                                    for v in pattern)) + b")", re.DOTALL))
                  for s in elem_sizes]
    else:
        combos = [(s, b, first_row_regex(s, b)) for s in elem_sizes for b in bases]
    t0 = time.time()
    for cbase, data, own, kind in iter_chunks(proc, max_region):
        for s, b, rx in combos:
            need = CELLS * s
            for m in rx.finditer(data):
                pos = m.start()
                if pos >= own:
                    break
                addr = cbase + pos
                if addr % s or (addr, s, b) in seen:
                    continue
                if pattern is not None:
                    seen.add((addr, s, b))
                    found.append(Candidate(addr, s, None, kind))
                else:
                    block = data[pos:pos + need]
                    if len(block) < need:
                        continue
                    res = validate(decode(block, s), b)
                    if res:
                        seen.add((addr, s, b))
                        found.append(Candidate(addr, s, b, kind, *res))
                if len(found) >= limit:
                    return found, time.time() - t0
    return found, time.time() - t0


def check_live(proc, cands, rounds=15, interval=0.05):
    if not cands:
        return
    prev = [proc.read(c.addr, CELLS * c.elem_size) for c in cands]
    for _ in range(rounds):
        time.sleep(interval)
        for i, c in enumerate(cands):
            d = proc.read(c.addr, CELLS * c.elem_size)
            if d is not None and prev[i] is not None and d != prev[i]:
                c.changes += 1
            prev[i] = d
    for c in cands:
        c.live = c.changes > 0


def where(addr, modules):
    for name, base, size in modules:
        if base <= addr < base + size:
            return f"{name}+0x{addr - base:X}"
    return "heap (address is not static)"


def print_candidates(cands, modules, show=30):
    print(f"{'#':>3}  {'address':>18}  el  base  marker  filled  live  location")
    for i, c in enumerate(cands[:show]):
        mk = "-" if c.marker is None else str(c.marker)
        b = "-" if c.base is None else str(c.base)
        print(f"{i:>3}  0x{c.addr:016X}  {c.elem_size:>2}  {b:>4}  {mk:>6}  {c.filled:>6}  "
              f"{'YES' if c.live else ' no':>4}  {where(c.addr, modules)}")
    if len(cands) > show:
        print(f"... and {len(cands) - show} more")


def sort_candidates(cands):
    # live -> incomplete (search in progress) -> inside a module (static address)
    cands.sort(key=lambda c: (not c.live, c.filled == 100, c.kind != "image", -c.changes))


def print_matrix(norm):
    for r in range(10):
        print("   " + " ".join("." if v == EMPTY else str(v) for v in norm[r * 10:r * 10 + 10]))
    print()


def _odlk_progress(item):
    pid, name = item
    try:
        p = Process(pid, name)
    except OSError:
        return 2.0
    try:
        c = OdlkCounters(p).read()
        if not c:
            return 2.0
        total, checked = c[0], c[1]
        if total > 0:
            return checked / total
        return 0.0
    finally:
        p.close()


def attach(args, stop_event=None):
    announced = False
    while True:
        if stop_event and stop_event.is_set():
            return None
        if args.pid:
            procs = [(p, n) for p, n in list_processes() if p == args.pid]
        else:
            procs = find_processes(args.process)
        if procs:
            if len(procs) > 1 and all(is_odlk(n) for _, n in procs):
                procs.sort(key=_odlk_progress)  # the freshest workunit stays attached the longest
                print(f"[sniffer] found {len(procs)} ODLK workunits, taking the one with the least progress")
            elif len(procs) > 1:
                print(f"[sniffer] found {len(procs)} processes, taking the first one (choose with --pid):")
                for p, n in procs:
                    print(f"           pid={p}  {n}")
            pid, name = procs[0]
            proc = Process(pid, name)
            print(f"[sniffer] attached (read-only): pid={pid} {name}")
            return proc
        if not announced:
            print(f"[sniffer] waiting for process '{args.pid or args.process}' ...")
            announced = True
        time.sleep(2)


def resolve_target(proc, args):
    """-> (addr, elem_size, base, auto_validate)"""
    if args.address is not None:
        return args.address, args.elem_size, args.base, False
    if args.offset is not None:
        mods = proc.modules()
        want = (args.module or proc.name).lower()
        for name, mbase, _ in mods:
            if name.lower() == want:
                return mbase + args.offset, args.elem_size, args.base, False
        raise RuntimeError(f"module {want} not found in the process")
    if not args.auto and is_odlk(proc.name):
        for name, mbase, _ in proc.modules():
            if name.lower() == proc.name.lower():
                return mbase + ODLK_MATRIX, 1, 0, False
    # --auto
    sizes = [args.elem_size] if args.elem_size_set else [1, 4, 2]
    bases = [args.base] if args.base_set else [0, 1]
    while True:
        print("[sniffer] auto: scanning memory ...")
        cands, dt = scan(proc, sizes, bases)
        check_live(proc, cands[:2000])
        sort_candidates(cands)
        live = [c for c in cands if c.live]
        print(f"[sniffer] auto: {len(cands)} candidates, {len(live)} live ({dt:.1f} s)")
        if live:
            c = live[0]
            print(f"[sniffer] auto: selected 0x{c.addr:X} el={c.elem_size} base={c.base} "
                  f"-> {where(c.addr, proc.modules())}")
            return c.addr, c.elem_size, c.base, True
        if not proc.alive():
            raise RuntimeError("process exited")
        time.sleep(3)


# ============================================================================
#  ODLK profile (odlk3 / odlkmax 1.0, windows_x86_64): found by reverse-engineering the binary.
#  Global variables in .bss (offsets from the exe base):
#    +0x50B040  uint8[100]  current square (what we draw)
#    +0x50B190  uint64      N - workunit size (second number from input.txt)
#    +0x50B198  uint64      squares checked (ODLK computes fraction_done = this / N)
#    +0x50B1A0  uint64      squares generated
#  ODLK writes a found square to the result file right away with fwrite (212 bytes per square),
#  through the msvcrt buffer - so the number found = (file size + bytes in the FILE buffer) / 212.
# ============================================================================
ODLK_MATRIX = 0x50B040
ODLK_COUNTERS = 0x50B190
ODLK_SQUARE_BYTES = 212


def is_odlk(name):
    return name.lower().startswith("odlk")


def _pe_export(proc, module_base, wanted):
    """RVA of an export from a module image loaded in another process (read-only)."""
    hdr = proc.read(module_base, 0x400)
    if not hdr or hdr[:2] != b"MZ":
        return None
    pe = int.from_bytes(hdr[0x3C:0x40], "little")
    opt = pe + 24
    magic = int.from_bytes(hdr[opt:opt + 2], "little")
    dd = opt + (112 if magic == 0x20B else 96)
    exp_rva = int.from_bytes(hdr[dd:dd + 4], "little")
    exp = proc.read(module_base + exp_rva, 40)
    if not exp:
        return None
    n_names = int.from_bytes(exp[24:28], "little")
    funcs, names, ords = (int.from_bytes(exp[o:o + 4], "little") for o in (28, 32, 36))
    name_rvas = proc.read(module_base + names, 4 * n_names)
    ordinals = proc.read(module_base + ords, 2 * n_names)
    if not name_rvas or not ordinals:
        return None
    w = wanted.encode() + b"\0"
    for i in range(n_names):
        rva = int.from_bytes(name_rvas[4 * i:4 * i + 4], "little")
        if proc.read(module_base + rva, len(w)) == w:
            o = int.from_bytes(ordinals[2 * i:2 * i + 2], "little")
            f = proc.read(module_base + funcs + 4 * o, 4)
            return int.from_bytes(f, "little") if f else None
    return None


class OdlkCounters:
    """Reads ODLK counters and the fill level of the result file buffer."""

    def __init__(self, proc):
        self.proc = proc
        mods = {m[0].lower(): m for m in proc.modules()}
        exe = mods.get(proc.name.lower())
        self.base = exe[1] if exe else None
        self.iob = None
        msv = mods.get("msvcrt.dll")
        if sys.platform == "win32" and msv:
            rva = _pe_export(proc, msv[1], "_iob")
            if rva:
                self.iob = msv[1] + rva

    def read(self):
        """-> (total, checked, generated, buffered_bytes) or None."""
        if self.base is None:
            return None
        d = self.proc.read(self.base + ODLK_COUNTERS, 24)
        if not d:
            return None
        total, checked, generated = struct.unpack("<QQQ", d)
        return total, checked, generated, self._buffered()

    def _buffered(self):
        # FILE (msvcrt, x64): _ptr@0, _cnt@8, _base@16, _flag@24, _file@28 ... size 48
        if not self.iob:
            return -1
        d = self.proc.read(self.iob, 48 * 20)
        if not d:
            return -1
        for i in range(3, 20):  # 0..2 — stdin/stdout/stderr
            ptr, = struct.unpack_from("<Q", d, i * 48)
            base, = struct.unpack_from("<Q", d, i * 48 + 16)
            flag, fd = struct.unpack_from("<ii", d, i * 48 + 24)
            if flag & 0x2 and fd >= 3:  # _IOWRT - the only file open for writing = the result
                return ptr - base if base else 0
        return -1


def parse_int(s):
    return int(s, 0)


def stream_loop(args, stop_event=None, pause_event=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    target = (args.host, args.port)
    period = 1.0 / args.hz

    while True:
        if stop_event and stop_event.is_set():
            break
        if pause_event and pause_event.is_set():
            time.sleep(0.1)
            continue
        proc = attach(args, stop_event=stop_event)
        if not proc:
            break
        try:
            addr, s, base, auto_validate = resolve_target(proc, args)
        except (RuntimeError, OSError) as e:
            print(f"[sniffer] {e}")
            proc.close()
            time.sleep(2)
            continue
        print(f"[sniffer] stream 0x{addr:X} (el={s}, base={base}) -> udp://{args.host}:{args.port} @ {args.hz:g} Hz")
        counters = OdlkCounters(proc) if is_odlk(proc.name) else None
        tail = struct.pack("<IQQQq", proc.pid, 0, 0, 0, -1)
        last_cnt = 0.0
        bad = 0
        last_print = 0.0
        next_t = time.perf_counter()
        while True:
            if stop_event and stop_event.is_set():
                proc.close()
                return
            if pause_event and pause_event.is_set():
                time.sleep(0.1)
                continue
            if not proc.alive():
                print(f"[sniffer] task pid={proc.pid} finished, selecting next task...")
                break
            d = proc.read(addr, CELLS * s)
            if d is None:
                bad += 1
                if bad > 60 and not proc.alive():
                    print(f"[sniffer] task pid={proc.pid} exited, selecting next task...")
                    break
                time.sleep(0.01)
            else:
                bad = 0
                raw = decode(d, s)
                if auto_validate and not validate(raw, base):
                    bad += 1
                    if bad > 180:
                        print("[sniffer] matrix no longer looks like a DLS - rescanning")
                        break
                else:
                    bad = 0
                norm = normalize(raw, base)
                if counters and time.perf_counter() - last_cnt >= 0.25:
                    last_cnt = time.perf_counter()
                    c = counters.read()
                    if c:
                        tail = struct.pack("<IQQQq", proc.pid, *c)
                try:
                    # 100 matrix bytes + pid, N, checked, generated, bytes in the result buffer
                    sock.sendto(norm + tail, target)
                except OSError:
                    pass
                if args.print_every and time.perf_counter() - last_print >= args.print_every:
                    last_print = time.perf_counter()
                    print_matrix(norm)
            next_t += period
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()
        proc.close()
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser(description="ODLK1 read-only memory sniffer -> UDP")
    ap.add_argument("--process", default="odlk*", help="exe name (glob), default 'odlk*'")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--pattern", help='sequence to search for, e.g. "0 1 2 3 4 5 6 7 8 9"')
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--offset", type=parse_int)
    ap.add_argument("--module", help="module for --offset (the exe itself by default)")
    ap.add_argument("--address", type=parse_int)
    ap.add_argument("--elem-size", type=int, choices=[1, 2, 4], default=None)
    ap.add_argument("--base", type=int, choices=[0, 1], default=None,
                    help="0: digits 0..9; 1: digits 1..10 (0 = empty)")
    ap.add_argument("--hz", type=float, default=60.0)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--print", dest="print_every", type=float, default=0,
                    help="print the matrix to the console every N seconds")
    args = ap.parse_args()
    args.elem_size_set = args.elem_size is not None
    args.base_set = args.base is not None
    args.elem_size = args.elem_size or 1
    args.base = 0 if args.base is None else args.base

    if args.list:
        for pid, name in (find_processes(args.process) if not args.pid else
                          [(p, n) for p, n in list_processes() if p == args.pid]):
            print(f"pid={pid:<8} {name}")
        return

    lower_own_priority()

    if args.scan:
        proc = attach(args)
        sizes = [args.elem_size] if args.elem_size_set else [1, 4, 2]
        bases = [args.base] if args.base_set else [0, 1]
        pattern = [int(x, 0) for x in args.pattern.replace(",", " ").split()] if args.pattern else None
        cands, dt = scan(proc, sizes, bases, pattern)
        print(f"[scan] {len(cands)} matches in {dt:.1f} s, checking volatility ...")
        check_live(proc, cands[:2000])
        sort_candidates(cands)
        print_candidates(cands, proc.modules())
        if cands and pattern is None:
            c = cands[0]
            d = proc.read(c.addr, CELLS * c.elem_size)
            if d:
                print("\nBest candidate right now:")
                print_matrix(normalize(decode(d, c.elem_size), c.base))
        print("Hint: an address like 'module.exe+0x...' -> run with --offset; 'heap' -> --auto "
              "or a pointer scan in Cheat Engine.")
        return

    if sys.platform == "win32":
        mutex = k32.CreateMutexW(None, True, "Local\\ODLK1_Voxel_Sniffer_Lock")
        if k32.GetLastError() == 183:
            print("[sniffer] another sniffer instance is already running; exiting")
            return

    stream_loop(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[sniffer] stopped")

