"""
Mock stand-in for the ODLK1 visualizer.

Instead of random noise this runs a REAL backtracking search for a
diagonal Latin square of order 10:
  * the first row is a random permutation (like ODLK's fixed prefix),
  * cells are filled row by row, checking rows, columns and both diagonals,
  * on a dead end the algorithm backtracks (the cell is cleared -> 255).

Every 1/hz seconds the current matrix state (100 bytes, 0..9, empty = 255)
is sent over UDP to 127.0.0.1:5005, fire-and-forget.

The matrix lives in a contiguous bytearray(100), so this process can be used
as a test subject for sniffer.py --auto (testing the sniffer without BOINC).
"""
import argparse
import random
import socket
import time

EMPTY = 255


class DLSSearch:
    """Step-by-step search for a diagonal Latin square (one step = one placement/backtrack)."""

    def __init__(self, rng: random.Random, n: int = 10, restart_steps: int = 500_000):
        self.rng = rng
        self.n = n
        self.restart_steps = restart_steps
        self.grid = bytearray([EMPTY] * (n * n))  # the same buffer for the whole lifetime
        self.solutions = 0
        self.total_steps = 0
        self.reset()

    def reset(self):
        n = self.n
        for i in range(n * n):
            self.grid[i] = EMPTY
        self.rows = [0] * n
        self.cols = [0] * n
        self.d1 = 0
        self.d2 = 0
        self.cands = [None] * (n * n)
        perm = list(range(n))
        self.rng.shuffle(perm)
        for c, v in enumerate(perm):  # prefix: first row
            self._place(c, v)
        self.pos = n
        self.steps = 0
        self.solved = False

    def _place(self, i, v):
        n = self.n
        r, c = divmod(i, n)
        b = 1 << v
        self.rows[r] |= b
        self.cols[c] |= b
        if r == c:
            self.d1 |= b
        if r + c == n - 1:
            self.d2 |= b
        self.grid[i] = v

    def _remove(self, i):
        n = self.n
        r, c = divmod(i, n)
        b = ~(1 << self.grid[i])
        self.rows[r] &= b
        self.cols[c] &= b
        if r == c:
            self.d1 &= b
        if r + c == n - 1:
            self.d2 &= b
        self.grid[i] = EMPTY

    def step(self):
        if self.solved:
            return
        n = self.n
        self.steps += 1
        self.total_steps += 1
        i = self.pos
        if self.grid[i] != EMPTY:
            self._remove(i)
        lst = self.cands[i]
        if lst is None:
            r, c = divmod(i, n)
            used = self.rows[r] | self.cols[c]
            if r == c:
                used |= self.d1
            if r + c == n - 1:
                used |= self.d2
            lst = [v for v in range(n) if not (used >> v) & 1]
            self.rng.shuffle(lst)
            self.cands[i] = lst
        if lst:
            self._place(i, lst.pop())
            self.pos += 1
            if self.pos == n * n:
                self.solved = True
                self.solutions += 1
        else:
            self.cands[i] = None
            self.pos -= 1
            if self.pos < n:  # prefix branch exhausted
                self.reset()
        if self.steps >= self.restart_steps and not self.solved:
            self.reset()  # heavy tail - restart with a new prefix


def stream_loop(args, stop_event=None, pause_event=None):
    rng = random.Random(args.seed)
    search = DLSSearch(rng, restart_steps=args.restart_steps)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    target = (args.host, args.port)

    period = 1.0 / args.hz
    next_t = time.perf_counter()
    solved_at = None
    last_report = time.perf_counter()
    print(f"[mock] backtracking DLS-10 -> udp://{args.host}:{args.port} @ {args.hz:g} Hz, {args.steps} steps/frame")

    while True:
        if stop_event and stop_event.is_set():
            break
        if pause_event and pause_event.is_set():
            time.sleep(0.1)
            continue

        if search.solved:
            if solved_at is None:
                solved_at = time.perf_counter()
            elif time.perf_counter() - solved_at >= args.hold:
                search.reset()
                solved_at = None
        else:
            for _ in range(args.steps):
                search.step()
                if search.solved:
                    break

        try:
            sock.sendto(search.grid, target)  # fire-and-forget
        except OSError:
            pass

        now = time.perf_counter()
        if now - last_report >= 2.0:
            filled = sum(1 for v in search.grid if v != EMPTY)
            print(f"[mock] steps={search.total_steps:,} depth={filled:3d}/100 solutions={search.solutions}")
            last_report = now

        next_t += period
        delay = next_t - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        else:
            next_t = time.perf_counter()


def main():
    ap = argparse.ArgumentParser(description="Mock ODLK1: backtracking DLS-10 -> UDP")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--hz", type=float, default=60.0, help="frame send rate")
    ap.add_argument("--steps", type=int, default=200, help="search steps between frames (1 = slow motion)")
    ap.add_argument("--hold", type=float, default=1.5, help="seconds to show a found square")
    ap.add_argument("--restart-steps", type=int, default=500_000)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    try:
        stream_loop(args)
    except KeyboardInterrupt:
        print("\n[mock] stopped")


if __name__ == "__main__":
    main()

