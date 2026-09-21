# ADR 0021: Opaque server-side sessions in a cookie, not JWTs; and a limit that is a bucket, not a window

- **Status:** Superseded by [ADR 0022](0022-federated-identity-and-signed-tokens.md)
- **Date:** 2026-09-18

> **Superseded.** Single sign-on made the provider's JWT the credential, so
> the opaque session this ADR chose no longer exists. The reasoning below is
> still the reason revocation had to be rebuilt rather than dropped - ADR
> 0022 states what that cost and what replaced it. The rate-limiting half of
> this ADR is untouched and still current.

## Context

Phase 20 puts a real identity under rules that have been enforced since Phase 2.
Authorisation was never the open question — every research object carries a
`user_id` and every read is scoped by it — so what had to be decided was how a
request proves which user it is, and how much a proven user may ask for.

Three constraints shaped both answers.

**FR-1 requires per-device revocation.** "Sign this device out" is listed in the
PRD and rendered on the settings page. It is also the only security action a
person can take without an administrator, which makes it the one that has to
work immediately rather than eventually.

**The frontend is a browser application on a different port, and now on a
different host in production.** Whatever carries the credential has to survive a
page load, must not be reachable from JavaScript, and must work across a
same-site origin split.

**Every replica shares one Redis and one Postgres, and neither is guaranteed to
be up.** A control that turns a dependency's outage into the product's outage is
not a control anybody keeps switched on.

The earlier documents named Auth.js. That was written when the frontend was
expected to own the session and the API to validate it. The system as built has
a FastAPI service that every client talks to directly — the browser, the
Playwright suite, and eventually anything else — so the session belongs where
the authorisation checks already are.

## Decision

### The session is a row, and the cookie is a pointer to it

A token is 256 bits from the operating system's CSPRNG. It means nothing on its
own: who the session belongs to, when it expires and whether it was revoked are
columns in `sessions`. The row stores a SHA-256 of the token and never the
token.

**Not a JWT.** A signed token carries its claims, which is exactly what makes
revoking one hard: either the claims are trusted until they expire — so "sign
this device out" is a promise the system cannot keep — or every request checks a
revocation list, which is the database lookup a JWT was supposed to avoid. The
second is what a JWT deployment converges on, at which point it is a session
with extra cryptography. FR-1 asks for the first behaviour, so the row is the
truth from the start.

**SHA-256 at rest, not Argon2.** Password hashing is slow on purpose because a
password is low-entropy and guessable. A 256-bit CSPRNG token is neither, so a
slow hash buys nothing and would add its cost to _every authenticated request_.
The property both choices are after is the same: what is stored cannot be
replayed.

**Argon2id for passwords**, at the RFC 9106 low-memory parameters
(m=64 MiB, t=3, p=4) that are the library's defaults and OWASP's first
recommendation. Every hash and verify runs in a thread: one is ~130 ms of
deliberate CPU, and on the event loop that is 130 ms of stall for every other
request in the process — the defence becoming the denial of service.
`check_needs_rehash` re-hashes on the next successful sign-in, so the cost can
be raised later without a password reset.

**The cookie is `HttpOnly`, `Secure` outside development, `SameSite=Lax`,
`Path=/`.** `HttpOnly` is not configurable: it is what keeps an XSS bug on the
frontend from becoming account takeover. `SameSite=Lax` is enough while the web
app and the API share a registrable domain; `none` exists for the deployment
that genuinely splits them, and the settings validator refuses it without
`Secure` rather than letting a browser silently drop every session.

### The development identity survives, narrowed twice

`X-Aether-User` still selects a user, and an unauthenticated request in a
development environment still acts as the default developer. The gate is an
allowlist of `local` and `test` — not `!production`, which would leave `staging`
open — and a second flag, `DEV_IDENTITY_ENABLED`, can close it further but can
never open it, because the allowlist is checked first.

One behaviour is deliberately asymmetric: a request carrying a cookie that does
_not_ resolve is refused, even in a development environment. Falling through to
the shared identity would make a revoked session look like a working one, which
is the single thing revocation exists to prevent.

### The rate limit is a token bucket per identity and route class

A fixed window of 60 a minute allows 60 at 00:59 and 60 more at 01:00 — the
burst the limit existed to stop. A bucket separates the two numbers that mean
different things: capacity is what may be spent at once after being idle, and
refill is the sustained ceiling.

Three classes, because the requests are not alike: reading a run, starting one,
and attempting a password. Each has its own bucket, so heavy reading cannot
consume the allowance that bounds brute force.

**The credential endpoints draw on two buckets: the client address and the
address being attempted.** An address-only limit bounds one attacker trying many
accounts and misses many clients trying one account, which is what credential
stuffing is. Both are spent _before_ the password is verified, so a refusal
costs an attacker a round trip and costs this process no Argon2.

**Evaluated in Redis, in one Lua script.** A GET-then-SET pair from two replicas
is how a limit becomes a suggestion. The in-memory backend under `APP_ENV=test`
runs the identical arithmetic.

**It fails open.** A backend that cannot answer allows the request and logs an
error. Rate limiting protects against abuse; an outage of Redis turning into an
outage of the product would be the worse failure. The audit log still records
what happened during the window.

### The audit log is append-only, and written outside the request's transaction

Authentication events and research mutations become `audit_log` rows. Reads are
not audited: every request is already in the access log, and a trail that
records everything records nothing.

**Its own transaction**, like the ledger's. The most valuable rows — a refused
login, a rejected registration — are written on paths that end in an exception,
and an exception rolls the request's transaction back. Written there, the log
would contain successful logins only.

**A failed write never fails the request**, for the same reason the limiter
fails open. `user_id` is `ON DELETE SET NULL`, so deleting an account does not
delete the evidence of what it did.

## Consequences

**Good.** Revocation is immediate and per device, because there is nothing to
revoke except a row. A database leak hands over no live sessions and no
passwords. The cost of password hashing is a setting that can be raised without
a reset. One identity mechanism serves the browser, the tests and any future
client, and it is enforced beside the authorisation checks rather than a network
hop away from them. A new endpoint under `/api/v1` is rate-limited by
construction — the dependency is on the router, not on each route — which is the
failure mode a rate limit exists to prevent.

**Costs.** Every authenticated request does an indexed lookup in `sessions`; a
JWT would not. That is the price of revocation working, and it is one index hit
against a request that already opens a transaction. The API is stateful with
respect to Postgres for authentication, so a Postgres outage is a total outage —
which it already was, since the system of record holds every run.

**Accepted residuals.** Registration remains an enumeration surface: any refusal
of a well-formed address with an acceptable password means the address is taken,
and no wording changes that. Closing it properly means accepting every sign-up
and confirming by email, which needs mail delivery this system does not have;
the credential bucket in front of it and the audit row behind it are the
controls that do apply. Rate limiting depends on Redis being reachable to be
enforced at all. And with several API replicas, clock skew between them can
grant a client up to (skew × refill) extra tokens — bounded, and cheaper than
making the script depend on the Redis version's clock semantics.

**Superseded.** `docs/TDD.md` section 3.3 and `docs/threat-model.md` section 3.7
named Auth.js. The server-side session store, the hashed tokens and the
revocation list they describe are exactly what was built; the library is not.
