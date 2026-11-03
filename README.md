# Raspberry Pi Traefik deployment

Configuration for `dgkagramanyan.ru`, running on the Raspberry Pi at
`170.134.51.13`. This repository owns Traefik, its website routes, Prometheus,
Grafana and the optional Apple relay profile-signing deployment.

The VPN server, relay engine, user accounts and application code remain in
[openvpn-server](https://github.com/dkagramanyan/openvpn-server). The signer
is built directly from a pinned public source revision of that application;
the Pi needs only this deployment checkout. The repositories communicate over
the existing relay and HTTPS signing endpoints.

## Adopt the existing deployment

Keep the working directory `~/traefik`, its local files and its Docker project
name. This preserves the existing Prometheus/Grafana named volumes and ACME
certificate store.

Clone this private repository into a temporary adjacent directory, using a
GitHub identity that can read it:

```sh
git clone git@github.com:dkagramanyan/traefik.git ~/traefik-config
python3 ~/traefik-config/scripts/adopt.py ~/traefik
cd ~/traefik
sudo docker compose config --quiet
sudo docker compose up -d --build
```

The helper backs up existing configuration in a private, ignored
`migration-backups/` directory, moves the simple inline Grafana password into
`.env`, adds Git metadata and writes the versioned configuration. It preserves
existing environment values, `certs/`, `letsencrypt/`, `kuma_api_key` and Docker
volumes. It never starts or restarts containers. If the deployment already has
Git metadata or a password cannot be safely migrated, it stops before changing
the files; follow the error message instead of replacing keys or data.

Do not use `docker compose down -v` during migration. Later updates are:

```sh
cd ~/traefik
git pull --ff-only
sudo docker compose up -d --build
```

## Files and local state

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Existing Traefik, Prometheus and Grafana stack; optional signer |
| `traefik.yml` | Entry points, Docker/file providers, Selectel DNS challenge, metrics |
| `dynamic/native-services.yml` | Existing public/private routes, relay TCP route, Zoraxy fallback |
| `dynamic/portal.yml` | Public client-only profile download page and API |
| `dynamic/certs.yml` | Paths to the existing wiki/cloud certificates; no key material |
| `prometheus.yml` | Existing Traefik, wiki, Kuma and home-PC scrape targets |
| `.env` | Local credentials and Compose settings; ignored |
| `certs/`, `letsencrypt/` | Local certificates, private keys and ACME account store; ignored |
| `kuma_api_key` | Local Kuma metrics credential; ignored |

`.env.example` contains names and placeholders only. Preserve the existing
SelectelV2 DNS-provider variables and `DASHBOARD_AUTH` password hash. Put the
Grafana password in `GRAFANA_ADMIN_PASSWORD`. Existing Grafana installations
keep the database password; changing its initial-password environment variable
does not reset that database password. Never commit `.env`, API keys, certificate
files or migration backups.

The supplied service image tags match the existing stack. Upgrading or pinning
them is a separate, deliberate change. The validation fixture uses Traefik
`v3.7.15` to check the configuration without contacting a production ACME service.

## Network paths

* TCP 80 and 443 reach Traefik. Native HTTP redirects are defined in the dynamic
  file so other domains can still reach Zoraxy's HTTP challenge handling.
* TCP 8443 reaches Traefik's `apple-relay` entry point. Its TLS TCP router sends
  decrypted HTTP/2 to the OpenVPN host at `170.134.51.30:8444`.
* Existing website management routes use `trusted-only`: LAN
  `170.134.51.0/24`, WireGuard `10.10.10.0/24`, full-access OpenVPN
  `10.0.70.0/25`. Guest VPN addresses are excluded.
* `/portal`, `/api/portal` and static assets on `openvpn.dgkagramanyan.ru` are
  public so a new user can sign in and download their own profiles. The OpenVPN
  application enforces client-only access; management APIs keep the private rule.
* The existing `whoami` diagnostic router is public, as in the supplied config.

Keep the router's TCP 8443 forwarding to `170.134.51.13:8443`. No new WAN port
is required for signing or the portal.

## Automatic profile signing

Traefik already obtains and renews the HTTPS certificate. The optional signer
uses that same identity to sign downloaded `.mobileconfig` files. It does not
replace certificate renewal or require manual certificate copying.

Generate a private shared token once with `openssl rand -hex 32`. On the Pi,
merge these entries into the existing `.env`:

```dotenv
COMPOSE_PROFILES=profile-signing
RELAY_PROFILE_SIGNING_TOKEN=YOUR_GENERATED_VALUE
```

Then `sudo docker compose up -d --build` starts the signer alongside the existing
services. Its image builds from the pinned public source in
`PROFILE_SIGNER_SOURCE`; no extra local OpenVPN clone is required. The service
reads the ACME directory through a read-only mount and publishes no host port.
An HTTPS route at `/internal/relay-sign` accepts only the OpenVPN host
`170.134.51.30`, and the signer also requires the shared token.

On the **OpenVPN host**, merge into its own `.env`:

```dotenv
OVPN_RELAY_SIGNER_URL=https://openvpn.dgkagramanyan.ru/internal/relay-sign
RELAY_PROFILE_SIGNING_TOKEN=THE_SAME_GENERATED_VALUE
OVPN_UI_TRUSTED_PROXIES=127.0.0.1,170.134.51.13
OVPN_UI_SECURE_COOKIES=true
```

Recreate the OpenVPN services with `sudo docker compose up -d --build`.
The application's **Relay** page should show **Signed downloads enabled**.
The UI verifies the signature and exact contents before returning the file.
Installed relay access does not depend on the signer staying online; creating
new signed downloads does.

Create personal website logins in **Clients → client → Portal access**.
Users then sign in at `https://openvpn.dgkagramanyan.ru/portal`.

## Validation

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python3 scripts/validate.py
python3 -m pytest tests -q
python3 scripts/test_traefik.py
```

Configuration validation uses fake environment values in a temporary directory
and never prints expanded secrets. The Docker test uses temporary certificates,
disables ACME and Docker discovery, loads the actual dynamic routes and checks
that they are enabled. It publishes only a temporary loopback diagnostic port
and cleans up its container afterwards. CI also builds the optional signer from
the pinned source.
