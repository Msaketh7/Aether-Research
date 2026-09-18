"""Registration and sign-in.

The rules that make the difference between an authentication system and a
password check live here rather than in the endpoint, so they are testable
without HTTP and so none of them can be skipped by a second caller later:

* **A wrong address and a wrong password are the same answer**, in the same
  time. The message is identical, the code is identical, and when the address
  is unknown the work of a real verification is still done
  (``dummy_verify``) - otherwise the response time answers "is this address
  registered?" for anybody who asks.
* **An account with no password hash cannot be signed into.** Null means *this
  account has no password*, and there is no branch here where it means
  anything else.
* **Cost parameters can be raised at any time.** A successful verification
  against a hash weaker than the current settings re-hashes in place, so
  raising the cost migrates passwords as people sign in rather than needing a
  reset.
* **Registration does not reveal whether an address is taken.** It returns the
  same refusal as a policy failure would, for the same reason as above.

What is *not* here: the cookie, the audit row, the rate-limit decision. Those
are properties of a request, and they belong at the endpoint that has one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.auth.passwords import (
    HashParameters,
    check_password_policy,
    dummy_verify,
    hash_password,
    verify_password,
)
from app.auth.sessions import IssuedSession, SessionService
from app.core.config import Settings
from app.core.errors import RegistrationClosed, Unauthenticated, ValidationFailed
from app.core.logging import get_logger
from app.db.models.user import UserRow
from app.db.repositories.user import UserRepository

logger = get_logger(__name__)

#: Longest display name accepted. The column holds 200.
MAX_NAME_LENGTH = 200


#: The single message every failed credential check returns. One string, in one
#: place, so that no future branch can accidentally make one of them
#: distinguishable from the others.
_REFUSAL = "That email and password do not match an account."


@dataclass(frozen=True, slots=True)
class SignIn:
    """A successful authentication and the session issued for it."""

    user: UserRow
    session: IssuedSession


class AuthService:
    """Account creation and sign-in, over the user and session stores."""

    def __init__(
        self,
        *,
        users: UserRepository,
        sessions: SessionService,
        settings: Settings,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._settings = settings
        self._hash_parameters = HashParameters(
            time_cost=settings.password_hash_time_cost,
            memory_kib=settings.password_hash_memory_kib,
            parallelism=settings.password_hash_parallelism,
        )

    async def register(
        self,
        *,
        email: str,
        password: str,
        name: str,
        user_agent: str,
        ip: str | None,
        now: dt.datetime,
    ) -> SignIn:
        """Create an account and sign it in.

        Signing in as part of registering is what the frontend expects, and it
        also means there is exactly one code path that issues a session.
        """
        if not self._settings.registration_enabled:
            raise RegistrationClosed()

        normalised = normalise_email(email)
        check_password_policy(
            password, email=normalised, minimum_length=self._settings.min_password_length
        )

        password_hash = await hash_password(password, self._hash_parameters)
        user = await self._users.create(
            email=normalised,
            password_hash=password_hash,
            name=(name.strip() or normalised.split("@")[0])[:MAX_NAME_LENGTH],
        )
        if user is None:
            # The address is taken. The message does not say so - but be honest
            # about what that buys: any refusal of a well-formed address and an
            # acceptable password means the address is registered, and no
            # wording changes that. Registration is inherently an enumeration
            # surface, and the controls that actually apply are the credential
            # rate limit in front of it and the audit row behind it. Closing it
            # properly means accepting every sign-up and confirming by email,
            # which needs mail delivery this system does not have.
            logger.info("registration refused: address already registered")
            raise ValidationFailed(
                "That account could not be created.",
                code="registration_refused",
                details={"email": ["This address cannot be registered."]},
            )

        await self._users.record_login(user.id, at=now)
        session = await self._sessions.issue(user_id=user.id, user_agent=user_agent, ip=ip, now=now)
        return SignIn(user=user, session=session)

    async def sign_in(
        self,
        *,
        email: str,
        password: str,
        user_agent: str,
        ip: str | None,
        now: dt.datetime,
    ) -> SignIn:
        """Verify a credential and issue a session, or refuse indistinguishably."""
        normalised = normalise_email(email)
        user = await self._users.get_by_email(normalised)

        if user is None or not user.password_hash:
            # Burn the same work a real verification costs. Without this the
            # response time says whether the address exists, and whether it has
            # a password - two facts this endpoint must not hand out.
            await dummy_verify(self._hash_parameters)
            raise Unauthenticated(_REFUSAL, code="invalid_credentials")

        result = await verify_password(user.password_hash, password, self._hash_parameters)
        if not result.ok:
            raise Unauthenticated(_REFUSAL, code="invalid_credentials")

        if result.needs_rehash:
            # The password is in hand and correct exactly once - now - so this
            # is the only moment stronger parameters can be applied without
            # asking the user for anything.
            logger.info("re-hashing a password under the current parameters")
            await self._users.set_password_hash(
                user.id, await hash_password(password, self._hash_parameters)
            )

        await self._users.record_login(user.id, at=now)
        session = await self._sessions.issue(user_id=user.id, user_agent=user_agent, ip=ip, now=now)
        return SignIn(user=user, session=session)


def normalise_email(email: str) -> str:
    """Trim and case-fold an address.

    The column is ``citext``, so the database already compares
    case-insensitively; folding here as well means the *stored* value is
    predictable and that the value fed to the password policy matches the value
    used for lookup. Nothing clever is attempted - no dot-stripping, no
    plus-address folding - because those are provider-specific policies and
    applying them would silently merge addresses their owners consider distinct.
    """
    return email.strip().casefold()
