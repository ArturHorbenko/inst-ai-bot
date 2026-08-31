#!/usr/bin/env python3
"""Run the single-user OAuth authorization server on port 8003."""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from video_processor.oauth_server import build_production_app  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    host = os.environ.get("OAUTH_HOST", "127.0.0.1")
    port = int(os.environ.get("OAUTH_PORT", "8003"))
    uvicorn.run(build_production_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
