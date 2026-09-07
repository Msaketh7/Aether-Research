"""Stripping the things hostile text hides in.

Threat model 3.1: "content is sanitized: scripts, hidden elements, zero-width
and bidi control characters, and `data:`/`javascript:` URIs are stripped before
the model sees the text."

Each of those is a real technique rather than a checklist item:

* **Zero-width characters** (U+200B-U+200D, U+FEFF, U+2060) are invisible to a
  human reviewing a page and fully visible to a tokenizer. They are how an
  injected instruction hides inside what looks like ordinary prose.
* **Bidi controls** (U+202A-U+202E, U+2066-U+2069) reorder how text *renders*
  without changing its logical order. A span that reads as innocuous to a person
  can be something else entirely to a model - the Trojan Source attack.
* **Tag characters** (U+E0000 block) are a deprecated Unicode range that renders
  as nothing at all and survives most naive filters.
* **`data:` and `javascript:` URIs** are how a link becomes a payload if any
  downstream surface ever renders extracted content.
* **Scripts, styles and hidden elements** carry text a reader never sees, which
  makes them the natural place to put an instruction aimed at a machine.

Sanitising is not a defence against injection on its own - a plainly worded
hostile paragraph passes all of this untouched, and the layered controls in the
threat model exist because of that. What this removes is the class of attack the
*reviewer* cannot see, which is the class that would otherwise never be caught.
"""

from __future__ import annotations

import re
import unicodedata

#: Invisible or direction-controlling characters, removed outright.
_ZERO_WIDTH = (
    "​"  # zero width space
    "‌"  # zero width non-joiner
    "‍"  # zero width joiner
    "⁠"  # word joiner
    "﻿"  # zero width no-break space / BOM
    "­"  # soft hyphen
)
_BIDI = (
    "‪‫‬‭‮"  # embedding / override
    "⁦⁧⁨⁩"  # isolates
    "‎‏"  # LTR / RTL marks
)

_INVISIBLE = re.compile(f"[{_ZERO_WIDTH}{_BIDI}]")

#: Unicode tag characters - render as nothing, survive naive filters.
_TAG_CHARS = re.compile(r"[\U000e0000-\U000e007f]")

#: Dangerous URI schemes, wherever they appear in extracted text.
_DANGEROUS_URI = re.compile(r"\b(?:data|javascript|vbscript|file):[^\s\"'<>]*", re.IGNORECASE)

#: Elements whose text a human reader never sees.
_INVISIBLE_ELEMENTS = re.compile(
    r"<(script|style|noscript|template|iframe|object|embed)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)

#: Elements explicitly hidden from rendering. Matched before the generic tag
#: strip, because their *content* has to go, not just their markup.
_HIDDEN_ELEMENTS = re.compile(
    r"<([a-z0-9]+)\b[^>]*(?:style\s*=\s*[\"'][^\"']*display\s*:\s*none|hidden\b|"
    r"aria-hidden\s*=\s*[\"']true[\"'])[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)

_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")


def sanitize_text(text: str) -> str:
    """Remove invisible characters and dangerous URIs from plain text.

    Applied to every ``UntrustedText`` on construction, so nothing downstream has
    to remember to call it.
    """
    if not text:
        return ""

    # NFKC folds the compatibility forms that let visually identical strings
    # differ byte-wise - which is how a filter gets bypassed by a lookalike.
    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = _INVISIBLE.sub("", cleaned)
    cleaned = _TAG_CHARS.sub("", cleaned)
    cleaned = _DANGEROUS_URI.sub("[removed-uri]", cleaned)

    # Control characters other than tab and newline have no place in prose and
    # are a common way to smuggle structure past a reviewer.
    cleaned = "".join(
        character
        for character in cleaned
        if character in "\t\n" or unicodedata.category(character)[0] != "C"
    )

    cleaned = _TRAILING_SPACE.sub("\n", cleaned)
    cleaned = _EXCESS_BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()


def strip_invisible_markup(html: str) -> str:
    """Remove elements whose content a reader never sees, before extraction.

    Runs *before* the readability extractor rather than after. An extractor
    scores blocks by text density, so a hidden `<div>` stuffed with prose can
    win that contest and become the "main content" of the page. Removing it
    first means the extractor sees what a reader would.
    """
    if not html:
        return ""
    stripped = _COMMENTS.sub(" ", html)
    stripped = _INVISIBLE_ELEMENTS.sub(" ", stripped)
    stripped = _HIDDEN_ELEMENTS.sub(" ", stripped)
    return stripped


def html_to_text(html: str) -> str:
    """A last-resort text extraction, for when the readability pass finds nothing.

    Not a parser and not trying to be one: it exists so that a page the extractor
    cannot make sense of still yields something rather than nothing, clearly
    marked in the result as a fallback so a caller can weigh it accordingly.
    """
    text = strip_invisible_markup(html)
    text = _TAGS.sub(" ", text)
    text = _unescape(text)
    return sanitize_text(re.sub(r"[ \t]{2,}", " ", text))


def _unescape(text: str) -> str:
    from html import unescape

    return unescape(text)
