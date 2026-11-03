#!/usr/bin/env python3
"""Validate the portable deployment with fake settings; never expand real secrets."""
from pathlib import Path
import shutil
import subprocess
import tempfile

import yaml
from settings import ROOT, materialize, prepare, write_env


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


def fixture(root, metrics=True):
    root = Path(root)
    shutil.copy2(ROOT / ".env.example", root / ".env.example")
    write_env(root / ".env", {"DOMAIN": "example.test", "DASHBOARD_AUTH": "test:$2y$fake-hash",
                             "METRICS_REMOTE_WRITE_URL": "http://receiver:9090/api/v1/write" if metrics else ""})
    settings = prepare(root)
    materialize(root, settings)
    return settings


def validate(root=ROOT, compose_check=True):
    root = Path(root)
    compose = load(root / "docker-compose.yml")
    services = compose["services"]
    assert set(services) == {"traefik", "grafana", "metrics-agent", "profile-signer"}
    assert "prometheus" not in services
    agent = services["metrics-agent"]
    assert "--agent" in agent["command"] and not agent.get("ports")
    assert agent["profiles"] == ["metrics"]
    args = services["traefik"]["command"]
    assert not any("configfile" in arg.lower() for arg in args)
    assert any("dnschallenge.provider=${ACME_DNS_PROVIDER" in arg for arg in args)
    assert "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json" in args
    assert not any("redirections" in arg for arg in args)
    signer = services["profile-signer"]
    assert signer["profiles"] == ["profile-signing"] and not signer.get("ports")
    assert signer["volumes"] == ["${ACME_STORAGE_DIR:-./letsencrypt}:/letsencrypt:ro"]
    assert "https://github.com/dkagramanyan/openvpn-server.git#" in signer["build"]["context"]
    for service in services.values():
        env = service.get("environment", {})
        for key, value in env.items():
            if any(part in key for part in ("PASSWORD", "TOKEN", "SECRET")):
                assert str(value).startswith("${"), key
    if compose_check:
        with tempfile.TemporaryDirectory(prefix="traefik-check-") as temp:
            folder = Path(temp)
            fixture(folder)
            shutil.copy2(root / "docker-compose.yml", folder / "docker-compose.yml")
            subprocess.run(["docker", "compose", "--profile", "metrics", "--profile", "profile-signing", "config", "--quiet"], cwd=folder, check=True)
    return len(services)


if __name__ == "__main__":
    print(f"Validated {validate()} portable services, agent mode and credential references.")
