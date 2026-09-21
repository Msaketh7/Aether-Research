"""Issuing, renewing, verifying and revoking sessions.

**Signed tokens, not opaque ones** (ADR 0022, which supersedes ADR 0021). A
caller proves who they are with a short-lived access token this system signed,
validated by signature rather than by a row lookup - which is what lets the
worker and any future service verify a caller without reaching into the API's
database.

The three properties that opaque sessions gave away for free are rebuilt here
rather than abandoned:

* **Revocation takes effect on the next request.** `revoke` writes to the
  revocation index as well as the row, and every verification consults it. The
  row is the durable record; the index is what makes the refusal immediate.
* **Renewal is checked against the database.** A refresh always reads
  `sessions.revoked_at`, so a revoked session cannot be renewed even if the
  index has been emptied. This is why losing the index degrades revocation to
  the access token's lifetime and no further.
* **A stolen refresh token is detected, not just rotated around.** Reuse of a
  consumed token revokes its whole family. See
  `app.db.repositories.refresh_token` for why both parties are signed out.

What is *not* here: cookies, audit rows, rate limits. Those are properties of
a request and belong at the endpoint that has one.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.auth.revocation import RevocationStore, revocation_ttl_seconds
from app.auth.tokens import (
    AccessClaims,
    IssuedTokens,
    TokenIssuer,
    hash_refresh_token,
    mint_refresh_token,
)
from app.core.errors import Unauthenticated
from app.core.logging import get_logger
from app.db.models.user import SessionRow
from app.db.repositories.refresh_token import DurableRevocation, RefreshTokenRepository
from app.db.repositories.session import SessionRepository

logger = get_logger(__name__)

#: The single refusal every failed renewal returns. One string in one place, so
#: no later branch can accidentally distinguish "unknown token" from "already
#: used" from "expired" - which would tell an attacker which of those they are
#: holding.
_REFRESH_REFUSAL = "Sign in to continue."


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    """How long a session lives and how many a person may hold."""

    ttl_seconds: int
    max_per_user: int


class SessionService:
    """The session lifecycle, over the session and refresh-token stores."""

    def __init__(
        self,
        repository: SessionRepository,
        policy: SessionPolicy,
        *,
        refresh_tokens: RefreshTokenRepository,
        issuer: TokenIssuer,
        revocations: RevocationStore,
        access_ttl_seconds: int,
        durable_revocation: DurableRevocation | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._refresh = refresh_tokens
        self._issuer = issuer
        self._revocations = revocations
        self._access_ttl = access_ttl_seconds
        # Optional so a unit test can build the service without a database
        # handle; a request always has one, wired in `deps`.
        self._durable = durable_revocation

    async def start(
        self,
        *,
        user_id: uuid.UUID,
        email: str,
        role: str,
        provider: str,
        user_agent: str,
        ip: str | None,
        now: dt.datetime,
    ) -> IssuedTokens:
        """Begin a session and mint its first token pair.

        The per-user ceiling is applied *before* the new row is inserted, so
        the session being started is never the one revoked to make room for
        itself.
        """
        if self._policy.max_per_user > 0:
            revoked = await self._repository.revoke_beyond(
                user_id, keeping=self._policy.max_per_user - 1, now=now
            )
            if revoked:
                logger.info(
                    "older sessions revoked to stay within the per-user ceiling",
                    extra={"revoked": revoked, "ceiling": self._policy.max_per_user},
                )

        expires_at = now + dt.timedelta(seconds=self._policy.ttl_seconds)
        row = await self._repository.create(
            user_id=user_id,
            expires_at=expires_at,
            # Truncated here rather than at the column: a header is
            # attacker-controlled and its only use is being read by a person.
            user_agent=user_agent[:400],
            ip=ip,
            provider=provider,
        )

        return await self._mint(
            session=row,
            email=email,
            role=role,
            provider=provider,
            # A new sign-in starts a new family. Nothing links it to whatever
            # the previous one was, which is what makes a compromised family
            # revocable without touching this one.
            family_id=uuid.uuid4(),
            now=now,
        )

    async def refresh(
        self,
        token: str,
        *,
        load_identity: Callable[[uuid.UUID], Awaitable[tuple[str, str] | None]],
        now: dt.datetime,
    ) -> tuple[IssuedTokens, SessionRow]:
        """Exchange a refresh token for a new pair, rotating it.

        `load_identity` returns the user's *current* email and role, and the
        new access token is minted from that rather than from whatever the
        previous token carried. That is what makes a role change take effect on
        the next renewal instead of waiting for a sign-out: the token is a
        snapshot, and this is the moment the snapshot is retaken.

        Passed in rather than looked up here, so this service keeps needing
        only the two stores it owns - a user repository would make it the place
        every account concern eventually lands.
        """
        row = await self._refresh.find(hash_refresh_token(token))
        if row is None:
            raise Unauthenticated(_REFRESH_REFUSAL, code="invalid_refresh_token")

        if row.used_at is not None:
            # **Reuse.** Two parties hold this token. Which one is legitimate
            # cannot be determined, so the whole family goes and everybody
            # signs in again.
            logger.warning(
                "refresh token reuse detected; revoking the family",
                extra={"family_id": str(row.family_id), "session_id": str(row.session_id)},
            )
            # **In its own transaction, not this request's.** The refusal below
            # is a 401, and a 401 rolls the request's transaction back - which
            # would undo this revocation and leave a detector that detects and
            # changes nothing. See `DurableRevocation`.
            if self._durable is not None:
                await self._durable.revoke_family(
                    family_id=row.family_id, session_id=row.session_id, now=now
                )
            else:
                await self._refresh.revoke_family(row.family_id, now=now)
                await self._repository.revoke_unscoped(row.session_id, now=now)

            # The index is a separate store, so it is unaffected by the
            # rollback and can be written either way.
            await self._revocations.revoke(
                row.session_id, ttl_seconds=revocation_ttl_seconds(self._access_ttl)
            )
            raise Unauthenticated(_REFRESH_REFUSAL, code="invalid_refresh_token")

        if row.revoked_at is not None or row.expires_at <= now:
            raise Unauthenticated(_REFRESH_REFUSAL, code="invalid_refresh_token")

        # The database decides who wins a race, not a prior check: two requests
        # presenting the same unused token both reach here, and exactly one
        # rowcount comes back non-zero.
        if not await self._refresh.mark_used(row.id, now=now):
            raise Unauthenticated(_REFRESH_REFUSAL, code="invalid_refresh_token")

        # Read *after* consuming the token, and from the database rather than
        # the index: this is what makes a revoked session unrenewable even when
        # the revocation index has been emptied.
        session = await self._repository.find_active(row.session_id, now=now)
        if session is None:
            raise Unauthenticated(_REFRESH_REFUSAL, code="invalid_refresh_token")

        identity = await load_identity(session.user_id)
        if identity is None:
            # The session outlived its account.
            raise Unauthenticated(_REFRESH_REFUSAL, code="invalid_refresh_token")
        email, role = identity

        issued = await self._mint(
            session=session,
            email=email,
            role=role,
            provider=session.provider,
            # Same family: this token descends from the same sign-in, and that
            # is what makes the lineage revocable as a unit.
            family_id=row.family_id,
            now=now,
        )
        return issued, session

    async def verify(self, access_token: str, *, now: dt.datetime) -> AccessClaims:
        """Check an access token's signature and that its session still lives."""
        claims = self._issuer.verify_access_token(access_token, now=now)

        if await self._revocations.is_revoked(claims.session_id):
            raise Unauthenticated("Sign in to continue.", code="session_revoked")

        return claims

    async def revoke(self, session_id: uuid.UUID, *, user_id: uuid.UUID, now: dt.datetime) -> bool:
        """Sign one device out, everywhere that matters.

        Three writes, and all three are needed: the row so the record is
        durable and the session cannot be renewed, the refresh tokens so no
        replacement can be minted, and the index so the access token already in
        the browser stops working on its next request rather than at expiry.
        """
        revoked = await self._repository.revoke(session_id, user_id=user_id, now=now)
        if not revoked:
            return False

        await self._refresh.revoke_for_session(session_id, now=now)
        await self._revocations.revoke(
            session_id, ttl_seconds=revocation_ttl_seconds(self._access_ttl)
        )
        return True

    async def revoke_others(
        self, *, user_id: uuid.UUID, keep: uuid.UUID | None, now: dt.datetime
    ) -> int:
        rows = await self._repository.list_active(user_id, now=now)
        revoked = 0
        for row in rows:
            if keep is not None and row.id == keep:
                continue
            if await self.revoke(row.id, user_id=user_id, now=now):
                revoked += 1
        return revoked

    async def list_active(self, user_id: uuid.UUID, *, now: dt.datetime) -> list[SessionRow]:
        return await self._repository.list_active(user_id, now=now)

    async def _mint(
        self,
        *,
        session: SessionRow,
        email: str,
        role: str,
        provider: str,
        family_id: uuid.UUID,
        now: dt.datetime,
    ) -> IssuedTokens:
        access_token, access_expires_at = self._issuer.issue_access_token(
            user_id=session.user_id,
            session_id=session.id,
            email=email,
            role=role,
            provider=provider,
            now=now,
        )

        refresh_token = mint_refresh_token()
        await self._refresh.create(
            session_id=session.id,
            token_hash=hash_refresh_token(refresh_token),
            family_id=family_id,
            # A refresh token never outlives the session it renews. Otherwise
            # the session TTL is decorative: the pair would keep rotating past
            # the point the session was supposed to end.
            expires_at=session.expires_at,
        )

        return IssuedTokens(
            access_token=access_token,
            access_expires_at=access_expires_at,
            refresh_token=refresh_token,
            refresh_expires_at=session.expires_at,
            session_id=session.id,
        )
