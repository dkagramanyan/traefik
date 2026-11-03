"""Environment bootstrap and migration shared by the setup and adoption commands."""
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
from urllib.parse import urlsplit

import yaml
import bcrypt

ROOT = Path(__file__).resolve().parents[1]


def read_env(path):
    """Read ordinary Compose dotenv values without executing shell code."""
    result = {}
    if not Path(path).is_file():
        return result
    for line in Path(path).read_text().splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if value.startswith("'"):
            end = re.search(r"(?<!\\)'", value[1:])
            if end is None:
                raise ValueError(f"Use a single-line dotenv value for {key}")
            value = value[1:end.start()+1].replace("\\'", "'")
        else:
            if value.startswith('"'):
                # JSON escaping is compatible with the values written by this tool.
                value = json.JSONDecoder().raw_decode(value)[0]
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            def expand(match):
                if match.group(0) == "$$": return "$"
                name, fallback, plain = match.groups()
                return result.get(name or plain, os.environ.get(name or plain, fallback or ""))
            value = re.sub(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)", expand, value)
        result[key] = value
    return result


def write_env(path, values, previous=""):
    """Keep comments/unknown settings; quote dollar signs literally for Compose."""
    def quote(value):
        if "\n" in str(value) or "\r" in str(value):
            raise ValueError("Environment values must be single-line")
        return "'" + str(value).replace("'", "\\'") + "'"
    lines, written = [], set()
    for line in previous.splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in values:
            key = match.group(1)
            if key not in written:
                lines.append(key + "=" + quote(values[key]))
                written.add(key)
        else:
            lines.append(line)
    lines.extend(key + "=" + quote(value) for key, value in values.items() if key not in written)
    path = Path(path)
    # Replacing the file avoids briefly publishing secrets under a permissive umask.
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("\n".join(lines) + "\n")
    temp.replace(path)


def load_yaml(path):
    if not path.is_file() or "{{" in path.read_text():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        raise ValueError(f"Fix {path.name} before importing its YAML configuration") from None


def import_existing(folder):
    """Import settings, not private keys, from the old Compose/file deployment."""
    folder = Path(folder)
    values = read_env(folder / ".env")
    static = load_yaml(folder / "traefik.yml")
    acme = static.get("certificatesResolvers", {}).get("letsencrypt", {}).get("acme", {})
    dns = acme.get("dnsChallenge", {})
    for key, value in {
        "ACME_EMAIL": acme.get("email"), "ACME_CA_SERVER": acme.get("caServer"),
        "ACME_DNS_PROVIDER": dns.get("provider"),
        "ACME_DNS_RESOLVERS": ",".join(dns.get("resolvers", [])),
        "ACME_DNS_DELAY": dns.get("propagation", {}).get("delayBeforeChecks"),
    }.items():
        if value and not values.get(key): values[key] = str(value)
    services = load_yaml(folder / "docker-compose.yml").get("services", {})
    grafana_env = services.get("grafana", {}).get("environment", {})
    if isinstance(grafana_env, list):
        grafana_env = dict(line.split("=", 1) for line in grafana_env if "=" in line)
    password = str(grafana_env.get("GF_SECURITY_ADMIN_PASSWORD", ""))
    if password and "${" not in password and not values.get("GRAFANA_ADMIN_PASSWORD"):
        values["GRAFANA_ADMIN_PASSWORD"] = password
    native = load_yaml(folder / "dynamic/native-services.yml")
    http = native.get("http", {})
    for name, service in http.get("services", {}).items():
        servers = service.get("loadBalancer", {}).get("servers", [])
        if servers:
            values.setdefault("BACKEND_" + name.upper().replace("-", "_"), servers[0]["url"])
    trusted = http.get("middlewares", {}).get("trusted-only", {}).get("ipAllowList", {}).get("sourceRange", [])
    if trusted: values.setdefault("TRUSTED_CIDRS", ",".join(trusted))
    routers = native.get("tcp", {}).get("routers", {})
    relay_rule = routers.get("apple-relay", {}).get("rule", "")
    domain = re.search(r"HostSNI\(`([^`]+)`\)", relay_rule)
    if domain:
        values.setdefault("DOMAIN", domain.group(1))
        values.setdefault("APPLE_RELAY_HOST", domain.group(1))
    for name, key in (("apple-relay", "APPLE_RELAY_BACKEND"), ("fallback-zoraxy", "ZORAXY_TLS_BACKEND")):
        servers = native.get("tcp", {}).get("services", {}).get(name, {}).get("loadBalancer", {}).get("servers", [])
        if servers: values.setdefault(key, servers[0]["address"])
    certs = load_yaml(folder / "dynamic/certs.yml").get("tls", {}).get("certificates", [])
    if certs: values.setdefault("EXTRA_TLS_CERTIFICATES", json.dumps(certs))
    # Preserve local hostname mappings without baking machine names into Compose.
    extra_hosts = services.get("traefik", {}).get("extra_hosts", [])
    if isinstance(extra_hosts, dict): extra_hosts = [name + ":" + address for name, address in extra_hosts.items()]
    for mapping in extra_hosts:
        if "${" in mapping: continue
        name, address = mapping.split(":", 1)
        if name != "host.docker.internal":
            values.setdefault("BACKEND_HOST_ALIAS", name)
            values.setdefault("BACKEND_HOST_ADDRESS", address)
            break
    return values


