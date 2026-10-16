# mcp-serial

[![ci](https://github.com/JustFady/mcp-serial/actions/workflows/ci.yml/badge.svg)](https://github.com/JustFady/mcp-serial/actions/workflows/ci.yml)

An [MCP](https://modelcontextprotocol.io) server that lets Claude, Cursor, or any MCP client work with your microcontroller the way you do at a serial console: list ports, open a session, send commands, read and filter the output, and flash firmware to STM32 or ESP32 boards. Flashing is gated behind an explicit `confirm=true` and an allow-listed firmware directory, so the assistant can't overwrite a board by accident.

```
you:    "Flash build/app.bin to the Nucleo, then tell me if the sensor init logs any errors."
claude: flash_stm32(firmware_path="build/app.bin", confirm=true)      -> flashed, reset
        open_port("/dev/cu.usbmodem1103", 115200)
        read_lines(port=..., grep="error|fail", wait_seconds=5)        -> 0 matches
        "Flashed and reset. 1,214 lines in 5 s, no errors; IMU and BMP280 both report OK."
```

## Install

```bash
uv tool install mcp-serial          # or: pipx install mcp-serial
```

Claude Code:

```bash
claude mcp add mcp-serial -e MCP_SERIAL_ALLOWED_DIR=$PWD -- mcp-serial
```

Claude Desktop / other clients (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "mcp-serial": {
      "command": "mcp-serial",
      "env": { "MCP_SERIAL_ALLOWED_DIR": "/path/to/your/firmware/project" }
    }
  }
}
```

Flashing uses whatever you already have installed: `st-flash`/`st-info` (stlink) for STM32, `esptool` for ESP32.

## Tools

| Tool | What it does |
|---|---|
| `list_ports()` | Serial ports with description, hwid, VID/PID, and whether a session is open |
| `open_port(port, baud=115200)` | Open a port (or a pyserial URL like `loop://`) and capture lines in the background |
| `close_port(port)` | Stop capturing and release the port |
| `write_line(port, text)` | Send a command to the firmware's CLI |
| `read_lines(port, max_lines, after_seq, grep, wait_seconds)` | Page through captured output with sequence numbers, a regex filter, and an optional short wait for new data |
| `clear_buffer(port)` | Drop the capture buffer |
| `flash_stm32(firmware_path, address="0x08000000", confirm)` | `st-flash --reset write` |
| `flash_esp32(firmware_path, port, address="0x10000", chip="auto", baud, confirm)` | `esptool write_flash`; closes the port session first |
| `reset_stm32()` | `st-flash reset` |
| `probe_stlink()` | `st-info --probe` |

## Example session

`examples/session.py` drives the server as a real MCP client over stdio, using pyserial's `loop://` so it runs without hardware:

```
tools: list_ports, open_port, close_port, write_line, read_lines, clear_buffer, flash_stm32, flash_esp32, reset_stm32, probe_stlink

> open_port            {"port": "loop://", "baud": 115200, "already_open": false}
> write_line           {"port": "loop://", "bytes_written": 7}          # "status"
> write_line           {"port": "loop://", "bytes_written": 22}         # "ERR: imu init timeout"
> read_lines (grep=ERR)
  {"port": "loop://", "last_seq": 2, "count": 1,
   "lines": [{"seq": 2, "t": 1792127056.6, "text": "ERR: imu init timeout"}]}
> flash_stm32 without confirm
  {"flashed": false, "reason": "set confirm=true to flash; this overwrites the firmware on the board"}
> close_port           {"port": "loop://", "closed": true, "lines_captured": 2}
```

## Safety model

- Flash tools refuse unless `confirm=true` is passed, so the client has to state the intent explicitly.
- Firmware paths must resolve inside `MCP_SERIAL_ALLOWED_DIR` (default: the server's working directory).
- External tools run with argument lists, never through a shell; addresses and chip names are validated.
- Output is captured in a bounded ring buffer (`MCP_SERIAL_MAX_LINES`, default 10,000) so a chatty board can't exhaust memory.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `MCP_SERIAL_ALLOWED_DIR` | cwd | Directory firmware files must live under |
| `MCP_SERIAL_BAUD` | `115200` | Default baud for `open_port` |
| `MCP_SERIAL_MAX_LINES` | `10000` | Capture buffer size per port |

## Development

```bash
uv venv && uv pip install -e ".[dev]" && pytest -q
```

The tests use pyserial's `loop://` loopback, so they run without hardware.

## License

MIT © Fady Youssef
