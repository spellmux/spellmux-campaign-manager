# ADR 0002: Make the administrative CLI a first-class interface

## Status

Accepted

## Decision

Installation, diagnostics, backup, upgrade, and recovery will be available through
`campaignctl`. Unraid deployments use the same containers and commands as other
Docker hosts, with SSH as the initial remote transport.

## Consequences

- Deployments remain auditable and scriptable.
- An Unraid Community Applications template can remain a thin presentation layer.
- MCP can manage campaign workflows without receiving general host access.
- Remote administrative access must be protected by SSH keys and a VPN/tailnet.

