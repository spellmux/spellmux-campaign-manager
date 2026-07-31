# Unraid deployment target

Unraid is a supported deployment environment, not an application dependency.

## Initial target

- Unraid 7.1 or later
- Docker enabled
- SSH reachable only over LAN or Tailscale
- CPU-only processing profile
- Containers run as `PUID=99` and `PGID=100` where supported

Provisional paths:

```text
/mnt/user/appdata/campaign-manager       application and database data
/mnt/user/campaign-media                 audio and generated artifacts
/mnt/user/appdata/otterwiki/repository  optional OtterWiki publishing target
```

The OtterWiki path must not be mounted until the publisher implements conflict
checks, locking, preview, and auditable Git commits.

## SSH bootstrap goals

The future `campaignctl install` workflow will:

1. detect Unraid and Docker versions;
2. report CPU, memory, GPU, storage, and existing port/network conflicts;
3. show every directory and container change before applying it;
4. create narrowly scoped application directories;
5. generate protected secrets without printing them;
6. start the database, server, and worker;
7. run migrations and health checks;
8. leave existing OtterWiki and Plex containers unchanged.

## Preview-first installer

The current bootstrap script defaults to a non-mutating preflight:

```bash
bash deploy/unraid/install.sh --check
```

It must be invoked explicitly with `--apply` before it creates persistent paths,
the private Docker network, PostgreSQL, or application containers:

```bash
bash deploy/unraid/install.sh --apply
```

The installer refuses to overwrite existing containers or its protected secrets
file. The initial administrator is created interactively afterward so no password
is placed in shell history or automation logs.

If the initial database pull or startup is interrupted, correct the reported cause
and continue without regenerating credentials:

```bash
bash deploy/unraid/install.sh --resume
```

Do not expose Unraid SSH, the Docker socket, or the campaign administrative API
directly to the public internet.
