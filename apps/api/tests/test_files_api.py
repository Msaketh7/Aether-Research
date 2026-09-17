"""The file API - upload, list, fetch - and the ``document_ids`` a run is created with.

Every test runs the real application over HTTP against Postgres and the
filesystem object store, like the rest of the API suite.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.db.models.source import DocumentRow, SourceRow
from app.db.models.upload import ResearchRunUploadRow, UploadRow
from app.main import create_app
from app.retrieval.attached import AttachedUploadIngestion
from app.sources.credibility import TIER_SCORES
from app.sources.schemas import SourceCredibility
from tests.conftest import API, BASE_URL, as_user, valid_request
from tests.support.documents import encrypt_pdf, make_pdf, prose
from tests.support.ingestion import in_process_ingestor

PDF = make_pdf([prose(2), prose(2, offset=4)], title="Quarterly filing")


async def upload(
    client: AsyncClient,
    data: bytes,
    *,
    content_type: str = "application/pdf",
    filename: str | None = "filing.pdf",
    headers: dict[str, str] | None = None,
):
    request_headers = {"Content-Type": content_type}
    if filename is not None:
        request_headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    request_headers.update(headers or {})
    return await client.post(f"{API}/files", content=data, headers=request_headers)


@pytest.fixture
async def small_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """An app whose upload ceiling is one KiB, so the limit is cheap to reach."""
    app = create_app(settings.model_copy(update={"max_upload_bytes": 1024}))
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as http,
        app.router.lifespan_context(app),
    ):
        yield http


# --- uploading ----------------------------------------------------------------


async def test_an_upload_is_stored_and_described(client, database):
    response = await upload(client, PDF)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["format"] == "pdf"
    assert body["mime_type"] == "application/pdf"
    assert body["size_bytes"] == len(PDF)
    assert body["content_hash"] == hashlib.sha256(PDF).hexdigest()
    assert body["filename"] == "filing.pdf"

    async with database.session() as session:
        row = await session.get(UploadRow, uuid.UUID(body["id"]))
    assert row is not None
    assert row.storage_key.startswith(f"uploads/{row.user_id}/pdf/")


async def test_the_same_file_again_is_the_same_upload(client):
    first = await upload(client, PDF)
    second = await upload(client, PDF, filename="renamed.pdf")

    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["id"] == first.json()["id"]


async def test_two_users_uploading_one_file_get_separate_uploads(client, other_user_id):
    """Identity is per user. A global key would tell one user that another had
    already uploaded a given confidential file."""
    mine = await upload(client, PDF)
    theirs = await upload(client, PDF, headers=as_user(other_user_id))

    assert theirs.status_code == 201
    assert theirs.json()["id"] != mine.json()["id"]


@pytest.mark.parametrize(
    ("data", "content_type", "filename", "expected"),
    [
        (b"# Notes\n\nMarkdown body.", "text/markdown", "notes.md", "markdown"),
        (b"Plain notes.", "text/plain; charset=utf-8", "notes.txt", "text"),
        (b"<html><body><p>Page</p></body></html>", "text/html", "page.html", "html"),
    ],
)
async def test_each_supported_format_is_accepted(client, data, content_type, filename, expected):
    response = await upload(client, data, content_type=content_type, filename=filename)
    assert response.status_code == 201, response.text
    assert response.json()["format"] == expected


async def test_a_declared_length_over_the_ceiling_is_refused_before_reading(small_client):
    response = await upload(small_client, b"x" * 2048, content_type="text/plain")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "document_too_large"


async def test_a_streamed_body_is_cut_off_at_the_ceiling(small_client):
    """No Content-Length: the bytes are counted as they arrive."""

    async def body() -> AsyncIterator[bytes]:
        for _ in range(4):
            yield b"a" * 600

    response = await small_client.post(
        f"{API}/files", content=body(), headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 413


@pytest.mark.parametrize(
    ("data", "content_type", "status", "code"),
    [
        (b"\x89PNG\r\n\x1a\n rest", "image/png", 415, "unsupported_document_format"),
        (b"<html>not a pdf</html>", "application/pdf", 415, "document_format_mismatch"),
        (b"", "text/plain", 422, "empty_document"),
        ("café costs".encode("latin-1"), "text/plain", 415, "unsupported_text_encoding"),
    ],
)
async def test_a_bad_upload_is_refused_with_its_reason(client, data, content_type, status, code):
    response = await upload(client, data, content_type=content_type, filename=None)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def test_a_declared_charset_makes_non_utf8_text_acceptable(client):
    response = await upload(
        client,
        "café costs".encode("latin-1"),
        content_type="text/plain; charset=latin-1",
        filename="menu.txt",
    )
    assert response.status_code == 201


async def test_a_non_ascii_filename_sent_the_rfc_5987_way_is_decoded(client):
    response = await client.post(
        f"{API}/files",
        content=b"# N\n\nbody",
        headers={
            "Content-Type": "text/markdown",
            "Content-Disposition": "attachment; filename*=UTF-8''na%C3%AFve%20report.md",
        },
    )
    assert response.json()["filename"] == "naïve report.md"


async def test_a_filename_cannot_carry_a_path(client):
    response = await upload(
        client, b"plain", content_type="text/plain", filename="../../etc/passwd.txt"
    )
    assert response.json()["filename"] == "passwd.txt"


# --- reading ------------------------------------------------------------------


async def test_uploads_are_listed_newest_first_one_page_at_a_time(client):
    ids = []
    for number in range(3):
        response = await upload(
            client, f"File number {number}.".encode(), content_type="text/plain"
        )
        ids.append(response.json()["id"])

    first = (await client.get(f"{API}/files", params={"limit": 2})).json()
    assert [item["id"] for item in first["items"]] == [ids[2], ids[1]]

    second = (
        await client.get(f"{API}/files", params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()
    assert [item["id"] for item in second["items"]] == [ids[0]]
    assert second["next_cursor"] is None


async def test_another_users_upload_is_indistinguishable_from_a_missing_one(client, other_user_id):
    mine = (await upload(client, PDF)).json()

    response = await client.get(f"{API}/files/{mine['id']}", headers=as_user(other_user_id))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "upload_not_found"

    listed = (await client.get(f"{API}/files", headers=as_user(other_user_id))).json()
    assert listed["items"] == []


@pytest.mark.parametrize("endpoint", ["/files", "/research"])
async def test_a_cursor_that_decodes_to_nonsense_is_a_validation_error(client, endpoint):
    """Base64 of any string is a well-formed cursor. It used to reach UUID()
    unguarded and surface as a 500 - on the run history as well."""
    cursor = base64.urlsafe_b64encode(b"not-a-uuid").decode().rstrip("=")
    response = await client.get(f"{API}{endpoint}", params={"cursor": cursor})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# --- document_ids on run creation --------------------------------------------


async def test_a_run_can_only_be_created_with_the_callers_own_uploads(client, other_user_id):
    """Someone else's upload is reported exactly like one that does not exist."""
    theirs = (await upload(client, PDF, headers=as_user(other_user_id))).json()["id"]

    for document_id in (theirs, str(uuid.uuid4())):
        response = await client.post(
            f"{API}/research", json=valid_request(document_ids=[document_id])
        )
        assert response.status_code == 422
        assert response.json()["error"]["details"]["document_ids"] == [
            f"No uploaded document with id {document_id}."
        ]


