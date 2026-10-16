"""mcp-serial: an MCP server that gives AI assistants safe, structured access to embedded boards over serial.

Tools: list ports, open a console, send commands, read/tail captured output with filters,
and flash STM32 (st-flash) or ESP32 (esptool) firmware. Flashing always requires confirm=true
and a firmware file inside MCP_SERIAL_ALLOWED_DIR.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import serial
from mcp.server.mcpserver import MCPServer
from serial.tools import list_ports as _list_ports

ALLOWED_DIR = Path(os.environ.get("MCP_SERIAL_ALLOWED_DIR", os.getcwd())).resolve()
MAX_LINES = int(os.environ.get("MCP_SERIAL_MAX_LINES", "10000"))
DEFAULT_BAUD = int(os.environ.get("MCP_SERIAL_BAUD", "115200"))

server = MCPServer(
    name="mcp-serial",
    instructions=(
        "Work with microcontrollers over serial: list ports, open a console, send commands, "
        "read or tail captured output with a regex filter, and flash STM32/ESP32 firmware. "
        "Flashing requires confirm=true and a firmware file inside the allowed directory. "
        "After write_line, call read_lines with wait_seconds to collect the board's reply."
    ),
)


@dataclass
class Session:
    port: str
    baud: int
    ser: serial.Serial
    lines: deque[tuple[int, float, str]] = field(default_factory=lambda: deque(maxlen=MAX_LINES))
    seq: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._reader, name=f"mcp-serial:{self.port}", daemon=True)
        self.thread.start()

    def _reader(self) -> None:
        while not self.stop.is_set():
            try:
                raw = self.ser.readline()
            except (serial.SerialException, OSError):
                break
            if not raw:
                continue
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            with self.lock:
                self.seq += 1
                self.lines.append((self.seq, time.time(), text))

    def close(self) -> None:
        self.stop.set()
        try:
            self.ser.close()
        finally:
            if self.thread is not None:
                self.thread.join(timeout=1)


_sessions: dict[str, Session] = {}
_sessions_lock = threading.Lock()


def _session(port: str) -> Session:
    s = _sessions.get(port)
    if s is None:
        raise ValueError(f"port {port!r} is not open; call open_port first")
    return s


def _check_firmware(path: str) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f"firmware file not found: {p}")
    if p != ALLOWED_DIR and ALLOWED_DIR not in p.parents:
        raise ValueError(f"{p} is outside the allowed directory {ALLOWED_DIR} (set MCP_SERIAL_ALLOWED_DIR)")
    return p


def _run(cmd: list[str], timeout: int = 180) -> dict[str, Any]:
    if shutil.which(cmd[0]) is None:
        raise ValueError(f"{cmd[0]} is not installed or not on PATH")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return {
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


@server.tool()
def list_ports() -> list[dict[str, Any]]:
    """List serial ports with description, hardware id, VID/PID, and whether this server has them open."""
    return [
        {
            "device": p.device,
            "description": p.description,
            "hwid": p.hwid,
            "vid": p.vid,
            "pid": p.pid,
            "open": p.device in _sessions,
        }
        for p in _list_ports.comports()
    ]


@server.tool()
def open_port(port: str, baud: int = DEFAULT_BAUD) -> dict[str, Any]:
    """Open a serial port (or a pyserial URL such as loop://) and start capturing lines in the background."""
    with _sessions_lock:
        if port in _sessions:
            return {"port": port, "baud": _sessions[port].baud, "already_open": True}
        ser = serial.serial_for_url(port, baudrate=baud, timeout=0.2)
        s = Session(port=port, baud=baud, ser=ser)
        s.start()
        _sessions[port] = s
    return {"port": port, "baud": baud, "already_open": False}


@server.tool()
def close_port(port: str) -> dict[str, Any]:
    """Close a port opened with open_port and stop capturing."""
    with _sessions_lock:
        s = _sessions.pop(port, None)
    if s is None:
        return {"port": port, "closed": False, "reason": "not open"}
    s.close()
    return {"port": port, "closed": True, "lines_captured": s.seq}


@server.tool()
def write_line(port: str, text: str, newline: str = "\n") -> dict[str, Any]:
    """Send text plus a newline to an open port, e.g. a command for the firmware's CLI."""
    s = _session(port)
    n = s.ser.write((text + newline).encode("utf-8"))
    s.ser.flush()
    return {"port": port, "bytes_written": n}


@server.tool()
def read_lines(
    port: str,
    max_lines: int = 50,
    after_seq: int = 0,
    grep: str | None = None,
    wait_seconds: float = 0,
) -> dict[str, Any]:
    """Return captured lines (oldest first, newest last) with sequence numbers.

    Use after_seq to fetch only output newer than your last read, grep for a regex filter
    (e.g. "error|warn"), and wait_seconds to block briefly for fresh output after a command.
    """
    s = _session(port)
    pattern = re.compile(grep) if grep else None
    deadline = time.time() + wait_seconds
    while True:
        with s.lock:
            rows = [
                (q, t, line)
                for q, t, line in s.lines
                if q > after_seq and (pattern is None or pattern.search(line))
            ]
        if rows or time.time() >= deadline:
            break
        time.sleep(0.05)
    rows = rows[-max_lines:]
    return {
        "port": port,
        "last_seq": s.seq,
        "count": len(rows),
        "lines": [{"seq": q, "t": round(t, 3), "text": line} for q, t, line in rows],
    }


@server.tool()
def clear_buffer(port: str) -> dict[str, Any]:
    """Drop all captured lines for a port; sequence numbers keep increasing."""
    s = _session(port)
    with s.lock:
        n = len(s.lines)
        s.lines.clear()
    return {"port": port, "dropped": n}


@server.tool()
def flash_stm32(firmware_path: str, address: str = "0x08000000", confirm: bool = False) -> dict[str, Any]:
    """Flash a .bin to an STM32 over ST-Link with st-flash, then reset the target. Requires confirm=true."""
    if not confirm:
        return {"flashed": False, "reason": "set confirm=true to flash; this overwrites the firmware on the board"}
    p = _check_firmware(firmware_path)
    if not re.fullmatch(r"0x[0-9a-fA-F]{8}", address):
        raise ValueError("address must look like 0x08000000")
    res = _run(["st-flash", "--reset", "write", str(p), address])
    res["flashed"] = res["returncode"] == 0
    return res


@server.tool()
def flash_esp32(
    firmware_path: str,
    port: str,
    address: str = "0x10000",
    chip: str = "auto",
    baud: int = 460800,
    confirm: bool = False,
) -> dict[str, Any]:
    """Flash a .bin to an ESP32-family chip with esptool; closes any open session on the port first. Requires confirm=true."""
    if not confirm:
        return {"flashed": False, "reason": "set confirm=true to flash; this overwrites the firmware on the board"}
    p = _check_firmware(firmware_path)
    if not re.fullmatch(r"0x[0-9a-fA-F]{1,8}", address):
        raise ValueError("address must be hex like 0x10000")
    if not re.fullmatch(r"[a-z0-9\-]+", chip):
        raise ValueError("chip must be like auto, esp32, esp32s3 or esp32c3")
    if port in _sessions:
        close_port(port)
    tool = "esptool" if shutil.which("esptool") else "esptool.py"
    res = _run([tool, "--chip", chip, "--port", port, "--baud", str(baud), "write_flash", address, str(p)], timeout=300)
    res["flashed"] = res["returncode"] == 0
    return res


@server.tool()
def reset_stm32() -> dict[str, Any]:
    """Reset the connected STM32 target through ST-Link (st-flash reset)."""
    return _run(["st-flash", "reset"], timeout=30)


@server.tool()
def probe_stlink() -> dict[str, Any]:
    """Show the connected ST-Link target: chip id, flash and SRAM size (st-info --probe)."""
    return _run(["st-info", "--probe"], timeout=30)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
