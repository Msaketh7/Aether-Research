r"""Where a person may be sent after signing in.

The API's half of the open-redirect guard. The frontend applies the same rule
to the same `?next=` value, and both halves exist on purpose: the frontend's
protects `router.push`, and this one protects the `Location` header the OAuth
callback emits - which the frontend never sees and therefore cannot check.

A `next` value arrives from the address bar, so whoever sent the link chose it.
Emitting it unchecked is an open redirect: the victim signs in on a page that
really is ours and lands on a page that is not, which is the shape of a
credential-phishing flow wearing our domain's credibility.

The value is **validated, not sanitised**. Anything that is not plainly a path
on the application's own origin becomes the default. Rewriting a bad value into
a good one invites a bypass; rejecting it cannot be bypassed.

The cases, and why "starts with a slash" catches none of the interesting ones:

* `//evil.example` is protocol-relative - an absolute URL on another
  host that begins with a slash.
* `/\evil.example` is folded to the above by browsers that treat a backslash
  as a path separator, which is most of them.
* A newline or control character is how header injection is attempted against
  the `Location` this feeds, and is never legitimate in a path.
"""

from __future__ import annotations

DEFAULT_DESTINATION = "/dashboard"

#: Longer than any real in-app path, and short enough that a redirect target
#: cannot be used to smuggle a payload through the query string.
MAX_DESTINATION_LENGTH = 512


def safe_destination(next_: str | None, fallback: str = DEFAULT_DESTINATION) -> str:
    """The destination, or the default when it is not plainly a local path."""
    if not next_:
        return fallback
    if len(next_) > MAX_DESTINATION_LENGTH:
        return fallback

    # Checked on the raw string before any parsing. Parsing is where a URL
    # library's own leniency would be reintroduced, and this rule exists
    # precisely because browsers are lenient.
    if not next_.startswith("/"):
        return fallback
    if next_.startswith("//") or next_.startswith("/" + chr(92)):
        return fallback
    if chr(92) in next_:
        return fallback
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in next_):
        return fallback

    return next_
