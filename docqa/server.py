"""Entry point for `uvicorn docqa.server:app`."""

import logging

from .main import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = create_app()
