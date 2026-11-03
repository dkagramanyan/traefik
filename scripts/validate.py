#!/usr/bin/env python3
"""Validate deployment configuration using only temporary, fake credentials."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


class UniqueLoader(yaml.SafeLoader):
    pass


def mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def load(path):
    return yaml.load(Path(path).read_text(), Loader=UniqueLoader)


def validate(root=ROOT, compose_check=True):
    root = Path(root)
    compose = load(root / "docker-compose.yml")
    static = load(root / "traefik.yml")
    documents = [load(p) for p in sorted((root / "dynamic").glob("*.yml"))]
    assert {"traefik", "prometheus", "grafana", "profile-signer"} <= compose["services"].keys()
    assert {"80:80", "443:443", "8443:8443/tcp"} <= set(compose["services"]["traefik"]["ports"])
    assert static["entryPoints"]["apple-relay"]["address"] == ":8443"
    assert "redirections" not in static["entryPoints"]["web"].get("http", {})
    routers = {}
    for document in documents:
        for kind in ("http", "tcp", "udp"):
            for name, router in document.get(kind, {}).get("routers", {}).items():
                assert (kind, name) not in routers, f"Duplicate router: {name}"
                routers[kind, name] = router
                assert set(router["entryPoints"]) <= static["entryPoints"].keys()
    for name in ("podkop", "kuma", "zoraxy", "transmission", "omada", "openvpn", "openvpn-legacy", "openvpn-orig", "luci-main", "luci-reserv", "plex", "proxmox", "grafana", "tecno"):
        assert "trusted-only" in routers["http", name].get("middlewares", []), f"Missing access rule: {name}"
    assert routers["tcp", "fallback-zoraxy"]["tls"]["passthrough"] is True
    assert "passthrough" not in routers["tcp", "apple-relay"]["tls"]
    portal = routers["http", "user-portal"]
    assert "/api/portal" in portal["rule"] and "/api/clients" not in portal["rule"]
    assert portal["priority"] > len(routers["http", "openvpn"]["rule"])
    signer = compose["services"]["profile-signer"]
    assert signer["profiles"] == ["profile-signing"] and not signer.get("ports")
    assert signer["read_only"] and signer["volumes"] == ["./letsencrypt:/letsencrypt:ro"]
    assert "https://github.com/dkagramanyan/openvpn-server.git#" in signer["build"]["context"]
    for name, service in compose["services"].items():
        environment = service.get("environment", {})
        values = environment.items() if isinstance(environment, dict) else [v.split("=", 1) for v in environment]
        for key, value in values:
            if re.search("PASSWORD|TOKEN|SECRET|API_KEY", key, re.I):
                assert str(value).startswith("${"), f"Inline credential in service {name}: {key}"
    prometheus = load(root / "prometheus.yml")
    jobs = {j["job_name"]: j for j in prometheus["scrape_configs"]}
    assert {"traefik", "wiki-host", "uptime-kuma", "homepc-node", "homepc-smartctl", "homepc-gpu"} <= jobs.keys()
    assert jobs["uptime-kuma"]["basic_auth"]["password_file"] == "/etc/prometheus/kuma_api_key"
    if compose_check:
        with tempfile.TemporaryDirectory(prefix="traefik-compose-check-") as temp:
            folder = Path(temp)
            shutil.copy2(root / "docker-compose.yml", folder / "docker-compose.yml")
            (folder / ".env").write_text("DOMAIN=dgkagramanyan.ru\nDASHBOARD_AUTH=validation-only\nGRAFANA_ADMIN_PASSWORD=validation-only\nRELAY_PROFILE_SIGNING_TOKEN=validation-only-validation-only-validation-only\n")
            subprocess.run(["docker", "compose", "config", "--quiet"], cwd=folder, check=True)
    return len(routers)


if __name__ == "__main__":
    count = validate()
    print(f"Validated Compose, metrics configuration and {count} routers; no expanded credentials printed.")
