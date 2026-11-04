"""Import the admin UI's private gateway export without executing its contents."""
import ipaddress
import json
import re
from urllib.parse import urlsplit


def host(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ValueError("Gateway hostnames must be DNS names without a scheme or port")
    return value


def url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or re.search(r"[\s`{}\\]", value):
        raise ValueError("Backend URLs must be HTTP(S), without credentials or template characters")
    _ = parsed.port
    return value


def networks(values):
    if not isinstance(values, list): raise ValueError("Expected a list of source networks")
    result = [str(ipaddress.ip_network(value, strict=True)) for value in values]
    if any(ipaddress.ip_network(value).prefixlen == 0 for value in result):
        raise ValueError("Source networks must not include the entire internet")
    return ",".join(result)


def services(items, reserved):
    if not isinstance(items, list) or len(items) > 100: raise ValueError("Expected a list of at most 100 services")
    names, result = set(), []
    for item in items:
        name = item["name"]
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name) or name in names:
            raise ValueError("Service names must be unique lowercase identifiers")
        names.add(name)
        hostname = host(item["host"])
        if hostname in reserved: raise ValueError("A custom service cannot replace the portal or dashboard")
        paths = item.get("paths", [])
        if not isinstance(paths, list) or any(not isinstance(p, str) or not p.startswith("/") or re.search(r"[\s`{}\\]", p) for p in paths):
            raise ValueError("Invalid service path")
        if not isinstance(item.get("public", False), bool) or not isinstance(item.get("insecure_tls", False), bool):
            raise ValueError("Service flags must be booleans")
        result.append({"name": name, "host": hostname, "url": url(item["url"]), "paths": paths,
                       "public": item.get("public", False), "insecure_tls": item.get("insecure_tls", False)})
    return result


def from_file(path):
    try:
        data = json.loads(path.read_text())
        if data["version"] != 1: raise ValueError("Unsupported network export version")
        gateway, portal = data["gateway"], data["portal"]
        values = {"DOMAIN": host(gateway["domain"]), "OPENVPN_HOST": host(gateway["openvpn_host"]),
                  "APPLE_RELAY_HOST": host(gateway["relay_host"]), "BACKEND_OPENVPN": url(gateway["openvpn_backend"]),
                  "TRUSTED_CIDRS": networks(gateway["trusted_cidrs"]), "PORTAL_CIDRS": networks(gateway["portal_cidrs"])}
        if not values["TRUSTED_CIDRS"]: raise ValueError("Supply private-service source networks")
        target_host, port = gateway["relay_backend"].rsplit(":", 1)
        if not target_host or re.search(r"[\s`{}\\/@?#]", target_host) or not 1 <= int(port) <= 65535:
            raise ValueError("Invalid relay backend address")
        values["APPLE_RELAY_BACKEND"] = gateway["relay_backend"]
        if gateway.get("relay_public_url"):
            public_url = url(gateway["relay_public_url"])
            parsed = urlsplit(public_url)
            if parsed.scheme != "https" or parsed.hostname != values["APPLE_RELAY_HOST"] or parsed.path or parsed.query or parsed.fragment:
                raise ValueError("Public relay URL must be an HTTPS origin matching the relay hostname")
            values["APPLE_RELAY_URL"] = public_url
        imported_services = services(gateway["services"], {values["OPENVPN_HOST"], "dashboard." + values["DOMAIN"]})
        replace_services = gateway.get("replace_services", True)
        if not isinstance(replace_services, bool):
            raise ValueError("Service replacement must be true or false")
        if replace_services:
            values["SERVICE_ROUTES_JSON"] = json.dumps(imported_services)
            values["IMPORTED_DYNAMIC_JSON"] = "{}"
        elif imported_services:
            raise ValueError("Enable service replacement to import an additional service inventory")
        values["PORTAL_LISTEN_ADDRESS"], values["PORTAL_HTTPS_PORT"] = "127.0.0.1", "9443"
        if portal.get("target"):
            if portal["host"] != values["OPENVPN_HOST"]: raise ValueError("Portal and OpenVPN hosts must match")
            address, port = portal["target"].rsplit(":", 1)
            ip = ipaddress.IPv4Address(address)
            if ip.is_loopback or ip.is_unspecified or ip.is_link_local or ip.is_multicast or not 1024 <= int(port) <= 65535:
                raise ValueError("Portal target must be a dedicated LAN listener")
            values["PORTAL_LISTEN_ADDRESS"], values["PORTAL_HTTPS_PORT"] = str(ip), str(int(port))
        return values
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid network export; download it again from Settings → Network") from exc
