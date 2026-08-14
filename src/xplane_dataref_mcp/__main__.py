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
from typing import Literal, cast

from xplane_dataref_mcp.server import mcp

__all__ = ["main"]

Transport = Literal["stdio", "streamable-http"]


def main() -> None:
    """Run the server."""
    parser = argparse.ArgumentParser(prog="xplane-dataref-mcp", description=__doc__)
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="stdio for a local client such as Claude Code; streamable-http to serve a network.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=args.log_level.upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    mcp.run(transport=cast(Transport, args.transport))


if __name__ == "__main__":
    main()
