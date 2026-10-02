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

**Where the password is checked depends on `AUTH_BACKEND` (ADR 0025).**
With `supabase`, Supabase Auth holds the password and confirms the address;
this service asks it, verifies the token it answers with, and resolves the
Supabase user to an account here through the same federation rules a Google
sign-in uses - the account is linked on Supabase's user id, never on email.
With `local`, the Argon2id path above is unchanged. Either way the session is
this system's own, so nothing after sign-in can tell the two apart.

What is *not* here: the cookie, the audit row, the rate-limit decision. Those
are properties of a request, and they belong at the endpoint that has one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.auth.federation import FederationService
from app.auth.passwords import (
    HashParameters,
    check_password_policy,
    dummy_verify,
    hash_password,
    verify_password,
)
from app.auth.sessions import SessionService
from app.auth.supabase import ConfirmationPending, SupabaseAuth, SupabaseIdentity
from app.auth.tokens import IssuedTokens
from app.core.config import Settings
from app.core.errors import InvalidCredentials, NotFound, RegistrationClosed, ValidationFailed
from app.core.logging import get_logger
from app.db.models.user import UserRow
from app.db.repositories.user import UserRepository

logger = get_logger(__name__)

#: Longest display name accepted. The column holds 200.
MAX_NAME_LENGTH = 200


@dataclass(frozen=True, slots=True)
class SignIn:
    """A successful authentication and the tokens issued for it.

    The same type single sign-on produces. Password and provider sign-in differ
    only in how the person was identified; from here on there is one session
    concept, one token pair and one revocation story.
    """

    user: UserRow
    tokens: IssuedTokens


@dataclass(frozen=True, slots=True)
class ConfirmationSent:
    """A sign-up that is waiting on the person to follow an emailed link.

    No account exists here yet, and no session: the first sign-in after the
    address is confirmed is what creates the account's row in this database.
    """

    email: str


