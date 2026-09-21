# ADR 0022: Federated identity, and signed tokens instead of opaque sessions

- **Status**: Accepted
- **Supersedes**: [ADR 0021](0021-sessions-not-tokens.md)
- **Date**: 2026-09-19

## Context

Phase 20 shipped password authentication with opaque server-side sessions: a
256-bit token in an `HttpOnly` cookie, hashed at rest, resolved by a row
lookup on every request. ADR 0021 chose that over JWTs for one reason, and it
was the right reason — a session can be revoked and the refusal lands on the
next request, whereas a signed token stays valid until it expires however
loudly the database disagrees.

Two requirements then arrived that the existing design could not absorb:

1. **Single sign-on** through Google and GitHub, brokered by **Auth0 and
   Supabase**, alongside the password form.
2. **Provider JWTs as the credential**, rather than exchanging a provider
   sign-in for one of our own opaque sessions.

The second is a deliberate reversal of ADR 0021, made with its consequence
stated: the opaque-session design was chosen for revocation, so replacing it
gives revocation up unless something is built to replace that property.

## Decision

**Every credential path ends in a signed access token.** Password sign-in mints
one from a first-party key; a provider sign-in mints one after verifying the
provider's ID token. The rest of the system cannot tell which happened, and
there is one session concept rather than two.

**Three mechanisms rebuild what opaque sessions gave away:**

| Property lost                  | How it comes back                                                                                        |
| ------------------------------ | -------------------------------------------------------------------------------------------------------- |
| Revocation on the next request | A **revocation index** keyed by the session id in every token's `sid`, consulted on every verification.  |
| A bounded credential lifetime  | **Short access tokens** (15 minutes), so the window in which a stolen one works is the token's lifetime. |
| Detecting a stolen credential  | **Refresh rotation with reuse detection** — a second use of a consumed token revokes the whole family.   |

**The `sessions` table survives, reshaped.** It no longer holds a credential:
`token_hash` is dropped, and the row's _id_ is what appears in tokens. What it
still does is the two things a token cannot — show a person their devices, and
be the durable record whose `revoked_at` stops a session being renewed.

**Providers sit behind one interface.** `IdentityProvider` is the seam, the
same shape used for models, storage, search and retrieval (ADR 0003). Auth0 and
Supabase are two descriptions of one shared OIDC implementation; the vendor
-specific code is about forty lines each and holds no protocol logic.

**Identities link on `(provider, subject)`, never on email.** Attaching a
provider sign-in to an existing local account by matching addresses is off by
default and requires the provider to have marked the address _verified_.

## Consequences

### What this costs, stated plainly

**Revocation is no longer atomic with the database write.** Under ADR 0021 a
revoked session was refused because the lookup found `revoked_at`. Now it is
refused because a separate index says so. If that index is unreachable or
empty — a Redis restart, a development process restart — revocation degrades
to the access token's remaining lifetime, at most fifteen minutes.

It cannot degrade further than that, and this is the property worth being
precise about: **the refresh flow reads `sessions.revoked_at` from Postgres**,
not from the index. A revoked session cannot be renewed regardless of what the
index remembers. Postgres remains the durable record of a revocation; the
index is only what makes it take effect sooner than expiry would.

**The index fails open, deliberately.** If Redis is unreachable, treating every
session as revoked would sign out every user over an infrastructure blip.
Treating none as revoked narrows revocation to the guarantee above. The
degraded mode is the documented one rather than a total outage.

**Reuse detection has a false-positive mode.** A client that retries a refresh
after a dropped response presents a consumed token and has its family revoked.
That costs one re-authentication. The alternative — assuming the second use is
legitimate — lets a thief rotate alongside the victim indefinitely, which is
the attack rotation exists to catch.

**A signing key is now required outside `local` and `test`.** Refused at
startup rather than generated, because an ephemeral key means each instance
signs with its own: a token issued by one is refused by every other, and every
restart signs everybody out. That reads as an intermittent auth outage rather
than a missing setting, so it is a startup failure instead.

**Supabase projects must use asymmetric signing keys.** The algorithm allowlist
contains no HMAC algorithm, because accepting one enables algorithm confusion
— a verifier that accepts `HS256` will validate a token signed with the
provider's _public_ key as the secret, and that key is public. A project still
on a legacy HS256 JWT secret will fail verification, which is the correct
outcome; the fix is migrating the project to ES256, which Supabase recommends
anyway.

### What it buys

- Verification needs no database round trip, so the worker — or any future
  service — can authenticate a caller without reaching into the API's database.
- One session concept across password and federated sign-in.
- Adding a third provider is a description, not a code path.

### Rejected alternatives

**Exchange a provider sign-in for an opaque session (keep ADR 0021).** The
architecturally cheaper option: everything Phase 20 built would have survived
unchanged and SSO would have become one more way to reach the same session.
Rejected because the requirement was explicitly for provider JWTs to be the
credential. This is the alternative to revisit first if the revocation
semantics above prove too weak in practice.

**Two parallel identity systems**, password sessions beside provider tokens.
Rejected: two auth paths through every endpoint, two revocation stories, and
every future security control implemented twice.

**Long-lived access tokens with no refresh.** Rejected: the token lifetime is
the compromise window, and a long one with no rotation is a bearer password.

## Verification status

The security properties are tested against a scripted provider driven through
the real gateway — algorithm confusion, `alg: none`, nonce replay, cross-issuer
and cross-audience tokens, expired tokens, unverified-email handling, PKCE
challenge derivation, JWKS caching and its refetch cooldown, single-use
transactions, token tampering and revocation.

**No live round trip has been completed.** There are no Auth0 or Supabase
credentials on this machine, so the flow against a real provider is
**unverified** — the same status the evaluation benchmark and the deployment
carry, and recorded here for the same reason.
