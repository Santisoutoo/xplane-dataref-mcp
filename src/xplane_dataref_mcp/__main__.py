"""Console entry point.

Logging goes to stderr and stays there. The stdio transport is a JSON-RPC stream
on stdout, so a single stray ``print`` — or a library that logs to stdout by
default — corrupts the protocol and the client drops the connection with an
error that says nothing about where it came from.
"""

from __future__ import annotations

import argparse
import logging
import sys

from xplane_dataref_mcp.server import mcp

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    """The CLI, as a function so tests can parse flags without starting anything."""
    parser = argparse.ArgumentParser(prog="xplane-dataref-mcp", description=__doc__)
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="stdio for a local MCP client; streamable-http to serve clients over the network.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for --transport streamable-http (ignored for stdio).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port for --transport streamable-http (ignored for stdio).",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> None:
    """Run the server."""
    args = _build_parser().parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=args.log_level.upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    # The run() overloads want no kwargs for stdio, so the call is branched
    # rather than passed a variable transport.
    if args.transport == "streamable-http":
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
