import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    """Configure centralized logging for Nira."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
