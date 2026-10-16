import asyncio
import inspect
from pathlib import Path

import pytest

from mcp_serial import server as S


def test_loopback_roundtrip():
    assert S.open_port("loop://", baud=115200)["already_open"] is False
    assert S.open_port("loop://")["already_open"] is True
    S.write_line("loop://", "hello board")
    out = S.read_lines("loop://", wait_seconds=1)
    assert out["count"] == 1
    assert out["lines"][0]["text"] == "hello board"
    S.write_line("loop://", "status: ok")
    S.write_line("loop://", "error: overheat")
    out = S.read_lines("loop://", after_seq=1, grep="error", wait_seconds=1)
    assert [line["text"] for line in out["lines"]] == ["error: overheat"]
    assert S.clear_buffer("loop://")["dropped"] >= 1
    assert S.close_port("loop://")["closed"] is True
    assert S.close_port("loop://")["closed"] is False


def test_read_requires_open_port():
    with pytest.raises(ValueError):
        S.read_lines("not-open://")


def test_list_ports_shape():
    for p in S.list_ports():
        assert {"device", "description", "hwid", "open"} <= p.keys()


def test_flash_refuses_without_confirm(tmp_path):
    fw = tmp_path / "fw.bin"
    fw.write_bytes(b"\x00" * 16)
    assert S.flash_stm32(str(fw))["flashed"] is False
    assert S.flash_esp32(str(fw), port="/dev/null")["flashed"] is False


def test_flash_rejects_file_outside_allowed_dir(tmp_path, monkeypatch):
    fw = tmp_path / "fw.bin"
    fw.write_bytes(b"\x00" * 16)
    monkeypatch.setattr(S, "ALLOWED_DIR", Path("/nonexistent-allowed-dir").resolve())
    with pytest.raises(ValueError):
        S.flash_stm32(str(fw), confirm=True)


def test_flash_rejects_missing_file_and_bad_address(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "ALLOWED_DIR", tmp_path.resolve())
    with pytest.raises(ValueError):
        S.flash_stm32(str(tmp_path / "missing.bin"), confirm=True)
    fw = tmp_path / "fw.bin"
    fw.write_bytes(b"\x00" * 16)
    with pytest.raises(ValueError):
        S.flash_stm32(str(fw), address="8000000", confirm=True)


def test_tools_registered():
    tools = S.server.list_tools()
    if inspect.isawaitable(tools):
        tools = asyncio.run(tools)
    names = {t.name for t in tools}
    assert {
        "list_ports", "open_port", "close_port", "write_line", "read_lines",
        "clear_buffer", "flash_stm32", "flash_esp32", "reset_stm32", "probe_stlink",
    } <= names
