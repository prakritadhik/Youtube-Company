"""Importing this module registers every approval handler and task handler exactly once."""

from app.analytics import handlers as analytics_handlers  # noqa: F401  (side effect: collection and reply task handlers)
from app.community import replies as community_replies  # noqa: F401  (side effect: the reply approval handler)
from app.improvement import handlers as improvement_handlers  # noqa: F401  (side effect: the lesson-mining task handler)
from app.ops import cycle as ops_cycle  # noqa: F401  (side effect: scheduled operations handlers)
from app.production import narration, pipeline  # noqa: F401  (side effect: production task handlers)
from app.research import workflow  # noqa: F401  (side effect: research and packaging task handlers)
from app.services import board, tasks, videos  # noqa: F401  (side effect: approval handlers)
from app.worker import handlers  # noqa: F401  (side effect: task handlers)
from app.youtube import handlers as youtube_handlers  # noqa: F401  (side effect: the upload task handler)


def load() -> None:
    return None
