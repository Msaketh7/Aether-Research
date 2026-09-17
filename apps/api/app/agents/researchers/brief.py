"""The parts of a researcher's prompt that every channel writes the same way.

A subtask and the run's restrictions are this system's own words - a planner
wrote the question, a user chose the domains - so they go into a prompt as
ordinary text. Retrieved material never does; that is ``untrusted_block``'s job
and it lives next to the content it wraps.

Shared here rather than imported across researchers so that no channel reaches
into another's private helpers to get them.
"""

from __future__ import annotations

from app.agents.schemas import SubtaskAssignment


def subtask_brief(assignment: SubtaskAssignment) -> str:
    """The subtask as the researcher is asked to read it."""
    subtask = assignment.subtask
    lines = [f"{subtask.question} [{subtask.key}, priority {subtask.priority.value}]"]
    if subtask.rationale:
        lines.append(f"Why it was asked: {subtask.rationale}")
    return "\n".join(lines)


def constraints(assignment: SubtaskAssignment) -> str:
    """The run's domain and date restrictions, stated rather than implied.

    The domain filter is applied by the search tool, so the model is told not to
    repeat it in the query text: a query that also spells out `site:` narrows
    twice and usually to nothing.
    """
    lines: list[str] = []
    if assignment.domains:
        lines.append(
            "Results are restricted to these domains, so do not repeat them in the "
            f"query text: {', '.join(assignment.domains)}"
        )
    if assignment.date_range_start or assignment.date_range_end:
        start = assignment.date_range_start or "any"
        end = assignment.date_range_end or "any"
        lines.append(f"The run is interested in the period {start} to {end}.")
    return "\n".join(lines) or "No domain or date restrictions apply."
