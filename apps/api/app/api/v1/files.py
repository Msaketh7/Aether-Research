"""The file API (TDD 3.5): upload a document so a research run can use it.

An upload is checked, stored and recorded, and that is all a request does.
Parsing, chunking and embedding happen when a run that names the upload in
``document_ids`` is executed - in the worker, in a killable child process
(ADR 0012). The request thread never runs a parser on hostile bytes.

## A direct upload, not a presigned URL

TDD 3.5 specifies presigned S3 uploads. This build takes the bytes through the
API, for reasons recorded in ADR 0012: the 25 MiB ceiling is small enough to
stream with a hard cap; the magic-byte check happens *before* anything is
stored, where a presigned flow can only check after the bytes are in the
bucket; and the filesystem backend - the one that makes the storage path
runnable without Docker - cannot presign at all.

The body is the raw file, not a multipart form. ``Content-Type`` is the file's
type and ``Content-Disposition`` carries its name. That keeps a multipart parser
- a recurring source of denial-of-service bugs - out of the upload path.
"""

from __future__ import annotations

import email.message
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    AuditTrailDep,
    CurrentUser,
    PageParamsDep,
    RateLimit,
    SettingsDep,
    UploadServiceDep,
)
from app.core.enums import AuditAction
from app.core.pagination import Page
from app.retrieval.errors import DocumentTooLarge
from app.retrieval.formats import MEDIA_TYPES
from app.retrieval.schemas import UploadedFile
from app.security.ratelimit import WRITE

router = APIRouter(prefix="/files", tags=["files"])

_REQUEST_BODY = {
    "required": True,
    "description": (
        "The file itself, as the raw request body. Content-Type is the file's type; "
        "Content-Disposition carries its name."
    ),
    "content": {
        media_type: {"schema": {"type": "string", "format": "binary"}}
        for media_type in MEDIA_TYPES.values()
    },
}


@router.post(
    "",
    response_model=UploadedFile,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document",
    responses={
        200: {
            "model": UploadedFile,
            "description": "This file was already uploaded; the existing record is returned.",
        }
    },
    openapi_extra={"requestBody": _REQUEST_BODY},
    # An upload is stored, parsed in a child process, chunked and embedded.
    # The tighter bucket, like the endpoints that start a run.
    dependencies=[Depends(RateLimit(WRITE))],
)
async def upload_file(
    request: Request,
    response: Response,
    user: CurrentUser,
    service: UploadServiceDep,
    settings: SettingsDep,
    trail: AuditTrailDep,
) -> UploadedFile:
    """201 for a new upload; 200 with the existing record when the same bytes come again."""
    data = await read_bounded_body(request, limit=settings.upload_limit_bytes)
    uploaded, created = await service.store(
        user.id,
        data,
        content_type=request.headers.get("content-type"),
        filename=filename_from_disposition(request.headers.get("content-disposition")),
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    # Recorded whether or not the bytes were new: the interesting fact is that
    # this account attached this document, not whether it saved a copy.
    await trail.record(
        AuditAction.FILE_UPLOADED,
        user_id=user.id,
        resource_type="upload",
        resource_id=uploaded.id,
        bytes=len(data),
        deduplicated=not created,
    )
    return uploaded


@router.get("", response_model=Page[UploadedFile], summary="List the caller's uploads")
async def list_files(
    user: CurrentUser, service: UploadServiceDep, params: PageParamsDep
) -> Page[UploadedFile]:
    return await service.list_for_user(user.id, params)


@router.get("/{upload_id}", response_model=UploadedFile, summary="One upload")
async def get_file(upload_id: UUID, user: CurrentUser, service: UploadServiceDep) -> UploadedFile:
    return await service.get(user.id, upload_id)


async def read_bounded_body(request: Request, *, limit: int) -> bytes:
    """The request body, refused the moment it passes ``limit``.

    A declared ``Content-Length`` over the limit is refused before a byte is
    read; a body without one is counted as it arrives. Either way the ceiling
    holds - a cap applied to a fully buffered body is not a cap, it is a report
    of how much memory was already spent.
    """
    message = f"That file is larger than the {limit:,}-byte upload limit."
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise DocumentTooLarge(message, context={"declared_bytes": int(declared), "limit": limit})

    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > limit:
            raise DocumentTooLarge(message, context={"limit": limit})
    return bytes(received)


def filename_from_disposition(header: str | None) -> str | None:
    """The filename in a ``Content-Disposition`` header, in either RFC 6266 form.

    Parsed with the standard library's MIME machinery rather than a regex:
    ``filename*=UTF-8''na%C3%AFve.pdf`` is what a browser sends for a non-ASCII
    name, and it is exactly the form a hand-written parser gets wrong.
    """
    if not header:
        return None
    parsed = email.message.Message()
    parsed["content-disposition"] = header
    return parsed.get_filename()
