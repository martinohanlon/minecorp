"""Run the service: python -m minecorp_agent (from the agent/ directory)."""
import logging

import uvicorn

from .config import get_settings

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    uvicorn.run("minecorp_agent.app:app", host=s.host, port=s.port, log_level="info")
