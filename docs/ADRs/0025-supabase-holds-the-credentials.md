# ADR 0025: Supabase holds the credentials; this system holds the session

- **Status**: Accepted
- **Amends**: [ADR 0022](0022-federated-identity-and-signed-tokens.md) - its
  password half. Its session half is unchanged.
- **Date**: 2026-10-02

## Context

Phase 20 built password sign-in on Argon2id hashes in this database, and Phase
26 added Google and GitHub through Auth0 or Supabase. The request that followed
was explicit: **use Supabase for authentication, as the store of accounts** -
sign-up, sign-in and the social providers all in one Supabase project.

The password path in this system also lacked two things every account system
needs and which need mail delivery to build: confirming that the person owns
the address, and resetting a forgotten password. Supabase Auth has both.

When a real Supabase project was created to do this, checking the existing
Supabase sign-in path against it found that the path could never have worked.
It had been tested only against a script written from the same reading of the
documentation as the code, so the two agreed with each other and both
disagreed with Supabase:

| What the code did                                | What the live project does                                                |
| ------------------------------------------------ | ------------------------------------------------------------------------- |
| Posted the code exchange as a form, per RFC 6749 | Parses JSON only; a form gets `400 bad_json`                              |
| Sent OIDC's `redirect_uri` to `/authorize`       | Reads `redirect_to`; anything else lands on the project's Site URL        |
| Required `state` back on the callback            | Does not echo `state` - every sign-in would have ended in `invalid_state` |
| Read `email_verified` from the token to trust it | Puts no `email_verified` in its tokens; every new account was refused     |

## Decision

**`AUTH_BACKEND=supabase` moves the credential, not the session.** Supabase
Auth holds the password, sends the confirmation email and brokers Google and
GitHub. This API is its only client: the browser never talks to Supabase and
never holds a Supabase token. The API verifies Supabase's token - ES256 against
the project's published keys, issuer, audience `authenticated`, expiry - reads
the user once, discards the token, and issues **this system's own session**
exactly as ADR 0022 describes. Revocation, refresh rotation and the device list
are unchanged.

**One identity, whichever way the person came in.** A Supabase user resolves to
an account here through the same federation rules a provider sign-in uses,
linked on `(supabase, auth.users.id)`. Supabase links a password account and a
Google sign-in for the same verified address into one `auth.users` row itself,
so both land on the same account here without this system ever matching on
email.

**The account row here is created at the first sign-in**, not at sign-up. Under
Supabase's default the address must be confirmed first, so sign-up answers
`202 {"confirmation_required": true}` and nothing exists here until the person
follows the link and signs in. That `202` is also what an already-registered
address gets - Supabase answers both the same way - so sign-up stops being the
enumeration surface the Phase 20 code said it could not avoid.

**Supabase gets its own provider class.** `oidc.py` stays for Auth0, which
speaks standard OIDC. Supabase's protocol lives in `app/auth/supabase.py`,
written from requests made to a live project; `state` travels inside
`redirect_to`, and confirmation is read from `user.email_confirmed_at` after
checking that the body names the same user as the signed token.

**Explicit, never inferred.** `AUTH_BACKEND` defaults to `local` and is never
switched by the presence of Supabase settings: a mistyped key must not quietly
move a deployment's passwords. `supabase` without a configured project refuses
to start. The `local` backend remains for the test suite and for development
without a project.

## Consequences

- **Supabase is now on the sign-in path.** An outage there is an outage of
  sign-in and sign-up. Existing sessions are unaffected - they are this
  system's tokens - so it does not sign anybody out.
- **Every call comes from one address.** Supabase rate-limits by IP, and all
  users share the API's. `SUPABASE_SECRET_KEY` lets the API forward each
  person's own address in `Sb-Forwarded-For`, which Supabase honours only with a
  secret key and only once IP forwarding is switched on in the project. A
  deployment with real traffic needs both.
- **The built-in mailer is not a production mailer.** It only delivers to the
  project's team and is capped at a few messages an hour; every other address
  fails with `email_address_not_authorized`, mapped here to
  `503 email_delivery_unavailable`. Custom SMTP is a launch requirement.
- **The password policy has to be set in two places.** This API applies
  `MIN_PASSWORD_LENGTH` before calling Supabase, but the publishable key is
  public by design, so Supabase's own minimum must match or sign-up can be
  called around this API with a weaker password. A new project's minimum is
  six characters - measured, not assumed: the live project refused `abc` with
  "at least 6 characters".
- **Existing local accounts do not move.** An Argon2id hash cannot become a
  Supabase password without the password. Nothing has been deployed, so no real
  accounts exist; a deployment that had them would need to keep `local` or have
  people sign up again.
- **Still unverified:** a successful live sign-in, and the Google and GitHub
  round trips. The first needs a confirmed account in the project; the second
  needs OAuth applications registered with Google and GitHub. Every request
  shape up to that point has been run against the live project.
