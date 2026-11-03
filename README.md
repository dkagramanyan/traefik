# Traefik server

A portable Docker Compose gateway for a Linux server, VPS or ARM host. It keeps
HTTP/HTTPS routing, private-service access rules, the secondary proxy fallback,
Grafana, automatic DNS-challenge certificates, and Apple Network Relay forwarding
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

Edit `.env` to supply DNS-provider credentials, reachable backend URLs and trusted
networks. Setup generates missing dashboard/Grafana passwords and the signing
token once, derives hostnames and URLs, and creates local directories. It keeps
existing values. Generated credentials are stored in `.env` with mode `0600` and
are never printed. Setup does not contact your DNS provider or start containers.

Start the services using the Docker permissions configured on your server:

```sh
sudo docker compose config --quiet
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
git clone https://github.com/dkagramanyan/traefik.git ~/traefik-config
~/traefik-config/manage adopt ~/traefik
cd ~/traefik
sudo docker compose config --quiet
sudo docker compose up -d --build --remove-orphans
```

The helper imports the existing domain, DNS resolver settings, dashboard hash,
Grafana password, backend URLs, trusted networks, certificate paths and legacy
hostname mapping into `.env`. It keeps the Docker project name and existing
Grafana volume, ACME store, certificates and other local credentials. It backs
up replaced configuration in ignored `migration-backups/` with private
permissions, then adds Git metadata. It never restarts containers.

`--remove-orphans` removes the previous Prometheus container. Its old data volume
is retained. Do not run `docker compose down -v` during migration. The previous
Kuma/wiki/home-PC scraping jobs belong on your central monitoring server; this
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
| `BACKEND_*`, `ZORAXY_TLS_BACKEND` | Each service's independent backend address |
| `TRUSTED_CIDRS` | Comma-separated source networks allowed into private websites |
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

## Existing routes and Apple relay

* HTTP 80 redirects the primary domain to HTTPS. Other domains still pass to the
  secondary proxy, including its HTTP challenge handling.
* HTTPS 443 keeps the existing public status routes and diagnostic `whoami`,
  private website routes, and encrypted TCP fallback for other domains.
* Private website requests use `TRUSTED_CIDRS`. Import retains the existing LAN,
  WireGuard and full-access OpenVPN ranges, excluding guest VPN addresses.
* `/portal`, `/api/portal` and static files on `OPENVPN_HOST` are public. The
  OpenVPN application checks user authentication and ownership; management APIs
  keep their private access rule.
* Apple relay TLS terminates on `APPLE_RELAY_PORT` with ALPN `h2`; its TCP router
  forwards decrypted HTTP/2 to `APPLE_RELAY_BACKEND`. Keep this backend hop on a
  trusted network. An ordinary HTTP router cannot replace this CONNECT route.

Forward public TCP 80, 443 and the configured relay port to this server. Keep
`APPLE_RELAY_HOST` covered by the primary domain certificate. If moving the
proxy to a different address, update `APPLE_RELAY_TRUSTED_PROXIES` on the OpenVPN
host to the new proxy's actual source IP. Backend guest/full access checks remain
in the OpenVPN relay engine.

### Automatic profile signing

Setup enables the `profile-signing` Compose profile and generates its shared
token if missing. The signer builds from a pinned public application revision,
reads the ACME directory through a read-only mount and reloads renewed identities.
It needs no second source checkout and exposes no host port. Its read-only
filesystem capability lets it read a mode-0600 ACME store owned by either the
setup user or Traefik; the certificate directory is mounted read-only.

Only `SIGNER_SOURCE_CIDRS` may call the HTTPS signing path, and a shared token is
also required. Setup derives that source range from the OpenVPN backend's literal
IP when possible. Set it explicitly if the backend uses a hostname or the network
changes its source address.

Copy these values from this server's `.env` into the **OpenVPN host's** `.env`:

```dotenv
OVPN_RELAY_SIGNER_URL=https://openvpn.example.com/internal/relay-sign
RELAY_PROFILE_SIGNING_TOKEN=THE_SAME_GENERATED_VALUE
```

Recreate the OpenVPN UI and check **Relay → Signed downloads enabled**. The
OpenVPN application verifies the signature and profile contents before returning
a file. Traefik keeps renewing the TLS identity; you do not copy private keys to
the OpenVPN machine. Installed relay access does not depend on the signer staying
online; new signed downloads do.

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
networks are removed afterwards. CI also builds the pinned profile signer.
