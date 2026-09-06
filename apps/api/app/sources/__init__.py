"""Source discovery and the web content pipeline (Phase 6).

Boundary: the only module allowed to make outbound requests to the open
internet. It owns the SSRF guard, fetch timeouts, content sanitisation and
deduplication. Everything it returns is untrusted data by definition - see
docs/threat-model.md section 3.1.
"""
