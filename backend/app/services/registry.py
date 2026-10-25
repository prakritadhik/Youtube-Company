"""Importing this module registers every approval handler and task handler exactly once."""

from app.production import narration, pipeline  # noqa: F401  (side effect: production task handlers)
from app.services import board, tasks, videos  # noqa: F401  (side effect: approval handlers)
from app.worker import handlers  # noqa: F401  (side effect: task handlers)


def load() -> None:
    return None
