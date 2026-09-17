"""Versioned prompt templates, loaded as data (TDD 6.3, Phase 10).

An agent's instructions are a file, not an f-string at a call site. Three things
follow from that, and each is the reason for a rule below.

**A prompt version means something.** Every model call writes
``prompt_version`` to its ledger row, so a change in output quality can be
attributed to a prompt edit rather than guessed at. That only holds if the
version travels with the text it names, so it is declared inside the file and
read from it - there is no way to send a prompt without one.

**The system section may not be templated.** Substitution is allowed in the user
message and refused in the system instruction, checked when the template is
parsed. A system prompt that varies per call has no stable identity: providers
cannot cache it, and a ``prompt_version`` that names ten thousand different
strings names nothing. Everything that varies is data, and data belongs in the
user turn.

**Retrieved content never reaches a placeholder as a bare string.** Values are
rendered by the caller, and the only way to render retrieved text is
``untrusted_block`` or ``UntrustedText.for_prompt``, both of which sanitise and
delimit it (ADR 0011, threat model 3.1). A template cannot enforce that by
itself; what it does enforce is that every placeholder is filled and every value
is used, so a renamed variable fails here rather than sending a prompt with the
literal ``{{evidence}}`` where the evidence should have been.

Format::

    @version planner/v1
    @system
    You are ...
    @user
    Research question:
    {{question}}

Directives are the three lines above, at column 0. Everything else is the body,
so a template may contain any Markdown, including its own headings.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources

from app.core.errors import AppError
from app.models.base import ChatMessage, MessageRole, Prompt

#: ``{{name}}`` rather than ``$name`` or ``{name}``: a template is Markdown that
#: contains JSON examples, braces and dollar amounts, and each of the other two
#: syntaxes collides with one of them.
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_VERSION = re.compile(r"^[a-z][a-z0-9_]*/v\d+$")

_PACKAGE = "app.agents.prompts"


class PromptTemplateInvalid(AppError):
    """A template file this build cannot use. Raised at load, not at call time."""

    status_code = 500
    code = "prompt_template_invalid"
    message = "An agent's prompt template could not be loaded."


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """One agent's instructions: a stable system turn and a templated user turn."""

    version: str
    system: str
    user: str
    #: Every placeholder the user message contains, so ``render`` can insist on
    #: exactly these - no more, no fewer.
    variables: frozenset[str]

    def render(self, **values: str) -> Prompt:
        """The prompt to send, with every placeholder filled.

        Refuses a missing value and an unused one alike. Both are the same bug -
        the template and its call site have drifted - and both are silent
        otherwise: one sends the model a literal ``{{claims}}``, the other
        quietly drops the claims.
        """
        supplied = frozenset(values)
        missing = self.variables - supplied
        unused = supplied - self.variables
        if missing or unused:
            raise PromptTemplateInvalid(
                "The values given do not match the template's placeholders.",
                context={
                    "template": self.version,
                    "missing": sorted(missing),
                    "unused": sorted(unused),
                },
            )
        body = _PLACEHOLDER.sub(lambda match: values[match.group(1)], self.user)
        return Prompt(
            messages=[ChatMessage(role=MessageRole.USER, content=body)],
            system=self.system,
            version=self.version,
        )


def load_template(name: str) -> PromptTemplate:
    """The template named ``name``, read from ``app/agents/prompts/<name>.md``.

    Cached: templates are package data that cannot change while the process
    runs, and re-reading one per model call would put a file read in the path of
    every agent in every research round.
    """
    return _load(name)


def render(name: str, **values: str) -> Prompt:
    """Load and render in one step, which is what every agent does."""
    return load_template(name).render(**values)


def parse_template(text: str, *, origin: str) -> PromptTemplate:
    """Parse a template's text. Separate from loading so a test can drive it."""
    sections = _split(text, origin=origin)
    version = sections["version"].strip()
    if not _VERSION.fullmatch(version):
        raise PromptTemplateInvalid(
            "A prompt version must look like 'planner/v1'.",
            context={"template": origin, "version": version[:60]},
        )

    system = sections["system"].strip()
    user = sections["user"].strip()
    if not system or not user:
        raise PromptTemplateInvalid(
            "A prompt template needs both a system instruction and a user message.",
            context={"template": origin},
        )
    if _PLACEHOLDER.search(system):
        raise PromptTemplateInvalid(
            "The system instruction may not contain placeholders; see the module docstring.",
            context={
                "template": origin,
                "placeholders": sorted(set(_PLACEHOLDER.findall(system))),
            },
        )
    return PromptTemplate(
        version=version,
        system=system,
        user=user,
        variables=frozenset(_PLACEHOLDER.findall(user)),
    )


def template_names() -> list[str]:
    """Every declared template, for the test that parses all of them."""
    return sorted(
        entry.name.removesuffix(".md")
        for entry in resources.files(_PACKAGE).iterdir()
        if entry.name.endswith(".md")
    )


@cache
def _load(name: str) -> PromptTemplate:
    source = resources.files(_PACKAGE).joinpath(f"{name}.md")
    if not source.is_file():
        raise PromptTemplateInvalid(
            "No prompt template by that name is installed.",
            context={"template": name, "declared": template_names()},
        )
    return parse_template(source.read_text(encoding="utf-8"), origin=name)


def _split(text: str, *, origin: str) -> Mapping[str, str]:
    """Cut the file at its directive lines, in the order they must appear."""
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("@version "):
            found["version"] = [line.removeprefix("@version ")]
            current = None
        elif line.rstrip() in ("@system", "@user"):
            current = found.setdefault(line.rstrip().removeprefix("@"), [])
        elif current is not None:
            current.append(line)

    missing = {"version", "system", "user"} - set(found)
    if missing:
        raise PromptTemplateInvalid(
            "A prompt template needs @version, @system and @user.",
            context={"template": origin, "missing": sorted(missing)},
        )
    return {key: "\n".join(lines) for key, lines in found.items()}
