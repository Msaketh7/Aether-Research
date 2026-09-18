"""Controls that protect the public surface itself (Phase 20).

Distinct from ``app/auth``, which answers *who is calling*. These answer *how
much they may call*, *where they are calling from*, and *what the system
records about it*:

* ``ratelimit`` - token buckets per identity and route class (TDD 3.2);
* ``forwarded`` - the client address, resolved through declared proxy hops
  rather than trusted from a header;
* ``audit`` - the request-scoped recorder behind ``audit_log``.

The SSRF guard, the untrusted-content boundary and the prompt-injection
defences are not here: they protect the system from what it *fetches*, and they
live with the code that does the fetching, in ``app/sources``.
"""
