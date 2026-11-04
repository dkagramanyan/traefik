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
| `PORTAL_CIDRS` | Portal-only source networks, including permitted guests; empty uses `TRUSTED_CIDRS` |
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

## Network settings from the admin UI

The repository ships no LAN inventory. Configure addresses, domains and additional
services in the OpenVPN admin UI under **Settings → Network and private portal**.
Network settings are private runtime data, not source code or test fixtures.

1. Review the shared domain, website/relay addresses and internal backend in
   the guided settings. Detection can suggest editable internal IPs; existing
   public addresses and ports stay as configured.
2. Existing additional services are kept by default. To replace their inventory,
   enable **Manage and replace the gateway's additional services here**, then
   include every required service. New services are private by default.
3. Choose **Save and download Traefik settings**. Copy the resulting
   `network.private.json` privately to the gateway and run:

   ```sh
   ./manage setup --network-file ./network.private.json
   sudo docker compose config --quiet && sudo docker compose up -d traefik
   ```

The importer validates the file, preserves existing credentials and writes the
necessary values to the gateway's ignored, mode-0600 `.env`. You do not enter
network values in `.env` manually. DNS-provider credentials remain private setup
secrets. Export files are ignored by Git; keep them private after download.
The application cannot directly configure a separate gateway without a management
connection, so transferring the generated file is an explicit deployment step.

Before upgrading an older deployment, retain a private configuration backup.
Adoption of a literal legacy routing file preserves additional routes in ignored
`dynamic/imported-private.yml`. New UI exports preserve that inventory unless
service replacement is explicitly enabled. Older exports without a replacement
flag retain their original replacement behavior. Never commit generated runtime
configuration. Public relay URLs can be imported for signing, but importing them
does not change the gateway's existing listener ports or router port forwards.

### Private portal for LAN, OpenVPN guests and Apple Relay guests

Configure guest access in the OpenVPN UI with **Allow guests to use the OpenVPN
website**. It suggests the gateway address, website DNS answers and a private
website listener. Detailed overrides remain under **Advanced settings → Website routing**. Export and deploy the gateway settings before
restarting VPN.

The guest subnet and VPN/relay server egress IP belong in `PORTAL_CIDRS`, separate
from `TRUSTED_CIDRS` for other private services. Filtering uses the connection
source address: [Traefik's IPAllowList](https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/ipallowlist/)
can see a NAT egress address instead of an individual guest. A forged forwarded
header cannot grant access.

The OpenVPN settings page does not require re-entering administrator source
networks. An empty gateway `trusted_cidrs` list keeps the gateway’s existing
`TRUSTED_CIDRS` (loopback-only on a fresh setup). Those existing administrator
sources also remain allowed on portal/static paths so a website update cannot
lock out their admin browser. Guest sources are added only to `PORTAL_CIDRS`.
A nonempty explicit override still replaces `TRUSTED_CIDRS`.

The isolated listener, normally gateway IPv4:9443, forwards only the OpenVPN
hostname. It supplies `X-OpenVPN-Portal-Only: 1`; the OpenVPN service owns the
URL boundary and returns 404 for administrative APIs, even with an admin cookie.
Opening its root redirects to `/portal`. This header only restricts access and
never grants authentication or permissions. TLS and source filtering remain in
Traefik; profile signing remains next to the gateway's private certificate keys.
Update OpenVPN before deploying this route contract.

The browser URL stays `https://your-openvpn-host/portal`. The VPN/relay service
routes only guest website traffic to the isolated listener, leaving other LAN
blocks intact. Other hostnames on that listener return 404. Keep it private;
it needs no WAN port forward. The shared HTTPS listener retains its separate
personal-portal path router so existing administrator access still works.

A listener can be exported before guest access is enabled. After deployment,
use **Save and restart VPN** to apply the website exception. This briefly
reconnects VPN clients. Older allowlist-only exports remain accepted for
networks with an existing route; they do not bypass guest destination blocks.

Verify after deployment: LAN and both guest transports can sign in/download;
other private websites and administrator APIs fail through the isolated route;
an external connection with VPN/relay disabled receives 403 for `/portal`.
The private listener returns 404 for `/api/clients`, even from an allowed LAN
source. First-time users receive their initial profile privately or from the LAN.

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
networks are removed afterwards. Migration tests reproduce root-owned configuration
files, check scoped permission repair, and verify legacy bcrypt logins. CI also
builds the pinned profile signer.

## Production update

Update OpenVPN/relay first using that repository's
[production guide](https://github.com/dkagramanyan/openvpn-server#production-update-openvpnrelay-and-traefik).
In its Settings page, review the detected addresses and download
`network.private.json`. Copy that file into `~/traefik/` on the gateway, then run:

```sh
cd ~/traefik &&
git pull --ff-only &&
test -f ./network.private.json &&
./manage setup --network-file ./network.private.json &&
sudo docker compose config --quiet &&
sudo docker compose up -d --build --remove-orphans
```

Use the actual downloaded file. `/path/to/network.private.json` is a placeholder,
not a file provided by Git. Preserve `.env`, certificates, ACME state and signing
credentials. Apply the gateway before restarting VPN for a website routing change.
