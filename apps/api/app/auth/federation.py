"""Turning a verified provider identity into an account.

The provider has already proved who somebody is at *its* end. This is the step
that decides who that makes them *here*, and it is the part of federated
sign-in that gets written wrong. The rules, in the order they are applied:

1. **A known `(provider, subject)` signs in as its linked account.** This is
   the only path that needs no further judgement: the pair was linked
   deliberately once and is unique in the database.

2. **An unknown subject with a verified address may attach to a local account
   with that address - only if the deployment has turned that on.** Off by
   default, because it is an account-takeover primitive: anyone who can make a
   provider assert `someone@example.com` becomes whoever owns that address
   here. It is defensible only with a *verified* address, and even then it is
   a decision a deployment should make rather than inherit, so it is a setting
   and the setting defaults to off.

3. **Otherwise a new account is created**, if SSO registration is open.

**An unverified address is never used for anything.** Not for matching, not
for populating the new account's email. A provider that does not verify
addresses is a provider where the address is a string somebody typed, and
treating it as an identity is the whole bug. GitHub in particular will hand
over an unverified address if the account has one.

**The subject is the key, always.** `ProviderClaims` deliberately offers no way
to look an identity up by email; see `app.db.repositories.identity`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.auth.providers.base import ProviderClaims
from app.core.errors import AppError, RegistrationClosed
from app.core.logging import get_logger
from app.db.models.user import UserRow
from app.db.repositories.identity import IdentityRepository
from app.db.repositories.user import UserRepository

logger = get_logger(__name__)

#: Longest display name taken from a provider. The column holds 200.
MAX_NAME_LENGTH = 200


class AccountConflict(AppError):
    """A provider identity cannot be resolved to exactly one account.

    Raised where linking would be a guess: an address that matches a local
    account while automatic linking is off, or a subject that lost a race to
    be linked. The message never says which, and never confirms that an
    address is registered - `/login` is reachable by anybody and is subject to
    the same enumeration rule as the credential endpoint.
    """

    status_code = 409
    code = "account_conflict"


@dataclass(frozen=True, slots=True)
class ResolvedAccount:
    """The account a provider sign-in resolved to."""

    user: UserRow
    #: True when this sign-in created the account, which the audit row records
    #: and the caller uses to decide between a `register` and a `login` event.
    created: bool


class FederationService:
    """Resolves verified provider claims to a local account."""

    def __init__(
        self,
        *,
        users: UserRepository,
        identities: IdentityRepository,
        registration_enabled: bool,
        link_by_verified_email: bool,
    ) -> None:
        self._users = users
        self._identities = identities
        self._registration_enabled = registration_enabled
        self._link_by_verified_email = link_by_verified_email

    async def resolve(
        self,
        *,
        provider: str,
        claims: ProviderClaims,
        now: dt.datetime,
    ) -> ResolvedAccount:
        existing = await self._identities.find(provider=provider, subject=claims.subject)
        if existing is not None:
            user = await self._users.get(existing.user_id)
            if user is None:
                # The identity outlived its account. Not reachable through the
                # cascade, so it is a broken invariant rather than a case to
                # paper over by creating a replacement account.
                logger.error(
                    "identity row points at a missing user",
                    extra={"provider": provider, "identity_id": str(existing.id)},
                )
                raise AccountConflict("That sign-in could not be completed.")
            await self._identities.touch(existing.id, now=now)
            return ResolvedAccount(user=user, created=False)

        email = claims.usable_email

        if email is not None and self._link_by_verified_email:
            matched = await self._users.get_by_email(email)
            if matched is not None:
                linked = await self._identities.link(
                    user_id=matched.id,
                    provider=provider,
                    subject=claims.subject,
                    connection=claims.connection,
                    email=email,
                    now=now,
                )
                if linked is None:
                    raise AccountConflict("That sign-in could not be completed.")
                logger.info(
                    "provider identity linked to an existing account by verified email",
                    extra={"provider": provider, "user_id": str(matched.id)},
                )
                return ResolvedAccount(user=matched, created=False)

        if email is not None and not self._link_by_verified_email:
            # An address that already belongs to a local account, with
            # automatic linking off. Refused rather than silently creating a
            # second account with a duplicate address - `users.email` is
            # unique, so that insert would fail anyway, and this failure at
            # least carries a code the frontend can explain.
            matched = await self._users.get_by_email(email)
            if matched is not None:
                logger.info(
                    "refused to link a provider identity to an existing address",
                    extra={"provider": provider},
                )
                raise AccountConflict("That sign-in could not be completed.")

        return await self._create(provider=provider, claims=claims, email=email, now=now)

    async def _create(
        self,
        *,
        provider: str,
        claims: ProviderClaims,
        email: str | None,
        now: dt.datetime,
    ) -> ResolvedAccount:
        if not self._registration_enabled:
            raise RegistrationClosed()

        if email is None:
            # Nothing to call the account and no way to contact it. Refused
            # rather than synthesising an address from the subject, which would
            # create an account nobody can ever recover or match.
            logger.info(
                "provider returned no verified address; cannot create an account",
                extra={"provider": provider},
            )
            raise AccountConflict("That sign-in could not be completed.")

        user = await self._users.create(
            email=email,
            # **Null, not empty.** `AuthService.sign_in` refuses an account
            # with no password hash, and that is exactly right here: an account
            # created through a provider has no password, and must not be
            # reachable through the password form until somebody sets one.
            password_hash=None,
            name=(claims.name or email.split("@")[0])[:MAX_NAME_LENGTH],
        )
        if user is None:
            # Lost a race with another sign-in for the same address.
            raise AccountConflict("That sign-in could not be completed.")

        linked = await self._identities.link(
            user_id=user.id,
            provider=provider,
            subject=claims.subject,
            connection=claims.connection,
            email=email,
            now=now,
        )
        if linked is None:
            raise AccountConflict("That sign-in could not be completed.")

        logger.info(
            "account created from a provider sign-in",
            extra={"provider": provider, "user_id": str(user.id)},
        )
        return ResolvedAccount(user=user, created=True)
