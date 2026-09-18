"""Password hashing and the password policy.

**Argon2id**, at the RFC 9106 low-memory parameters the library ships as its
default (m=64 MiB, t=3, p=4) - which is also OWASP's first recommendation. The
parameters are settings rather than constants because the right cost is a
property of the machine that runs the hash, not of this repository, and because
raising them later must be a configuration change rather than a migration.

Three properties this module exists to guarantee:

* **A hash is never computed on the event loop.** One hash is ~130 ms of
  deliberate CPU on this machine. Run inline it would stall every other request
  in the process for that long, which turns the defence into a denial of
  service. :func:`hash_password` and :func:`verify_password` are async and hand
  the work to a thread.
* **A wrong email and a wrong password cost the same.** Without
  :func:`dummy_verify`, "no such user" returns in microseconds and "wrong
  password" in 130 ms, so the timing tells an attacker which addresses are
  registered. The login path verifies against a throwaway hash when the user
  does not exist.
* **Parameters can be raised without locking anyone out.** ``verify`` reports
  whether the stored hash used weaker parameters than the current ones, and the
  login path rehashes in place when it did.

The policy is deliberately short: a length floor, a ceiling, and a refusal of
passwords derived from the address they protect. Composition rules ("one
symbol, one digit") are not in NIST 800-63B any more and mostly produce
`Password1!`; the length floor is the control that does work.
"""

from __future__ import annotations

import asyncio
import functools
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.errors import ValidationFailed

if TYPE_CHECKING:  # pragma: no cover - typing only
    from argon2 import PasswordHasher

#: Longest password accepted. Argon2 handles arbitrary input, but accepting a
#: megabyte of it means hashing a megabyte of it, once per attempt.
MAX_PASSWORD_LENGTH = 1024

#: The identifier every Argon2id hash produced here starts with. Used to
#: recognise a stored hash rather than to parse it.
ARGON2_PREFIX = "$argon2id$"

#: Passwords refused whatever their length. Not a serious breach list - that
#: belongs in a deployment - but the handful that a length floor alone lets
#: through.
_FORBIDDEN = frozenset(
    {
        "password",
        "passw0rd",
        "aetherresearch",
        "changeme",
        "letmein",
        "qwertyuiop",
        "123456789012",
        "administrator",
    }
)

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class HashParameters:
    """Argon2id cost. Raising any of these makes every existing hash stale,
    which is what ``needs_rehash`` is for."""

    time_cost: int
    memory_kib: int
    parallelism: int


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    #: True when the stored hash used parameters weaker than the current ones.
    #: Meaningless unless ``ok``: the plaintext is only in hand when it matched.
    needs_rehash: bool = False


@functools.lru_cache(maxsize=4)
def _hasher(parameters: HashParameters) -> PasswordHasher:
    """The configured hasher.

    Imported here rather than at module scope, like every other heavy
    third-party import in this codebase: ``argon2`` costs about half a second
    to import, and a process that never authenticates anybody - the worker -
    should not pay it. Cached because constructing one per call would re-read
    the parameters on every login for no benefit.
    """
    from argon2 import PasswordHasher
    from argon2.low_level import Type

    return PasswordHasher(
        time_cost=parameters.time_cost,
        memory_cost=parameters.memory_kib,
        parallelism=parameters.parallelism,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )


def normalise_password(raw: str) -> str:
    """What is actually hashed.

    Only the length ceiling is applied. No case folding, no trimming: a leading
    space a user typed deliberately is part of their password, and silently
    removing it means the password they set is not the password they have.
    """
    return raw[:MAX_PASSWORD_LENGTH]


def check_password_policy(password: str, *, email: str, minimum_length: int) -> None:
    """Refuse a password that would not protect the account.

    Raises :class:`ValidationFailed` with the field map the frontend renders
    next to the input, so the message arrives where the user is typing.
    """
    problems: list[str] = []

    if len(password) < minimum_length:
        problems.append(f"Use at least {minimum_length} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Use at most {MAX_PASSWORD_LENGTH} characters.")

    reduced = _NON_ALPHANUMERIC.sub("", password.casefold())
    if reduced in _FORBIDDEN:
        problems.append("That password is too common.")

    # A password that is the address it protects is one public fact away from
    # being known, and it is the single most common thing people type.
    local_part = email.split("@", 1)[0].casefold()
    if local_part and reduced and (reduced == local_part or reduced == email.casefold()):
        problems.append("Do not use your email address as your password.")

    if problems:
        raise ValidationFailed("That password cannot be used.", details={"password": problems})


async def hash_password(password: str, parameters: HashParameters) -> str:
    """Hash a password, off the event loop."""
    hasher = _hasher(parameters)
    return await asyncio.to_thread(hasher.hash, normalise_password(password))


async def verify_password(
    stored_hash: str, password: str, parameters: HashParameters
) -> VerifyResult:
    """Check a password against a stored hash, off the event loop.

    Never raises on a mismatch: a wrong password is an ordinary outcome, and an
    exception for it would put the attempt in the logs as a failure of the
    system rather than of the credential.
    """
    return await asyncio.to_thread(_verify_sync, stored_hash, password, parameters)


def _verify_sync(stored_hash: str, password: str, parameters: HashParameters) -> VerifyResult:
    from argon2.exceptions import Argon2Error, InvalidHashError

    hasher = _hasher(parameters)
    try:
        hasher.verify(stored_hash, normalise_password(password))
    except (Argon2Error, InvalidHashError):
        # Covers a wrong password, a corrupted hash and a hash from a scheme
        # this build does not know. All three mean "these credentials do not
        # open this account", and none of them is worth telling the caller apart.
        return VerifyResult(ok=False)
    return VerifyResult(ok=True, needs_rehash=hasher.check_needs_rehash(stored_hash))


async def dummy_verify(parameters: HashParameters) -> None:
    """Burn the same work a real verification would, and discard it.

    Called when the address is not registered, so that the response time of a
    login attempt carries no information about which addresses exist.
    """
    await verify_password(_dummy_hash(parameters), "not the password", parameters)


@functools.lru_cache(maxsize=4)
def _dummy_hash(parameters: HashParameters) -> str:
    """A real Argon2id hash of a fixed string, computed once per process.

    Cached rather than recomputed: computing it per attempt would double the
    cost of the very path the cache is protecting.
    """
    return _hasher(parameters).hash("aether-timing-equaliser")
