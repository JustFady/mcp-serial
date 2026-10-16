"""Drive mcp-serial as a real MCP client over stdio, using pyserial's loop:// so no hardware is needed.

Run: .venv/bin/python examples/session.py
"""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def show(name: str, result) -> None:
    text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    try:
        text = json.dumps(json.loads(text), indent=1)
    except Exception:  # noqa: BLE001
        pass
    print(f"\n> {name}\n{text}")


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_serial.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", ", ".join(t.name for t in tools.tools))
            show("open_port", await session.call_tool("open_port", {"port": "loop://", "baud": 115200}))
            show("write_line", await session.call_tool("write_line", {"port": "loop://", "text": "status"}))
            show("write_line", await session.call_tool("write_line", {"port": "loop://", "text": "ERR: imu init timeout"}))
            show("read_lines (grep=ERR)", await session.call_tool(
                "read_lines", {"port": "loop://", "grep": "ERR", "wait_seconds": 1}))
            show("flash_stm32 without confirm", await session.call_tool(
                "flash_stm32", {"firmware_path": "build/app.bin"}))
            show("close_port", await session.call_tool("close_port", {"port": "loop://"}))


if __name__ == "__main__":
    asyncio.run(main())
