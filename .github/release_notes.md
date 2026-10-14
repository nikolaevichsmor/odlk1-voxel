### Real-Time Split-Screen Visualizer for BOINC ODLK Tasks

Real-time split-screen visualizer for BOINC **ODLK** computations (searching for diagonal Latin squares of order 10) rendered as an interactive **2.5D isometric $10 \times 10$ pillar landscape** alongside a synchronized **multi-color $10 \times 10$ ODLK matrix**.

---

### Highlights

- **Zero Overhead**: Process memory is monitored strictly read-only (`PROCESS_VM_READ`), ensuring no slowdown to BOINC calculations.
- **Continuous Tracking**: Automatically targets the workunit with the lowest progress and tracks it through to 100% completion before switching to the next task.
- **Split-Screen 2.5D & Matrix View**: Interactive 2.5D isometric $10 \times 10$ pillar landscape (with 360° mouse rotation, tilt, and scroll zoom) on the left, paired with a high-contrast ODLK color $10 \times 10$ matrix and digit filter (`All | 0..9`) on the right.
- **Live Telemetry**: Real-time progress bar, search space counters, elapsed time, and estimated remaining computation.

---

### Quick Start (Windows)

1. Download and extract **`odlk1-voxel.zip`** from the Assets below.
2. Run **`start_odlk.bat`** *(run as administrator if BOINC is installed as a service)*.
3. The visualizer opens automatically in your browser at `http://127.0.0.1:8080`, and an icon appears in your Windows system tray to pause, resume, or exit.

> **Note:** To preview the visualizer without running BOINC, run **`start_demo.bat`** instead.

---

**Requirements:** Windows, Python 3.8+ (standard library only — no `pip` dependencies needed).
