# ADR 0001: Treat OtterWiki as a publishing target

## Status

Accepted

## Decision

Campaign Manager owns structured campaign data and its player portal. OtterWiki
is supported through a publishing adapter and is not bundled or required.

## Consequences

- Existing OtterWiki users retain their public wiki and Git history.
- Deployments without OtterWiki remain fully functional.
- Authentication and permissions are not shared between applications.
- Publication needs stable ownership and conflict rules for generated pages.

