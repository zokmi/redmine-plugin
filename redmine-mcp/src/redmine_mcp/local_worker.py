"""Private HTTP worker owned by the Node.js local service."""

import os
import sys
from threading import Thread

from redmine_mcp.__main__ import main


def exit_when_parent_closes() -> None:
    # The parent owns the write end and sends no data. EOF also covers abrupt exits.
    try:
        os.read(sys.stdin.fileno(), 1)
    finally:
        os._exit(0)


if __name__ == "__main__":
    Thread(target=exit_when_parent_closes, daemon=True).start()
    main(
        local_token=os.environ.pop("REDMINE_MCP_WORKER_TOKEN"),
        port=0,
        ready=lambda port: print(port, flush=True),
    )
