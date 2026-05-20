#!/usr/bin/env python3
"""Entrypoint for the inst-ai-bot MCP server.

Starts a Streamable HTTP MCP server on 0.0.0.0:8002 by default.
Override host/port via MCP_HOST / MCP_PORT.

Auth: Bearer token via Authorization header; token must match
INST_AI_BOT_API_KEY in the repo's .env (same secret as the FastAPI server).
"""
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from video_processor.mcp_server import build_app  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8002"))
    logger.info("Starting inst-ai-bot MCP server on %s:%d (path: /mcp)", host, port)
    uvicorn.run(build_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