def password_hash(username, password):
    if len(password.encode()) > 72:
        raise ValueError("Use a dashboard password of at most 72 UTF-8 bytes for bcrypt")
    return username + ":" + bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def prepare(root=ROOT, import_from=None, domain=None, email=None, hasher=password_hash, use_local_env=True):
    root = Path(root)
    source = Path(import_from) if import_from else root
    defaults = read_env(root / ".env.example")
    imported = import_existing(source)
    current = read_env(root / ".env") if use_local_env else {}
    values = defaults | imported | current
    # Blank placeholders must not suppress detected values during first migration.
    for key, value in imported.items():
        if not current.get(key): values[key] = value
    if domain: values["DOMAIN"] = domain
    if email: values["ACME_EMAIL"] = email
    domain = values.get("DOMAIN", "").strip().lower().rstrip(".")
    if not domain or not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain):
        raise ValueError("Set DOMAIN in .env or run ./manage setup --domain your-domain.example")
    values["DOMAIN"] = domain
    derived = {
        "ACME_EMAIL": "admin@" + domain, "OPENVPN_HOST": "openvpn." + domain,
        "APPLE_RELAY_HOST": domain, "METRICS_INSTANCE": socket.gethostname(),
        "LOCAL_UID": str(os.getuid()), "LOCAL_GID": str(os.getgid()),
    }
    for key, value in derived.items():
        if not values.get(key): values[key] = value
    if not values.get("OVPN_RELAY_SIGNER_URL"):
        values["OVPN_RELAY_SIGNER_URL"] = "https://" + values["OPENVPN_HOST"] + "/internal/relay-sign"
    if not values.get("APPLE_RELAY_URL"):
        values["APPLE_RELAY_URL"] = "https://" + values["APPLE_RELAY_HOST"] + ":" + values.get("APPLE_RELAY_PORT", "8443")
    if not values.get("SIGNER_SOURCE_CIDRS"):
        host = urlsplit(values["BACKEND_OPENVPN"]).hostname
        try:
            address = ipaddress.ip_address(host)
            values["SIGNER_SOURCE_CIDRS"] = str(address) + ("/32" if address.version == 4 else "/128")
        except ValueError:
            values["SIGNER_SOURCE_CIDRS"] = "127.0.0.1/32"
    for key in ("TRUSTED_CIDRS", "SIGNER_SOURCE_CIDRS"):
        networks = [str(ipaddress.ip_network(value.strip(), strict=False)) for value in values[key].split(",")]
        values[key] = ",".join(networks)
    certs = json.loads(values.get("EXTRA_TLS_CERTIFICATES", "[]"))
    if not isinstance(certs, list) or any(not isinstance(v, dict) or not {"certFile", "keyFile"} <= v.keys() for v in certs):
        raise ValueError("EXTRA_TLS_CERTIFICATES must be a JSON list of certFile/keyFile objects")
    for key, value in values.items():
        if (key.startswith("BACKEND_") and key not in ("BACKEND_HOST_ALIAS", "BACKEND_HOST_ADDRESS")) or key == "METRICS_REMOTE_WRITE_URL":
            if value and (urlsplit(value).scheme not in ("http", "https") or not urlsplit(value).hostname):
                raise ValueError(f"{key} must be an HTTP(S) URL")
    if values.get("METRICS_BEARER_TOKEN") and (values.get("METRICS_USERNAME") or values.get("METRICS_PASSWORD")):
        raise ValueError("Use either METRICS_BEARER_TOKEN or METRICS_USERNAME/PASSWORD")
    # Historical instructions doubled bcrypt dollar signs for Compose. Some
    # deployments also quoted that escaped value. Canonicalize recognized hashes.
    auth_entries = []
    for entry in values.get("DASHBOARD_AUTH", "").split(","):
        username, separator, digest = entry.partition(":")
        canonical = digest.replace("$$", "$")
        if separator and re.fullmatch(r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}", canonical):
            entry = username + ":" + canonical
        auth_entries.append(entry)
    values["DASHBOARD_AUTH"] = ",".join(auth_entries)
    if values.get("DASHBOARD_AUTH") and not (current.get("DASHBOARD_USERNAME") or imported.get("DASHBOARD_USERNAME")):
        values["DASHBOARD_USERNAME"] = values["DASHBOARD_AUTH"].split(":", 1)[0]
    if not values.get("DASHBOARD_AUTH"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", values["DASHBOARD_USERNAME"]):
            raise ValueError("DASHBOARD_USERNAME must contain only letters, digits, dot, underscore or dash")
        values["DASHBOARD_PASSWORD"] = values.get("DASHBOARD_PASSWORD") or secrets.token_urlsafe(24)
        values["DASHBOARD_AUTH"] = hasher(values["DASHBOARD_USERNAME"], values["DASHBOARD_PASSWORD"])
    for key in ("GRAFANA_ADMIN_PASSWORD", "RELAY_PROFILE_SIGNING_TOKEN"):
        if not values.get(key): values[key] = secrets.token_urlsafe(32)
    if len(values["RELAY_PROFILE_SIGNING_TOKEN"]) < 32:
        raise ValueError("RELAY_PROFILE_SIGNING_TOKEN must have at least 32 characters")
    profiles = [s for s in values.get("COMPOSE_PROFILES", "profile-signing").split(",") if s and s != "metrics"]
    if values.get("METRICS_REMOTE_WRITE_URL"): profiles.append("metrics")
    values["COMPOSE_PROFILES"] = ",".join(profiles)
    return values


def materialize(root, values, previous=None):
    root = Path(root)
    env = root / ".env"
    if previous is None: previous = env.read_text() if env.exists() else ""
    write_env(env, values, previous)
    for key in ("ACME_STORAGE_DIR", "CERTIFICATES_DIR"):
        (root / values[key]).mkdir(parents=True, exist_ok=True)
    acme = root / values["ACME_STORAGE_DIR"] / "acme.json"
    if acme.exists():
        if acme.stat().st_mode & 0o777 != 0o600: acme.chmod(0o600)
    else:
        fd = os.open(acme, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    runtime = root / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    runtime.chmod(0o700)
    (root / "state/metrics-agent").mkdir(parents=True, exist_ok=True)
    scrape = {"global": {"scrape_interval": "15s", "external_labels": {"instance": values["METRICS_INSTANCE"]}},
              "scrape_configs": [{"job_name": "traefik", "static_configs": [{"targets": ["traefik:8082"]}]}]}
    if values.get("METRICS_REMOTE_WRITE_URL"):
        remote = {"url": values["METRICS_REMOTE_WRITE_URL"]}
        if values.get("METRICS_BEARER_TOKEN"):
            remote["authorization"] = {"credentials_file": "/etc/prometheus/metrics-secret"}
        elif values.get("METRICS_USERNAME") or values.get("METRICS_PASSWORD"):
            remote["basic_auth"] = {"username": values["METRICS_USERNAME"], "password_file": "/etc/prometheus/metrics-secret"}
        scrape["remote_write"] = [remote]
    for name, content in {
        "prometheus.yml": yaml.safe_dump(scrape, sort_keys=False),
        "metrics-secret": values.get("METRICS_BEARER_TOKEN") or values.get("METRICS_PASSWORD", ""),
    }.items():
        target = runtime / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream: stream.write(content)
    missing = []
    if values.get("ACME_DNS_PROVIDER") == "selectelv2":
        missing = [key for key in ("SELECTELV2_ACCOUNT_ID", "SELECTELV2_PROJECT_ID", "SELECTELV2_USERNAME", "SELECTELV2_PASSWORD")
                   if not (values.get(key) or values.get(key + "_FILE"))]
    return missing
