# Traefik server

A portable Docker Compose gateway for a Linux server, VPS or ARM host. It keeps
HTTP/HTTPS routing, private-service access rules, optional monitoring,
automatic DNS-challenge certificates, and Apple Network Relay forwarding
and profile signing. Domains, backend addresses, access networks and credentials
come from a local `.env`.

The full Prometheus server has been replaced with an optional **metrics agent**.
It scrapes only Traefik and forwards samples to a separate remote-write endpoint.
Request logs stay in rotated Docker logs. No metrics destination is enabled until
you provide its URL.

## New server

Install Docker with Compose, Git, Python 3 and Python venv support. Then:

```sh
git clone https://github.com/dkagramanyan/traefik.git
cd traefik
./manage setup --domain example.com --email admin@example.com
```

Supply DNS-provider credentials privately. Import network settings from the
admin UI as described below. Setup generates missing dashboard/Grafana passwords and the signing
token once, derives hostnames and URLs, and creates local directories. It keeps
existing values. Generated credentials are stored in `.env` with mode `0600` and
are never printed. Setup does not contact your DNS provider or start containers.

Start the services using the Docker permissions configured on your server:

```sh
sudo docker compose config --quiet &&
sudo docker compose up -d --build
```

`./manage up` is a shortcut that reruns setup and starts Compose for accounts
with direct Docker access. Rerun `./manage setup` after editing `.env`, then
recreate the services. It refreshes the optional metrics agent's runtime config.
Use backend addresses reachable from this server over its LAN, Docker network
or VPN; setup does not create the network connection to another location.

## Adopt your existing deployment

Use this path if your current folder is not a Git checkout:

```sh
git clone https://github.com/dkagramanyan/traefik.git ~/traefik-config &&
~/traefik-config/manage adopt --fix-permissions ~/traefik &&
cd ~/traefik &&
sudo docker compose config --quiet &&
sudo docker compose up -d --build --remove-orphans
```

The helper imports the existing domain, DNS resolver settings, dashboard hash,
Grafana password, backend URLs, trusted networks, certificate paths and legacy
hostname mapping into `.env`. It keeps the Docker project name and existing
Grafana volume, ACME store, certificates and other local credentials. It backs
up replaced configuration in ignored `migration-backups/` with private
permissions, then adds Git metadata. It never restarts containers.

Permission checks run before backups or configuration copies. If old configuration
files are restricted, `--fix-permissions` invokes sudo to give the calling user
access to configuration files and directories. Certificate stores, private-key
files, service data and existing backup contents retain their ownership. The
migration itself runs as your normal user. The `&&` chain stops if any step fails.

If an earlier attempt stopped with `Permission denied`, use the existing clone:

```sh
git -C ~/traefik-config pull --ff-only &&
~/traefik-config/manage adopt --fix-permissions ~/traefik &&
cd ~/traefik &&
sudo docker compose config --quiet &&
sudo docker compose up -d --build --remove-orphans
```

`--remove-orphans` removes the previous Prometheus container. Its old data volume
is retained. Do not run `docker compose down -v` during migration. The previous
application scraping jobs belong on your central monitoring server; this
worker monitors Traefik only. Point Grafana data sources at your central query
server; the agent has no query API. The backup retains the old Prometheus YAML.

If you already deployed the earlier version of this repository, import the old
tracked settings during this upgrade:

```sh
previous_revision=$(git rev-parse HEAD)
git pull --ff-only
./manage setup --import-revision "$previous_revision"
sudo docker compose up -d --build --remove-orphans
```

Later updates use `git pull --ff-only`, `./manage setup`, and Compose as above.
Keep custom Compose overrides in sync; an old override may still add removed
services or change the signer build.

## Environment and credentials

`.env.example` lists all settings. `.env`, `.runtime/`, `state/`, `certs/`,
`letsencrypt/` and migration backups are ignored by Git. Never replace an existing
`.env` with the example file.

