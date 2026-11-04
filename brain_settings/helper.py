"""Portable executable entry point for the loopback app and read-only MCP."""

import argparse
import logging
import sys
from pathlib import Path

from brain_settings.runtime import data_directory

VERSION = "0.1.0"


def _port(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Port must be an integer from 1 to 65535") from None
    if not 1 <= result <= 65535:
        raise argparse.ArgumentTypeError("Port must be an integer from 1 to 65535")
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="SecondBrainHelper")
    parser.add_argument("--version", action="version", version=f"SecondBrainHelper {VERSION}")
    commands = parser.add_subparsers(dest="mode", required=True)
    serve = commands.add_parser("serve", help="Run the local application API")
    serve.add_argument("--port", type=_port, required=True)
    serve.add_argument("--data-dir", type=Path)
    mcp = commands.add_parser("mcp", help="Run the read-only MCP stdio server")
    mcp.add_argument("--data-dir", type=Path)
    arguments = parser.parse_args(argv)
    directory = data_directory(arguments.data_dir)
    if arguments.mode == "serve":
        import uvicorn

        from brain_settings.app import create_app

        uvicorn.run(create_app(data_dir=directory), host="127.0.0.1", port=arguments.port,
                    loop="asyncio", http="h11", ws="none", lifespan="on", access_log=False,
                    log_level="warning")
    else:
        from brain_settings.mcp_server import build_server

        logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
        build_server(directory).run(transport="stdio")


if __name__ == "__main__":
    main()