class AuthService:
    """Account creation and sign-in, over the user and session stores."""

    def __init__(
        self,
        *,
        users: UserRepository,
        sessions: SessionService,
        settings: Settings,
        supabase: SupabaseAuth | None = None,
        federation: FederationService | None = None,
    ) -> None:
        if settings.auth_backend == "supabase" and (supabase is None or federation is None):
            raise ValueError("The supabase auth backend needs a Supabase client and federation")
        self._users = users
        self._sessions = sessions
        self._settings = settings
        self._supabase = supabase if settings.auth_backend == "supabase" else None
        self._federation = federation
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
    ) -> SignIn | ConfirmationSent:
        """Create an account, and sign it in if the address needs no confirming.

        Signing in as part of registering is what the frontend expects, and it
        also means there is exactly one code path that issues a session. Under
        Supabase with confirmation on - its default - there is no session yet,
        and the answer is :class:`ConfirmationSent` instead.

        The password policy is applied here even when Supabase holds the
        password: it is this deployment's policy, and the person should hear it
        before a round trip rather than in Supabase's words after one. The
        Supabase project must enforce the same floor, because its publishable
        key is public and sign-up can be called without this API.
        """
        if not self._settings.registration_enabled or not self._settings.password_login_enabled:
            raise RegistrationClosed()

        normalised = normalise_email(email)
        check_password_policy(
            password, email=normalised, minimum_length=self._settings.min_password_length
        )
        display_name = (name.strip() or normalised.split("@")[0])[:MAX_NAME_LENGTH]

        if self._supabase is not None:
            outcome = await self._supabase.sign_up(
                email=normalised,
                password=password,
                name=display_name,
                redirect_to=self.confirmation_redirect,
                client_ip=ip,
            )
            if isinstance(outcome, ConfirmationPending):
                return ConfirmationSent(email=outcome.email)
            return await self._adopt(outcome, user_agent=user_agent, ip=ip, now=now)

        password_hash = await hash_password(password, self._hash_parameters)
        user = await self._users.create(
            email=normalised,
            password_hash=password_hash,
            name=display_name,
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
        tokens = await self._sessions.start(
            user_id=user.id,
            email=user.email,
            role=user.role,
            provider="local",
            user_agent=user_agent,
            ip=ip,
            now=now,
        )
        return SignIn(user=user, tokens=tokens)

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
        if not self._settings.password_login_enabled:
            # Refused with the same message as a wrong password. A distinct
            # one would be harmless here - the setting is not a secret - but
            # keeping one refusal for the whole endpoint is what stops a later
            # branch reintroducing a difference that does matter.
            raise InvalidCredentials()

        normalised = normalise_email(email)

        if self._supabase is not None:
            # Supabase answers an unknown address and a wrong password with the
            # same `invalid_credentials`, and only says the address is
            # unconfirmed once the password was right - the same rules as below.
            identity = await self._supabase.sign_in_with_password(
                email=normalised, password=password, client_ip=ip
            )
            return await self._adopt(identity, user_agent=user_agent, ip=ip, now=now)

        user = await self._users.get_by_email(normalised)

        if user is None or not user.password_hash:
            # Burn the same work a real verification costs. Without this the
            # response time says whether the address exists, and whether it has
            # a password - two facts this endpoint must not hand out.
            await dummy_verify(self._hash_parameters)
            raise InvalidCredentials()

        result = await verify_password(user.password_hash, password, self._hash_parameters)
        if not result.ok:
            raise InvalidCredentials()

        if result.needs_rehash:
            # The password is in hand and correct exactly once - now - so this
            # is the only moment stronger parameters can be applied without
            # asking the user for anything.
            logger.info("re-hashing a password under the current parameters")
            await self._users.set_password_hash(
                user.id, await hash_password(password, self._hash_parameters)
            )

        await self._users.record_login(user.id, at=now)
        tokens = await self._sessions.start(
            user_id=user.id,
            email=user.email,
            role=user.role,
            # The issuer that admitted this session. `local` means a password
            # was verified here, which is what distinguishes it in the device
            # list and in the audit trail from a federated sign-in.
            provider="local",
            user_agent=user_agent,
            ip=ip,
            now=now,
        )
        return SignIn(user=user, tokens=tokens)

    async def resend_confirmation(self, *, email: str, ip: str | None) -> None:
        """Email the sign-up link again. Silent about whether anybody was waiting."""
        if self._supabase is None:
            raise NotFound(
                "This deployment does not confirm email addresses.",
                code="confirmation_not_used",
            )
        await self._supabase.resend_confirmation(
            email=normalise_email(email),
            redirect_to=self.confirmation_redirect,
            client_ip=ip,
        )

    @property
    def confirmation_redirect(self) -> str:
        """Where the confirmation link lands: the sign-in page, saying so.

        Supabase confirms the address before it redirects, so the page only
        has to say it worked. The project's redirect allowlist must contain it.
        """
        return f"{self._settings.sso_app_base_url.rstrip('/')}/login?confirmed=1"

    async def _adopt(
        self,
        identity: SupabaseIdentity,
        *,
        user_agent: str,
        ip: str | None,
        now: dt.datetime,
    ) -> SignIn:
        """A Supabase user, as an account here with a session of ours.

        Through `FederationService`, so a password sign-in and a Google sign-in
        for the same Supabase user land on the same account: Supabase links
        those into one `auth.users` row itself, and this links on its id.
        """
        if self._federation is None:  # the constructor guarantees otherwise
            raise RuntimeError("Supabase sign-in without a federation service")
        resolved = await self._federation.resolve(
            provider="supabase", claims=identity.claims(), now=now
        )
        await self._users.record_login(resolved.user.id, at=now)
        tokens = await self._sessions.start(
            user_id=resolved.user.id,
            email=resolved.user.email,
            role=resolved.user.role,
            provider="supabase",
            user_agent=user_agent,
            ip=ip,
            now=now,
        )
        return SignIn(user=resolved.user, tokens=tokens)


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
