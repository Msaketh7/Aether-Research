# @aether/prompts

**The templates live in `apps/api/app/agents/prompts/`.** This package is the
place the repository layout reserves for them; nothing was put here, because
nothing but the API reads a prompt and the API's wheel packages `app` and
nothing else. Templates outside it would be present in a checkout and absent
from the deployed image, and the failure would be a worker that starts, takes a
run, and cannot build a prompt. See [ADR 0015](../../docs/ADRs/0015-agent-outputs.md).

## The contract, as implemented

- One Markdown file per agent step, read by `app/agents/prompting.py`.
- Three directives at column 0: `@version`, `@system`, `@user`. Everything else
  is body, so a template may contain any Markdown including its own headings.
- Every template carries a semantic version (`planner/v1`), written to the
  `llm_calls` row for every call it produces, so a change in output quality can
  be attributed to a prompt edit. No two templates may share one.
- **The system instruction may not be templated.** Substitution is allowed in
  the user message only, and the parser refuses a placeholder in the system
  section: a prompt whose identity varies per call cannot be cached
  provider-side and makes `prompt_version` meaningless.
- Rendering refuses a missing value and an unused one alike. Both are the same
  drift between a template and its call site.
- Retrieved content reaches a prompt only through `untrusted_block` or
  `UntrustedText.for_prompt`, which sanitise and delimit it. See the
  [threat model](../../docs/threat-model.md) and
  [ADR 0011](../../docs/ADRs/0011-untrusted-content-boundary.md).

If a second consumer of prompts ever appears - an evaluation harness that needs
to render them outside the API process - this is where the shared copy goes, and
the API reads it from here.
