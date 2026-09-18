"""Who is calling: principals, passwords, sessions and cookies (FR-1).

Authorisation has been enforced since Phase 2 - every research object carries a
``user_id`` and every read is scoped by it. Phase 20 puts a real identity
underneath those rules: Argon2id passwords, opaque server-side sessions in
Postgres, and an `HttpOnly` cookie (ADR 0021).

The modules divide by what they can get wrong:

* ``passwords`` - the slow hash, the timing equaliser and the policy;
* ``sessions`` - minting, hashing at rest, resolving, revoking;
* ``cookies`` - every attribute of the ``Set-Cookie`` line, in one place;
* ``service`` - registration and sign-in, and the rules that make the two
  indistinguishable from the outside when they fail;
* ``principal`` - resolving a request to a caller, including the development
  identity and the allowlist that keeps it out of a real deployment.
"""
