"""Read network.private.json, the gateway file from the OpenVPN UI (Settings → Network).

The file is data: every value is validated before it reaches .env, and nothing in
it is executed or used as a template.
"""
import ipaddress
import json
import re
from urllib.parse import urlsplit

VERSION = 2
HOSTNAME = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
UNSAFE = re.compile(r"[\s`{}\\]")


def host(value):
    if not isinstance(value, str) or not HOSTNAME.fullmatch(value):
        raise ValueError("Hostnames must be DNS names without a scheme or port")
    return value


def url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or UNSAFE.search(value):
        raise ValueError("Backend URLs must be HTTP(S), without credentials or template characters")
    _ = parsed.port
    return value


def address(value, minimum_port=1):
    """host:port, as used by Traefik TCP services and entrypoints."""
    name, _, port = str(value).rpartition(":")
    if not name or not port.isdigit() or not minimum_port <= int(port) <= 65535 or re.search(r"[\s`{}\\/@?#]", name):
        raise ValueError(f"Invalid address {value!r}")
    return name, int(port)


def networks(values):
    if not isinstance(values, list):
        raise ValueError("Expected a list of source networks")
    result = [ipaddress.ip_network(value, strict=True) for value in values]
    if any(net.prefixlen == 0 for net in result):
        raise ValueError("Source networks must not include the entire internet")
    return ",".join(str(net) for net in result)


def services(items, reserved):
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("Expected a list of at most 100 services")
    names, result = set(), []
    for item in items:
        name = item["name"]
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name) or name in names:
            raise ValueError("Service names must be unique lowercase identifiers")
        names.add(name)
        hostname = host(item["host"])
        if hostname in reserved:
            raise ValueError("A custom service cannot use the OpenVPN website or dashboard hostname")
        paths = item.get("paths", [])
        if not isinstance(paths, list) or any(not isinstance(p, str) or not p.startswith("/") or UNSAFE.search(p) for p in paths):
            raise ValueError("Invalid service path")
        if not isinstance(item.get("public", False), bool) or not isinstance(item.get("insecure_tls", False), bool):
            raise ValueError("Service flags must be booleans")
        result.append({"name": name, "host": hostname, "url": url(item["url"]), "paths": paths,
                       "public": item.get("public", False), "insecure_tls": item.get("insecure_tls", False)})
    return result


def server_source(backend):
    """The OpenVPN server's address as the gateway sees it, from its backend URL."""
    try:
        ip = ipaddress.ip_address(urlsplit(backend).hostname)
    except ValueError:
        return None
    return f"{ip}/{32 if ip.version == 4 else 128}"


def from_file(path):
    """The .env values the gateway file sets. Fields left out keep their .env values."""
    try:
        data = json.loads(path.read_text())
    except ValueError:
        raise ValueError("The network file is not valid JSON; download it again") from None
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ValueError("Unsupported network file: download it again from the current OpenVPN UI (Settings → Network)")
    try:
        values = {"DOMAIN": host(data["domain"]), "OPENVPN_HOST": host(data["openvpn_host"]),
                  "BACKEND_OPENVPN": url(data["openvpn_backend"])}
        if data.get("relay_host"):
            values["APPLE_RELAY_HOST"] = host(data["relay_host"])
        if data.get("relay_backend"):
            address(data["relay_backend"])
            values["APPLE_RELAY_BACKEND"] = data["relay_backend"]
        if data.get("relay_public_url"):
            public = urlsplit(url(data["relay_public_url"]))
            if public.scheme != "https" or public.hostname != values.get("APPLE_RELAY_HOST", values["DOMAIN"]) \
                    or public.path or public.query or public.fragment:
                raise ValueError("The public relay URL must be an HTTPS origin on the relay hostname")
            values["APPLE_RELAY_URL"] = data["relay_public_url"]
        trusted = networks(data.get("trusted_cidrs", []))
        if trusted:
            values["TRUSTED_CIDRS"] = trusted
        # Relay and VPN guests, and signing requests, all come from the OpenVPN server.
        source = server_source(values["BACKEND_OPENVPN"])
        if source:
            values["SIGNER_SOURCE_CIDRS"] = values["PORTAL_CIDRS"] = source
        values["PORTAL_LISTEN_ADDRESS"], values["PORTAL_HTTPS_PORT"] = "127.0.0.1", "9443"
        if data.get("portal_listener"):
            listen, port = address(data["portal_listener"], minimum_port=1024)
            ip = ipaddress.IPv4Address(listen)
            if ip.is_loopback or ip.is_unspecified or ip.is_link_local or ip.is_multicast:
                raise ValueError("The guest portal listener must be a LAN address of this gateway")
            values["PORTAL_LISTEN_ADDRESS"], values["PORTAL_HTTPS_PORT"] = str(ip), str(port)
        if data.get("services") is not None:   # None keeps the existing services
            reserved = {values["OPENVPN_HOST"], "dashboard." + values["DOMAIN"]}
            values["SERVICE_ROUTES_JSON"] = json.dumps(services(data["services"], reserved))
            values["IMPORTED_DYNAMIC_JSON"] = "{}"
        return values
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid network file; download it again from the OpenVPN UI (Settings → Network)") from exc
