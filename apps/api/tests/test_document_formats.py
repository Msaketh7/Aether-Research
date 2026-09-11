"""Format detection: the label is a claim, and the bytes decide (threat model 3.6)."""

from __future__ import annotations

import pytest

from app.core.enums import DocumentFormat
from app.retrieval.errors import (
    DocumentFormatMismatch,
    EmptyDocument,
    UnsupportedDocumentFormat,
    UnsupportedTextEncoding,
)
from app.retrieval.formats import (
    decode_text,
    detect_format,
    display_filename,
    parse_content_type,
)
from tests.support.documents import make_pdf

PDF = make_pdf(["A page of text."])


@pytest.mark.parametrize(
    ("content_type", "filename"),
    [
        ("application/pdf", "report.pdf"),
        ("application/octet-stream", "report.pdf"),
        ("application/pdf; charset=binary", None),
        (None, None),
    ],
)
def test_a_pdf_is_recognised_by_its_bytes(content_type, filename):
    detected = detect_format(PDF, content_type=content_type, filename=filename)
    assert detected.format is DocumentFormat.PDF
    assert detected.media_type == "application/pdf"


def test_a_pdf_header_padded_past_byte_zero_is_still_a_pdf():
    """Readers accept the header anywhere in the first KiB, so the detector must
    too - or a padded PDF labelled text/plain walks around the mismatch check."""
    padded = b" " * 500 + PDF
    assert detect_format(padded, content_type=None, filename=None).format is DocumentFormat.PDF
    with pytest.raises(DocumentFormatMismatch):
        detect_format(padded, content_type="text/plain", filename=None)


@pytest.mark.parametrize(
    ("data", "content_type", "filename"),
    [
        (b"<html><body>not a pdf</body></html>", "application/pdf", None),
        (PDF, "text/plain", None),
        (PDF, "text/markdown", None),
        (b"plain words, not a pdf", None, "invoice.pdf"),
    ],
)
def test_a_label_that_disagrees_with_the_bytes_is_refused(data, content_type, filename):
    with pytest.raises(DocumentFormatMismatch) as caught:
        detect_format(data, content_type=content_type, filename=filename)
    assert caught.value.status_code == 415


def test_an_unsupported_type_is_refused_by_name():
    with pytest.raises(UnsupportedDocumentFormat) as caught:
        detect_format(b"\x89PNG\r\n\x1a\n...", content_type="image/png", filename="a.png")
    assert caught.value.status_code == 415


def test_a_binary_file_labelled_as_text_is_refused_and_named():
    """The refusal says what the file really is - an answer a user can act on."""
    with pytest.raises(UnsupportedDocumentFormat) as caught:
        detect_format(b"PK\x03\x04 rest of a docx", content_type="text/plain", filename=None)
    assert "ZIP" in caught.value.message


def test_a_nul_byte_marks_a_file_as_binary():
    with pytest.raises(UnsupportedDocumentFormat):
        detect_format(b"looks like text\x00then binary", content_type="text/plain", filename=None)


def test_an_empty_file_is_refused():
    with pytest.raises(EmptyDocument):
        detect_format(b"", content_type="text/plain", filename=None)


@pytest.mark.parametrize(
    ("data", "content_type", "filename", "expected"),
    [
        (b"# Title\n\nBody", "text/markdown", None, DocumentFormat.MARKDOWN),
        (b"# Title\n\nBody", "application/octet-stream", "notes.md", DocumentFormat.MARKDOWN),
        (b"# Title\n\nBody", "text/plain", "notes.md", DocumentFormat.TEXT),
        (b"<!DOCTYPE html><html><body>x</body></html>", None, None, DocumentFormat.HTML),
        (b"Just some prose.", None, None, DocumentFormat.TEXT),
    ],
)
def test_the_label_decides_which_text_format(data, content_type, filename, expected):
    """No sniffing tells Markdown from prose, so the label wins among text formats."""
    assert detect_format(data, content_type=content_type, filename=filename).format is expected


def test_a_utf16_file_is_decoded_rather_than_mistaken_for_binary():
    """UTF-16 puts a NUL beside every ASCII letter; the byte-order mark says so."""
    data = "café prices".encode("utf-16")
    assert detect_format(data, content_type="text/plain", filename=None).format is (
        DocumentFormat.TEXT
    )
    assert decode_text(data, charset=None, fmt=DocumentFormat.TEXT) == "café prices"


def test_a_utf8_bom_is_removed():
    data = b"\xef\xbb\xbfhello"
    assert decode_text(data, charset=None, fmt=DocumentFormat.TEXT) == "hello"


def test_a_declared_charset_is_honoured():
    data = "café".encode("latin-1")
    assert decode_text(data, charset="latin-1", fmt=DocumentFormat.TEXT) == "café"


def test_an_html_meta_charset_is_honoured():
    data = b'<html><head><meta charset="windows-1252"></head><body>caf\xe9</body></html>'
    assert "café" in decode_text(data, charset=None, fmt=DocumentFormat.HTML)


def test_text_that_is_not_utf8_and_declares_nothing_is_refused_not_guessed():
    """A wrong guess becomes mojibake in a quoted evidence span."""
    with pytest.raises(UnsupportedTextEncoding):
        decode_text("café".encode("latin-1"), charset=None, fmt=DocumentFormat.TEXT)


def test_a_bytes_to_bytes_codec_cannot_be_smuggled_in_as_a_charset():
    with pytest.raises(UnsupportedTextEncoding):
        decode_text(b"\xff\xfe\xfd", charset="base64", fmt=DocumentFormat.TEXT)


def test_content_type_parameters_are_parsed():
    assert parse_content_type('Text/Plain; Charset="UTF-8"') == ("text/plain", "utf-8")
    assert parse_content_type(None) == ("", None)


def test_a_filename_is_cleaned_for_display():
    assert display_filename("..\\..\\windows\\boot.ini", DocumentFormat.TEXT) == "boot.ini"
    assert display_filename("../../etc/passwd.txt", DocumentFormat.TEXT) == "passwd.txt"
    # A right-to-left override makes "invoice[RLO]fdp.exe" render as a PDF name.
    assert display_filename("invoice" + chr(0x202E) + "fdp.exe", DocumentFormat.PDF) == (
        "invoicefdp.exe"
    )
    assert display_filename(None, DocumentFormat.PDF) == "upload.pdf"
    assert display_filename("   ", DocumentFormat.MARKDOWN) == "upload.md"
    assert len(display_filename("a" * 1000 + ".txt", DocumentFormat.TEXT)) == 200
