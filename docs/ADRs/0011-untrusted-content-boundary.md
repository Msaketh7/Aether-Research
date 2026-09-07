# ADR 0011: Retrieved content is a type, not a convention

- **Status:** Accepted
- **Date:** 2026-09-07

## Context

The product's core loop is ingesting text written by strangers and reasoning
about it with a model. The threat model calls prompt injection "the
highest-likelihood, highest-impact threat in the system" (§3.1) and specifies the
control: retrieved content is passed as **delimited data, never concatenated
into the instruction section of a prompt**.

That control is usually written down and then broken, because the thing that
breaks it is one line that looks completely ordinary:

```python
prompt = f"Summarise this source: {page.text}"
```

Nothing about that line looks wrong in review. It will be written, in a hurry,
by someone who has read the threat model, six months from now.

The same problem applies to the fetcher. "Validate the URL before fetching" is a
rule that holds until a redirect, a search result, or a second HTTP client
appears — each of which is a place the rule can be forgotten rather than
enforced.

## Decision

**Make the safe path the only path the type system allows.**

### Retrieved text is `UntrustedText`

It is not a `str`. `__str__` raises `TypeError`, so the f-string above fails at
the moment it is written. Getting at the characters requires naming which of two
things you mean:

| Method         | For                                                                          |
| -------------- | ---------------------------------------------------------------------------- |
| `for_prompt()` | a model — delimited, with a standing notice that the contents are data       |
| `expose()`     | storage, hashing, span verification — deliberately awkward to read in a diff |

Sanitisation happens in the constructor, so an unsanitised instance cannot
exist. Content that contains the closing delimiter has it neutralised, so a page
cannot end the data block early and continue in what the model reads as
instructions.

### One HTTP client, four layers of SSRF guard

Every outbound request in `app/sources` goes through `SafeHttpClient`. There is
no second client, and the API-backed tools use the same one — including for
`POST`, because the endpoint that gets exempted "just for the search provider"
is the one that later takes a URL from a config file someone can influence.

1. Scheme allowlist, no credentials in the URL, dangerous ports refused.
2. DNS resolution **before** the request. The hostname is never trusted; the
   resolved addresses are. Numerically encoded addresses (`http://2130706433/`,
   `http://0x7f000001/`, `http://127.1/`, `http://0177.0.0.1/`) are decoded
   with the full `inet_aton` grammar, because `ipaddress.ip_address` rejects
   them and they otherwise fall through to a resolver that accepts them.
3. **Every** resolved address is checked, not the first — a hostile resolver can
   answer with one public address and one private one.
4. The **connected peer address is verified** against the validated set before
   any body is read, which closes the DNS-rebinding window between resolution
   and connection.

Redirects are followed manually and re-validated at every hop. The cookie jar is
emptied before every request.

### Least privilege is a capability object

An agent receives a `Toolbelt`, not a module of functions. Which tools it
contains is decided by the caller's role: the synthesizer's is empty. There is
no shell tool, no filesystem tool and no code-execution tool — not disabled,
absent.

## Consequences

- **Every future agent phase inherits the control.** Phases 9-12 cannot
  accidentally interpolate a source into a prompt, because the type will not let
  them. This is the property that makes the decision worth an ADR: it is
  expensive to reverse and everything downstream depends on it.
- **Calling code is slightly more verbose.** `page.body.for_prompt()` rather
  than `page.body`. That is the entire cost, and it is paid at exactly the call
  sites that need thinking about.
- **`trafilatura` and `defusedxml` are new dependencies.** The first because
  hand-rolled boilerplate removal is a pile of heuristics that is wrong
  differently on every site; the second because arXiv answers in XML and
  `xml.etree` will resolve an external entity pointing at the metadata service —
  an SSRF that bypasses the guard entirely, since the _parser_ makes the request.
- **Politeness costs one request per domain.** `robots.txt` is fetched once per
  origin and cached. Failures are permissive: a site whose robots file 404s has
  not forbidden anything.

### Residual risks, accepted and recorded

- **Sanitisation does not stop a plainly worded injection.** Stripping
  zero-width characters, bidi controls and hidden elements removes the class of
  attack a _reviewer cannot see_. A hostile paragraph in ordinary prose passes
  untouched, and the defence against it is the rest of the threat model's stack
  — delimiting, structured output, verbatim-span citation validation — not this.
- **The peer-address check is skipped when a transport does not report one.**
  Test doubles and exotic transports do not expose `server_addr`. Failing closed
  would make the client untestable; the pre-flight validation and redirect
  re-validation still apply, and the deployment's egress security group forbids
  VPC-CIDR traffic as the outer layer (threat model §3.2).
- **Registrable-domain matching is approximate.** No public-suffix list, so the
  allow/deny lists and diversity checks use a heuristic eTLD+1. Adequate for
  "are these the same place"; not a legally precise answer.