async def test_attached_uploads_are_recorded_with_the_run(client, database):
    """Until Phase 7, ``document_ids`` was accepted and silently dropped."""
    upload_id = (await upload(client, PDF)).json()["id"]

    response = await client.post(
        f"{API}/research", json=valid_request(document_ids=[upload_id, upload_id])
    )

    assert response.status_code == 202, response.text
    run_id = uuid.UUID(response.json()["run_id"])
    async with database.session() as session:
        attached = (
            await session.execute(
                sa.select(ResearchRunUploadRow.upload_id).where(
                    ResearchRunUploadRow.run_id == run_id
                )
            )
        ).scalars()
        assert list(attached) == [uuid.UUID(upload_id)]


async def test_more_than_ten_documents_is_refused(client):
    response = await client.post(
        f"{API}/research",
        json=valid_request(document_ids=[str(uuid.uuid4()) for _ in range(11)]),
    )
    assert response.status_code == 422


# --- end to end: upload, attach, ingest ----------------------------------------


async def create_run_with(client: AsyncClient, *upload_ids: str) -> tuple[uuid.UUID, uuid.UUID]:
    response = await client.post(f"{API}/research", json=valid_request(document_ids=[*upload_ids]))
    assert response.status_code == 202, response.text
    run_id = uuid.UUID(response.json()["run_id"])
    run = (await client.get(f"{API}/research/{run_id}")).json()
    return run_id, uuid.UUID(run["user_id"])


async def test_an_attached_upload_becomes_a_source_of_its_run(client, database, artifact_store):
    upload_id = (await upload(client, PDF)).json()["id"]
    run_id, user_id = await create_run_with(client, upload_id)

    # What the worker will do when it starts the run (Phase 13).
    ingestion = AttachedUploadIngestion(
        database=database,
        storage=artifact_store,
        ingestor=in_process_ingestor(database, artifact_store),
    )
    results = await ingestion.ingest_run(run_id, user_id=user_id)

    assert [(result.upload_id, result.error_code) for result in results] == [
        (uuid.UUID(upload_id), None)
    ]
    outcome = results[0].outcome
    assert outcome is not None
    async with database.session() as session:
        source = await session.get(SourceRow, outcome.source_id)
        document = await session.get(DocumentRow, outcome.document_id)
    assert source is not None
    assert document is not None
    assert (source.run_id, source.source_type) == (run_id, "upload")
    assert source.title == "Quarterly filing"
    assert source.url == "upload://filing.pdf"
    # The origin of an upload is the person who asked: it is the document itself
    # rather than commentary about it, and nothing in the system has assessed it.
    # The shape is exactly what the `Source` DTO parses, which is what stopped
    # the sources endpoint failing on its first real row (Phase 11).
    assert SourceCredibility.model_validate(source.credibility_metadata) == SourceCredibility(
        is_primary=True,
        tier="unknown",
        domain_reputation=TIER_SCORES["unknown"],
        notes="Supplied with the request",
    )
    assert float(source.credibility_score) == TIER_SCORES["unknown"]
    assert source.relevance_score is None, "no relevance has been measured for it"
    assert document.storage_key is not None
    assert document.storage_key.startswith(f"runs/{run_id}/pdf/")
    assert (await client.get(f"{API}/research/{run_id}")).json()["source_count"] == 1


async def test_an_unreadable_attachment_is_skipped_and_the_others_ingested(
    client, database, artifact_store
):
    """One bad file costs the run that file, not the run."""
    good = (await upload(client, PDF)).json()["id"]
    locked = (await upload(client, encrypt_pdf(PDF), filename="locked.pdf")).json()["id"]
    run_id, user_id = await create_run_with(client, good, locked)

    results = await AttachedUploadIngestion(
        database=database,
        storage=artifact_store,
        ingestor=in_process_ingestor(database, artifact_store),
    ).ingest_run(run_id, user_id=user_id)

    outcomes = {str(result.upload_id): result.error_code for result in results}
    assert outcomes == {good: None, locked: "document_encrypted"}
    assert (await client.get(f"{API}/research/{run_id}")).json()["source_count"] == 1
