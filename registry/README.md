# Registry-owned state

This directory documents an ownership boundary; it is not a mutable telemetry store.

`marketplace.json` is authored catalog metadata: identity, version, source, immutable revision,
and package integrity. Registry services own downloads, freshness, stale state, health, and index
telemetry. Trust services own publisher verification, key ownership, signature/attestation status,
security review, and trusted-publisher assertions. CI may publish signed build/test/integrity results
to those services, but contributors cannot set a trust badge by editing this repository.

The absence of a trust record means “not verified”, never “verified false because an author said so”.
