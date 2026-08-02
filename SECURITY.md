# Security policy

## Supported versions

The project is pre-1.0. Security fixes are applied to the latest revision of `main`; older
commits and local deployment snapshots are not separately supported.

## Reporting a vulnerability

Do not open a public issue for vulnerabilities involving authentication, authorization,
path traversal, command execution, credential exposure, or private campaign data. Use GitHub's
private vulnerability reporting feature for this repository. Include affected versions,
reproduction steps, impact, and any suggested mitigation.

Do not include real credentials, recordings, transcripts, or player information in a report.

## Deployment responsibility

Campaign Manager processes sensitive campaign material. Operators should use unique secrets,
keep administrative endpoints behind an authenticated reverse proxy or private network, install
security updates promptly, and back up PostgreSQL and artifact storage. Never expose the Unraid
SSH service or Docker socket to the public internet.
