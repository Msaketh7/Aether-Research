"""The direct answer: what a reader sees first, as something they can come back to.

The stream (``app.workers.events``) is how the answer reaches someone who is
watching. This is how it reaches everyone else - a reload, a link sent to a
colleague, a run opened a week later - and it exists as its own small package
for the same reason the row is its own table: the answer is produced before the
report and outlives a failure to write one.
"""

from app.answers.projection import AnswerProjector
from app.answers.repository import AnswerRecord, AnswerStore
from app.answers.schemas import AnswerResponse, RunAnswer

__all__ = [
    "AnswerProjector",
    "AnswerRecord",
    "AnswerResponse",
    "AnswerStore",
    "RunAnswer",
]
