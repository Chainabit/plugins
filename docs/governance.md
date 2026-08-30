# Repository governance and required settings

## Verified repository-side controls

- `.github/workflows/marketplace.yml` runs validation on pull requests and pushes to `main` and
  `development-rebased`.
- `.github/CODEOWNERS` assigns contract, tooling, provider, security, and catalog paths.
- The validator fails closed on malformed or semantically unsafe marketplace states.
- `.gitignore` covers common environment, credential, private-key, certificate, and package-auth files.

## Required GitHub configuration

An organization administrator must verify and enable:

- Settings → Code security and analysis → Secret scanning and Push protection.
- Settings → Branches → rules for `main` and `development-rebased`: require pull requests,
  require the exact `marketplace / validate` status check, require CODEOWNER review, dismiss stale
  approvals, require branches to be up to date, and block force pushes/deletions.
- Settings → Security → Private vulnerability reporting / GitHub Security Advisories.
- Restrict workflow token permissions to read-only by default and review third-party actions.

The checked-in files cannot observe these account-level settings. Their enabled state is therefore
an unverified assumption until an administrator records evidence in the repository governance log.