| Settings | Purpose |
|---|---|
| `DOMAIN`, `ACME_EMAIL` | Primary domain and certificate account email |
| `ACME_DNS_PROVIDER`, provider-specific variables | DNS challenge and provider credentials |
| `ACME_DNS_RESOLVERS`, `ACME_DNS_DELAY`, `ACME_CA_SERVER` | Existing ACME behavior; production Let's Encrypt by default |
| `ACME_STORAGE_DIR`, `CERTIFICATES_DIR` | Persistent local certificate directories |
| `DASHBOARD_AUTH` | Existing or generated bcrypt dashboard login hash |
| `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD` | Generated dashboard login when no hash exists |
| `GRAFANA_ADMIN_PASSWORD` | Existing or generated Grafana initialization password |
| `BACKEND_OPENVPN`, `SERVICE_ROUTES_JSON` | Core backend and private UI-generated service inventory |
| `TRUSTED_CIDRS` | Comma-separated source networks allowed into private websites |
| `PORTAL_CIDRS` | Sources allowed into the guest portal listener (the OpenVPN server); empty uses `TRUSTED_CIDRS` |
| `BACKEND_HOST_ALIAS`, `BACKEND_HOST_ADDRESS` | Optional legacy hostname mapping |
| `HTTP_PORT`, `HTTPS_PORT`, `APPLE_RELAY_PORT`, `LISTEN_ADDRESS` | Host port bindings |
| `TRAEFIK_NETWORK`, `COMPOSE_PROJECT_NAME` | Docker networking and existing volume ownership |
| `EXTRA_TLS_CERTIFICATES` | JSON list of local certificate/key paths under `/certs` |
| `METRICS_REMOTE_WRITE_URL`, `METRICS_USERNAME`, `METRICS_PASSWORD`, `METRICS_BEARER_TOKEN` | Optional central metrics destination and authentication |
| `RELAY_PROFILE_SIGNING_TOKEN` | Shared random secret for signed Apple profiles |

