# Contributing plugins

1. Put distributable content below one canonical category directory. Repository tooling belongs
   in `tooling/`, contracts in `spec/`, and operational registry data outside the authored index.
2. Keep portable Agent Skills content at `skills/<name>/SKILL.md`. Directory skills may add
   `scripts/`, `references/`, and non-executable `assets/`; regenerate `bundle.json` after changes.
3. Declare exact plugin metadata, composition, required/optional dependencies, capabilities, and
   granular `permissions.requested`. A declaration requests authority; it never grants runtime access.
4. Use `installation.packages` with an exact version and registry integrity. Do not add shell
   command strings or signature-looking placeholders.
5. Add exactly one authored listing. It must include the immutable revision and package digest;
   generate the digest using the contract helper after the payload commit is available.
6. Run `node tooling/validate-marketplace.mjs`, `node --test tooling/marketplace-contract.test.mjs`,
   and a representative composition resolution before opening a pull request.

The CI check is required for pull requests and protected branch updates. A failed validator is a
contract failure, not a formatting preference. Compatibility aliases must declare `composition.role`
`compatibility`, `composition.aliasOf`, and the canonical dependency without duplicating payload.
