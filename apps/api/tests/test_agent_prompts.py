"""The prompt library: what it refuses, and what every shipped template must be.

The interesting tests here are the ones that run over *every* template rather
than over a fixture. A template is data, and data that ships in the wheel is
exactly the kind of thing that breaks quietly - a renamed placeholder, a version
not bumped, a stray substitution in a system prompt - so the suite asserts the
properties across the set rather than one file at a time.
"""

from __future__ import annotations

import pytest

from app.agents import prompting
from app.agents.prompting import (
    PromptTemplateInvalid,
    load_template,
    parse_template,
    render,
    template_names,
)

MINIMAL = "@version planner/v1\n@system\nBe careful.\n@user\nAnswer {{question}}.\n"


# --- what every shipped template must be ------------------------------------------


def test_every_shipped_template_parses():
    names = template_names()
    assert len(names) >= 9, "one template per agent step, at least"
    for name in names:
        assert load_template(name).version


def test_no_two_templates_share_a_version():
    """A version identifies a prompt in the call ledger, so it cannot be shared.

    Two templates at ``planner/v1`` would make every quality question about
    either of them unanswerable from the ledger alone.
    """
    versions = [load_template(name).version for name in template_names()]
    assert sorted(versions) == sorted(set(versions))


def test_no_system_instruction_is_templated():
    """Checked across the set, not just at parse time.

    The parser refuses a placeholder in a system prompt; this asserts that no
    shipped template has one, which is what keeps ``prompt_version`` meaningful
    and provider-side caching possible.
    """
    for name in template_names():
        template = load_template(name)
        assert "{{" not in template.system


def test_every_template_tells_the_model_to_return_the_structured_object():
    """Each agent asks for a schema, and the instruction has to match the ask."""
    for name in template_names():
        system = load_template(name).system.lower()
        assert "structured object" in system, name


#: Templates whose variables can carry a delimited block of retrieved text -
#: which is every one except the two that are given only a subtask to turn into
#: queries. Listed rather than derived, so that adding a template that reads
#: retrieved material is a decision someone makes here on purpose.
CARRY_RETRIEVED_TEXT = [
    "planner",
    "researcher_select",
    "evidence",
    "claims",
    "verifier",
    "contradictions",
    "critic",
    "synthesizer",
]


@pytest.mark.parametrize("name", CARRY_RETRIEVED_TEXT)
def test_templates_that_carry_retrieved_text_warn_about_it(name):
    """The belt-and-braces half of ADR 0011.

    The data block beside the content already says the content is data. The
    system prompt says it again, because an instruction thousands of tokens away
    from hostile text is weaker than one next to it - and because the wording is
    then the same everywhere a model meets it.
    """
    # Line wrapping in a Markdown file is not part of the instruction, so the
    # assertion is made on the words rather than on how they were laid out.
    system = " ".join(load_template(name).system.lower().split())
    assert "never instructions" in system or "never as instructions" in system
    assert "do not comply" in system


# --- what the parser refuses -------------------------------------------------------


def test_a_template_without_a_version_is_refused():
    with pytest.raises(PromptTemplateInvalid):
        parse_template("@system\nBe careful.\n@user\nAnswer.\n", origin="broken")


def test_a_version_that_is_not_a_version_is_refused():
    with pytest.raises(PromptTemplateInvalid) as caught:
        parse_template(MINIMAL.replace("planner/v1", "the good one"), origin="broken")
    assert caught.value.context["version"] == "the good one"


def test_a_templated_system_instruction_is_refused_at_parse_time():
    """Not at call time. A prompt whose identity varies per call is a defect in
    the file, and the file is read once - so it fails once, at load."""
    with pytest.raises(PromptTemplateInvalid) as caught:
        parse_template(MINIMAL.replace("Be careful.", "Be careful, {{tone}}."), origin="broken")
    assert caught.value.context["placeholders"] == ["tone"]


def test_a_template_missing_a_section_is_refused():
    with pytest.raises(PromptTemplateInvalid) as caught:
        parse_template("@version planner/v1\n@system\nBe careful.\n", origin="broken")
    assert caught.value.context["missing"] == ["user"]


def test_an_unknown_template_names_the_ones_that_exist():
    with pytest.raises(PromptTemplateInvalid) as caught:
        load_template("no_such_agent")
    assert "planner" in caught.value.context["declared"]


# --- rendering ---------------------------------------------------------------------


def test_rendering_substitutes_and_keeps_the_system_prompt_intact():
    template = parse_template(MINIMAL, origin="fixture")
    prompt = template.render(question="what is the price")

    assert prompt.system == "Be careful."
    assert prompt.version == "planner/v1"
    assert prompt.messages[0].content == "Answer what is the price."


def test_a_missing_value_is_refused_rather_than_sent_as_a_placeholder():
    """The failure this prevents is silent: a prompt containing the literal
    ``{{question}}`` reads to a model as a strange but answerable request."""
    template = parse_template(MINIMAL, origin="fixture")
    with pytest.raises(PromptTemplateInvalid) as caught:
        template.render()
    assert caught.value.context["missing"] == ["question"]


def test_a_value_the_template_does_not_use_is_refused():
    """The other half of the same drift: the caller thinks it passed the
    evidence, and the template no longer has anywhere to put it."""
    template = parse_template(MINIMAL, origin="fixture")
    with pytest.raises(PromptTemplateInvalid) as caught:
        template.render(question="q", evidence="the evidence that never arrived")
    assert caught.value.context["unused"] == ["evidence"]


def test_a_substituted_value_is_never_re_scanned_for_placeholders():
    """A value that happens to contain ``{{x}}`` must not become a placeholder.

    Retrieved text reaches these values. A page that writes ``{{claims}}`` into
    its own body must not be able to make the renderer look for a variable, and
    a single pass over the template is what guarantees it cannot.
    """
    template = parse_template(MINIMAL, origin="fixture")
    prompt = template.render(question="{{question}} and {{secret}}")
    assert prompt.messages[0].content == "Answer {{question}} and {{secret}}."


def test_templates_are_cached_so_a_round_of_agents_does_not_re_read_them():
    first = load_template("planner")
    assert load_template("planner") is first


def test_render_is_load_and_render_in_one_step():
    prompt = render(
        "researcher_queries",
        question="q",
        subtask="s",
        constraints="none",
        max_queries="3",
    )
    assert prompt.version == "researcher_queries/v1"
    assert "max_queries" not in prompt.messages[0].content


def test_the_package_is_the_only_place_templates_are_read_from():
    """A guard on the deployment decision, not on the code.

    Templates ship inside ``app`` so that the wheel carries them (ADR 0015). If
    someone moves them out, this is what says so.
    """
    assert prompting._PACKAGE == "app.agents.prompts"
