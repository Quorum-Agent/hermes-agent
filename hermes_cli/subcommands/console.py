"""``hermes console`` subcommand parser."""

from __future__ import annotations

from typing import Callable

from product_identity import PRODUCT_NAME


def build_console_parser(subparsers, *, cmd_console: Callable) -> None:
    """Attach the safe Hermes Console REPL subcommand."""
    console_parser = subparsers.add_parser(
        "console",
        help=f"Open the safe {PRODUCT_NAME} command console",
        description=(
            f"Open a curated {PRODUCT_NAME} command REPL. This is not a raw shell and "
            f"does not expose the full {PRODUCT_NAME} CLI."
        ),
    )
    console_parser.set_defaults(func=cmd_console)
