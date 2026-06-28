"""Process identity and port-collision detection for debugging stale-worker issues.

When uvicorn --reload misbehaves on this Windows machine (silent 500s, exception
handlers not firing), the most common cause is an orphaned worker process from a
previous session still bound to the target port. The new uvicorn and the zombie
both end up listening, and the load balancer / Windows socket sharing routes
requests to whichever socket the OS picks — usually the orphan, which has stale
code loaded.

Two helpers here turn that opaque failure into a loud, attributable signal:

* ``get_process_identity()`` returns this process's PID, parent PID, a fresh
  ``worker_id`` (uuid), and start time. Logged at startup and exposed via
  ``/health/whoami`` so any log line can be traced back to the worker that
  produced it.

* ``check_port_collision()`` probes a TCP port before uvicorn binds. If it
  succeeds and the owning PID is not us, it returns the squatter's PID via
  ``netstat -ano``. ``main.py`` logs a ``PORT_COLLISION`` warning so you see
  the zombie immediately rather than discovering it hours later when a 500
  looks like a code bug.
"""

from __future__ import annotations

import logging
import os
import platform
import socket
import subprocess
import time
import uuid

logger = logging.getLogger(__name__)

# Stable per-process. Generating on every get_process_identity() call would
# produce a different worker_id for the WORKER_START log line vs the /health/whoami
# response, defeating the whole point of correlating logs to a worker. With
# uvicorn --reload the worker is respawned via multiprocessing.spawn on each
# reload, so each fresh worker process picks up a new uuid at import time —
# which is exactly what we want (a stale orphan keeps its old worker_id).
_PROCESS_IDENTITY: dict[str, int | float | str] = {
    "pid": os.getpid(),
    "parent_pid": os.getppid(),
    "worker_id": uuid.uuid4().hex[:12],
    "started_at": time.time(),
}


def get_process_identity() -> dict[str, int | float | str]:
    """Snapshot of this worker's identity.

    Cached at module import — see ``_PROCESS_IDENTITY`` for why.
    """
    return dict(_PROCESS_IDENTITY)


def check_port_collision(host: str, port: int, own_pid: int) -> dict | None:
    """Probe ``host:port`` and return info about an existing listener.

    Returns ``None`` if the port is free. Returns a dict with ``port``,
    ``existing_pid``, and (best-effort) ``existing_cmdline`` / ``existing_age``
    if something is already listening. The owning process's cmdline is
    resolved via PowerShell ``Get-CimInstance Win32_Process`` on Windows and
    ``ps`` elsewhere, so non-Windows callers still get useful info.

    ``own_pid`` is excluded from the result — uvicorn may have already bound
    by the time this runs in the worker subprocess, and we don't want to
    warn about ourselves.
    """
    try:
        with socket.create_connection((host, port), timeout=0.5):
            pass
    except (TimeoutError, ConnectionRefusedError, OSError):
        return None

    pid = _find_listener_pid(host, port)
    if pid is None or pid == own_pid:
        return None

    info: dict = {"port": port, "host": host, "existing_pid": pid}
    try:
        info["existing_cmdline"] = _get_process_cmdline(pid)
    except Exception:
        pass
    try:
        info["existing_age_seconds"] = _get_process_age_seconds(pid)
    except Exception:
        pass
    return info


def _find_listener_pid(host: str, port: int) -> int | None:
    """Look up the PID owning the LISTENING socket on ``host:port``.

    Uses ``netstat -ano`` on Windows and falls back to ``lsof`` / ``ss``
    elsewhere. Returns ``None`` if the lookup fails or the entry can't be
    parsed — never raises, since observability code must not crash the app.
    """
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
            suffix = f":{port} "
            for line in out.splitlines():
                if "LISTENING" not in line or suffix not in line:
                    continue
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        return int(parts[-1])
                    except ValueError:
                        continue
            return None
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        return int(out.splitlines()[0]) if out else None
    except Exception:
        return None


def _get_process_cmdline(pid: int) -> str:
    if platform.system() == "Windows":
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        return out or ""
    out = subprocess.run(
        ["ps", "-p", str(pid), "-o", "args="],
        capture_output=True,
        text=True,
        timeout=2,
    ).stdout.strip()
    return out


def _get_process_age_seconds(pid: int) -> float | None:
    """Seconds since this PID started. None on failure."""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; "
                        f"if ($p) {{ [int]((Get-Date) - $p.CreationDate).TotalSeconds }} "
                        f"else {{ -1 }}"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout.strip()
            return float(out) if out and out != "-1" else None
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etimes="],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        return float(out) if out else None
    except Exception:
        return None