Unknown existing provider variables are preserved and passed through to Traefik.
Setup cannot obtain DNS API credentials from a provider or recover passwords
from hashes. If a dashboard hash exists, its login is kept. To generate a new
hash, set the desired username/password and clear `DASHBOARD_AUTH` before setup.
Legacy bcrypt hashes with doubled `$$` escapes are imported without changing
the password. Setup writes the canonical hash in single quotes; do not double
its dollar signs again. See [Compose dotenv quoting](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/#env-file-syntax).
Grafana keeps its current password in its existing database; its initialization
variable does not reset that database password.

## Automatic domain certificates

Automatic issuance **and renewal remain enabled**, using the same Traefik ACME
flow as before. The dashboard router requests `DOMAIN` and `*.DOMAIN` with the
`letsencrypt` resolver. Traefik renews these certificates automatically and
persists account/certificate state under `ACME_STORAGE_DIR`. Keep that directory
when moving the deployment.

Selectel v2 remains the default DNS provider. Supply its existing values in `.env`:

```dotenv
SELECTELV2_ACCOUNT_ID=YOUR_ACCOUNT_ID
SELECTELV2_PROJECT_ID=YOUR_PROJECT_ID
SELECTELV2_USERNAME=YOUR_USERNAME
SELECTELV2_PASSWORD=YOUR_PASSWORD
```

Other supported DNS providers work by changing `ACME_DNS_PROVIDER` and adding
their documented environment variables. Setup fills local defaults and secrets;
it leaves provider-issued credentials for you to supply. See the official
[Selectel v2 variables](https://go-acme.github.io/lego/dns/selectelv2/) and
[Traefik ACME renewal documentation](https://doc.traefik.io/traefik/reference/install-configuration/tls/certificate-resolvers/acme/).

Certificates supplied as local `.crt`/`.key` files retain their existing renewal
process. Traefik serves them and reloads changes; it does not renew arbitrary
imported file certificates through ACME.

## Network settings from the OpenVPN UI

This repository ships no addresses. The OpenVPN server's **Settings → Network**
page produces a gateway file, `network.private.json`, with everything the
gateway needs to know about it:

| In the file | Becomes in `.env` |
|---|---|
| domain, website hostname | `DOMAIN`, `OPENVPN_HOST` |
| the server's web address (`http://<ip>:8080`) | `BACKEND_OPENVPN`; its IP also `SIGNER_SOURCE_CIDRS` and `PORTAL_CIDRS` |
| relay hostname, public URL, backend (`<ip>:8444`) | `APPLE_RELAY_HOST`, `APPLE_RELAY_URL`, `APPLE_RELAY_BACKEND` |
| guest portal listener (`<gateway LAN ip>:9443`) | `PORTAL_LISTEN_ADDRESS`, `PORTAL_HTTPS_PORT` |
| trusted networks (optional) | `TRUSTED_CIDRS`; absent or empty keeps the current list |
| additional websites (optional) | `SERVICE_ROUTES_JSON`; absent keeps the current list |

Copy the file into this folder and run:

```sh
./manage setup --network-file ./network.private.json
sudo docker compose config --quiet && sudo docker compose up -d
```

The importer validates every value before writing it to `.env` (mode 0600) and
keeps all credentials. The file contains no secrets but describes your network;
it is ignored by Git, keep it private. Files from 1.x development builds
(version 1) are refused: download the file again.

### Guest portal

Guests of the OpenVPN server and its Apple relay reach only their personal
portal. The OpenVPN server sends every HTTPS connection of a guest to this
gateway's **portal listener** (entrypoint `portal`, `dynamic/portal.yml`):

* it listens on `PORTAL_LISTEN_ADDRESS:PORTAL_HTTPS_PORT` (the LAN address
  from the gateway file, port 9443; `127.0.0.1` until one is set) and needs
  no port forward from the internet;
* it accepts connections only from `PORTAL_CIDRS` (the OpenVPN server's
  address; empty falls back to `TRUSTED_CIDRS`), checked on the connection
  source, never on `X-Forwarded-For`;
* it routes only `OPENVPN_HOST` and adds `X-OpenVPN-Portal-Only: 1`, so the
  OpenVPN UI answers only the portal pages (`/` redirects to `/portal`, the
  admin API is 404). Other hostnames get 404.

The regular HTTPS listener has no portal exception: the OpenVPN website there
is for `TRUSTED_CIDRS` only.

Check after deploying: from the OpenVPN server,
`curl -k --resolve openvpn.<domain>:9443:<gateway ip> https://openvpn.<domain>:9443/api/clients`
returns 404 and `.../portal` the sign-in page; from any other machine the
listener answers 403.

### Automatic profile signing

Setup enables the `profile-signing` Compose profile and generates
`RELAY_PROFILE_SIGNING_TOKEN` once. The signer is built from a pinned revision
of the openvpn-server repository, reads the ACME store through a read-only
mount (its only capability is reading files it does not own) and picks up
renewed certificates by itself. It publishes no port: Traefik routes
`https://<OPENVPN_HOST>/internal/relay-sign` to it for `SIGNER_SOURCE_CIDRS`
only, and every request also needs the token.

On the OpenVPN server, open **Relay → Profile signing** and enter
`https://openvpn.<domain>/internal/relay-sign` and the same token (or set
`OVPN_RELAY_SIGNER_URL` and `RELAY_PROFILE_SIGNING_TOKEN` in its `.env`). The
OpenVPN UI checks every signed file before handing it out. Installed profiles
keep working when the signer is down; only new downloads need it.

## Metrics worker and Docker logs

Fill `METRICS_REMOTE_WRITE_URL` when your separate server is ready:

```dotenv
METRICS_REMOTE_WRITE_URL=https://prometheus.example.com/api/v1/write
# Optional: use basic auth OR a bearer token.
METRICS_USERNAME=
METRICS_PASSWORD=
METRICS_BEARER_TOKEN=
```

Run `./manage setup` and recreate Compose. Setup enables the `metrics` profile
when the URL is nonempty. Otherwise it leaves the worker disabled. After clearing
the URL, use `--remove-orphans` to stop a previously enabled worker.

The worker runs Prometheus in **agent mode**: only the `traefik:8082` scrape
job, remote write, and a persistent WAL buffer under `state/metrics-agent/`.
It publishes no port and runs no query/alerting server. Generated config and
credential files under `.runtime/` come from `.env` and have private permissions.
The central Prometheus must accept remote write (for Prometheus, enable
`--web.enable-remote-write-receiver`). See
[Prometheus agent mode](https://prometheus.io/docs/prometheus/latest/prometheus_agent/).

Read request and service logs with:

```sh
sudo docker compose logs -f --tail=100 traefik
sudo docker compose --profile metrics logs --tail=100 metrics-agent
sudo docker compose logs --tail=100 profile-signer
```

Docker rotates logs at 10 MB × 3 files per service. Request headers are omitted
from access logs. Metrics stay internal to the Docker network.

## Validation

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python scripts/validate.py
.venv/bin/python -m pytest tests -q
.venv/bin/python scripts/test_traefik.py
```

The Docker test uses fake domains, temporary certificates and isolated containers;
it makes no production ACME requests. It exercises real environment rendering,
all routes, public/private access, relay TLS termination with h2 ALPN, and actual
agent forwarding to a temporary remote-write receiver. Temporary containers and
networks are removed afterwards. Migration tests reproduce root-owned configuration
files, check scoped permission repair, and verify legacy bcrypt logins. CI also
builds the pinned profile signer.

## Production update

Update the OpenVPN server first (its README, *Upgrading to 2.0*), download the
gateway file there, copy it into this folder, then:

```sh
cd ~/traefik &&
git pull --ff-only &&
./manage setup --network-file ./network.private.json &&
sudo docker compose config --quiet &&
sudo docker compose up -d --build --remove-orphans
```

Afterwards use **Save and restart VPN** on the OpenVPN server. Keep `.env`,
`letsencrypt/`, `certs/` and the Docker volumes; never run `docker compose down -v`.
