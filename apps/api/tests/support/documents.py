"""Documents for the ingestion tests, built in code rather than checked in.

Built rather than stored so each fixture is readable in the test that uses it,
and so a PDF's exact text - which the offset assertions depend on - is stated
beside the assertion instead of hidden inside a binary file.

The PDF writer is deliberately minimal: one Type 1 font, one text object per
page, a correct cross-reference table. That is enough for pypdf to extract
exactly the lines given, and small enough to read.
"""

from __future__ import annotations

import io

_FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"

#: Sentences for generated prose. Repeats are intended: repeated text is what
#: makes offset resolution hard, so the fixtures should contain it.
SENTENCES = (
    "Nvidia reported data centre revenue of 26.3 billion dollars for the quarter.",
    "Analysts expect inference workloads to outgrow training within two years.",
    "The company said export controls would reduce shipments to some regions.",
    "Pricing for hosted inference fell by roughly half over the same period.",
    "Several start-ups announced custom accelerators aimed at lower latency.",
    "Memory bandwidth, not raw compute, now limits most serving deployments.",
    "Cloud providers are signing multi-year capacity contracts with chipmakers.",
    "Independent benchmarks disagree about the size of the efficiency gains.",
    "A regulatory filing listed supply constraints among the principal risks.",
    "Management guided to higher margins on the strength of software revenue.",
)


def prose(paragraphs: int, *, sentences_per_paragraph: int = 5, offset: int = 0) -> str:
    """Deterministic English prose: ``paragraphs`` paragraphs, blank-line separated."""
    return "\n\n".join(
        " ".join(
            SENTENCES[(offset + paragraph * 7 + sentence * 3) % len(SENTENCES)]
            for sentence in range(sentences_per_paragraph)
        )
        for paragraph in range(paragraphs)
    )


def _pdf_string(value: str) -> bytes:
    return (
        value.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    )


def make_pdf(
    pages: list[str],
    *,
    title: str | None = None,
    author: str | None = None,
    lang: str | None = None,
) -> bytes:
    """A PDF whose page ``i`` shows ``pages[i]``, one text line per ``\\n``.

    An empty string makes a page with no text at all - a figure, or a scan.
    """
    objects: dict[int, bytes] = {3: _FONT}
    next_id = 4

    info_id: int | None = None
    if title or author:
        info_id = next_id
        next_id += 1
        fields = []
        if title:
            fields.append(b"/Title (" + _pdf_string(title) + b")")
        if author:
            fields.append(b"/Author (" + _pdf_string(author) + b")")
        objects[info_id] = b"<< " + b" ".join(fields) + b" >>"

    kids: list[int] = []
    for text in pages:
        operations = [b"BT", b"/F1 12 Tf", b"14 TL", b"72 720 Td"]
        if text:
            operations += [b"(" + _pdf_string(line) + b") Tj T*" for line in text.split("\n")]
        operations.append(b"ET")
        stream = b"\n".join(operations)

        content_id = next_id
        next_id += 1
        objects[content_id] = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        page_id = next_id
        next_id += 1
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + str(content_id).encode()
            + b" 0 R >>"
        )
        kids.append(page_id)

    catalog = b"<< /Type /Catalog /Pages 2 0 R"
    if lang:
        catalog += b" /Lang (" + _pdf_string(lang) + b")"
    objects[1] = catalog + b" >>"
    objects[2] = (
        b"<< /Type /Pages /Kids ["
        + b" ".join(str(kid).encode() + b" 0 R" for kid in kids)
        + b"] /Count "
        + str(len(kids)).encode()
        + b" >>"
    )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for number in range(1, next_id):
        offsets[number] = out.tell()
        out.write(str(number).encode() + b" 0 obj\n" + objects[number] + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 " + str(next_id).encode() + b"\n0000000000 65535 f \n")
    for number in range(1, next_id):
        out.write(f"{offsets[number]:010d} 00000 n \n".encode())
    trailer = b"<< /Size " + str(next_id).encode() + b" /Root 1 0 R"
    if info_id is not None:
        trailer += b" /Info " + str(info_id).encode() + b" 0 R"
    out.write(b"trailer\n" + trailer + b" >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return out.getvalue()


def encrypt_pdf(data: bytes, *, owner_only: bool = False) -> bytes:
    """``data`` encrypted with RC4-128, which pypdf handles without optional crypto.

    ``owner_only`` restricts editing but not reading: the file opens with an
    empty user password, as many real reports do.
    """
    from pypdf import PdfReader, PdfWriter

    lock = "fixture-lock"
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(
        user_password=("" if owner_only else lock), owner_password=lock, algorithm="RC4-128"
    )
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def article_html(
    body: str,
    *,
    title: str = "Inference pricing in 2026",
    hidden: str | None = None,
    lang: str = "en",
    published: str | None = None,
) -> bytes:
    """A news page: navigation and footer around an article, optionally with a hidden block.

    ``hidden`` goes in a ``display:none`` div inside the article - the place an
    injection aimed at a model rather than a reader would be put.
    """
    hidden_block = f'<div style="display:none">{hidden}</div>' if hidden else ""
    date_meta = (
        f'<meta property="article:published_time" content="{published}">' if published else ""
    )
    paragraphs = "".join(f"<p>{paragraph}</p>" for paragraph in body.split("\n\n"))
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        f"<title>{title}</title>{date_meta}</head><body>"
        "<nav>Home | Pricing | Careers | About us</nav>"
        f"<article><h1>{title}</h1>{hidden_block}{paragraphs}</article>"
        "<footer>Copyright 2026. All rights reserved.</footer></body></html>"
    ).encode()
