# Security policy

## Supported versions

The default branch and the latest tagged marketplace release are supported. Older plugin
versions are immutable content and are not silently rewritten; report a vulnerable version
so it can be deprecated in registry metadata and documented for upgrade.

## Reporting

Report suspected vulnerabilities, malicious plugin content, leaked credentials, or supply-chain
tampering privately through GitHub Security Advisories for `Chainabit/plugins`. Do not open a
public issue containing a secret, exploit, private endpoint, or proof-of-concept that can harm
users. If GitHub private reporting is unavailable, contact the maintainers through the verified
Chainabit organization profile and include only the minimum reproduction details.

Maintainers will acknowledge a report, triage impact, quarantine affected listings, preserve
immutable evidence, notify affected users when appropriate, and publish remediation guidance.

## Plugin security expectations

Plugins are third-party content and must assume hostile inputs. Authors must disclose requested
authorities, required and optional dependencies, network/workspace access, executable scripts,
and structured installation packages. Portable `SKILL.md` content is not an authority grant.
Scripts execute only in a runtime sandbox; host execution, package installation, credentials,
network, and workspace access require separate runtime policy and explicit consent.

Raw shell lifecycle commands, mutable branch-only sources, path traversal, symlinks, binaries,
undeclared execution, fake signatures, and self-authored trust claims are rejected by CI.

## Supply-chain incidents

File hashes, package digests, immutable source revisions, publisher identity, registry trust,
and security review are separate signals. A valid digest does not make a publisher trusted.
Trust and telemetry (`downloads`, freshness, verification, review, and stale state) are owned by
registry services, not by `marketplace.json` contributors. Signed attestations must be verified
against a configured key ownership/trust policy before being displayed as verified.

## Repository controls

The repository ships CI validation and a credential-focused `.gitignore`. GitHub secret scanning,
push protection, required checks, branch protection, and private vulnerability reporting are
repository/organization settings, not claims that can be safely asserted from this tree. See
[`docs/governance.md`](docs/governance.md) for the exact configuration checklist.
