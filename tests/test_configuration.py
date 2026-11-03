import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import settings
import validate
import adopt


def fresh(path):
    path.mkdir()
    shutil.copy2(ROOT / ".env.example", path / ".env.example")
    return path


def prepared(path, **kwargs):
    return settings.prepare(path, domain="example.test", hasher=lambda u, p: u + ":$2y$fake-hash", **kwargs)


def test_portable_configuration_and_no_prometheus_server():
    assert validate.validate(compose_check=False) == 4


def test_duplicate_yaml_is_rejected(tmp_path):
    path = tmp_path / "duplicate.yml"
    path.write_text("services: {}\nservices: {}\n")
    with pytest.raises(ValueError, match="Duplicate"):
        validate.load(path)


def test_generated_settings_are_idempotent_and_private(tmp_path):
    root = fresh(tmp_path / "deployment")
    first = prepared(root)
    assert first["ACME_EMAIL"] == "admin@example.test"
    assert first["APPLE_RELAY_URL"] == "https://example.test:8443"
    assert first["OPENVPN_HOST"] == "openvpn.example.test"
    assert "metrics" not in first["COMPOSE_PROFILES"]
    settings.materialize(root, first)
    (root / "letsencrypt/acme.json").write_text("existing-account-and-private-identity")
    second = prepared(root)
    assert first == second
    settings.materialize(root, second)
    assert (root / "letsencrypt/acme.json").read_text() == "existing-account-and-private-identity"
    assert stat.S_IMODE((root / ".env").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / ".runtime/prometheus.yml").stat().st_mode) == 0o600


def test_dotenv_round_trip_and_compose_literal_credentials(tmp_path):
    root = fresh(tmp_path / "deployment")
    values = prepared(root)
    original = "value-with-$dollar-${BRACES}-#hash-and-'quote"
    values.update(SELECTELV2_PASSWORD=original, METRICS_PASSWORD=original)
    settings.materialize(root, values)
    assert settings.read_env(root / ".env")["SELECTELV2_PASSWORD"] == original
    shutil.copy2(ROOT / "docker-compose.yml", root / "docker-compose.yml")
    output = subprocess.run(["docker", "compose", "config", "--format", "json"], cwd=root,
                            text=True, capture_output=True, check=True).stdout
    resolved = json.loads(output)
    assert resolved["services"]["traefik"]["environment"]["SELECTELV2_PASSWORD"].replace("$$", "$") == original
    assert resolved["services"]["traefik"]["labels"]["traefik.http.middlewares.dashboard-auth.basicauth.users"].replace("$$", "$") == values["DASHBOARD_AUTH"]


def test_metrics_agent_config_contains_only_traefik_and_file_credentials(tmp_path):
    root = fresh(tmp_path / "deployment")
    settings.write_env(root / ".env", {"METRICS_REMOTE_WRITE_URL": "https://metrics.example.test/api/v1/write",
                                      "METRICS_USERNAME": "metrics-user", "METRICS_PASSWORD": "private-test-value"})
    values = prepared(root)
    assert "metrics" in values["COMPOSE_PROFILES"]
    settings.materialize(root, values)
    config = yaml.safe_load((root / ".runtime/prometheus.yml").read_text())
    assert [s["job_name"] for s in config["scrape_configs"]] == ["traefik"]
    assert config["remote_write"][0]["basic_auth"]["password_file"] == "/etc/prometheus/metrics-secret"
    assert "private-test-value" not in (root / ".runtime/prometheus.yml").read_text()
    assert (root / ".runtime/metrics-secret").read_text() == "private-test-value"


def deployment(path):
    path.mkdir()
    settings.write_env(path / ".env", {"DOMAIN": "example.test", "DASHBOARD_AUTH": "owner:$2y$existing-hash",
                                      "SELECTELV2_PASSWORD": "preserve-this-provider-secret"})
    (path / "docker-compose.yml").write_text("services:\n  grafana:\n    environment:\n      - GF_SECURITY_ADMIN_PASSWORD=old-local-test-password\n  traefik:\n    extra_hosts: ['gateway:192.0.2.1']\n")
    (path / "traefik.yml").write_text("certificatesResolvers:\n  letsencrypt:\n    acme:\n      email: existing@example.test\n      dnsChallenge:\n        provider: selectelv2\n        resolvers: ['a.ns.example.test:53']\n")
    (path / "dynamic").mkdir()
    (path / "dynamic/native-services.yml").write_text(yaml.safe_dump({
        "http": {"services": {"openvpn": {"loadBalancer": {"servers": [{"url": "http://192.0.2.30:8080"}]}}},
                 "middlewares": {"trusted-only": {"ipAllowList": {"sourceRange": ["192.0.2.0/24", "10.0.70.0/25"]}}}},
        "tcp": {"routers": {"apple-relay": {"rule": "HostSNI(`example.test`)"}},
                "services": {"apple-relay": {"loadBalancer": {"servers": [{"address": "192.0.2.30:8444"}]}}}},
    }))
    (path / "dynamic/certs.yml").write_text("tls:\n  certificates:\n    - certFile: /certs/extra.crt\n      keyFile: /certs/extra.key\n")
    (path / "letsencrypt").mkdir()
    (path / "letsencrypt/acme.json").write_text("local-acme-store")
    (path / "letsencrypt/acme.json").chmod(0o600)
    (path / "certs").mkdir()
    (path / "certs/extra.key").write_text("local-private-key")
    return path


def test_import_preserves_routes_credentials_certificates_and_networks(tmp_path):
    source = deployment(tmp_path / "old")
    target = fresh(tmp_path / "new")
    values = settings.prepare(target, import_from=source)
    assert values["DASHBOARD_AUTH"] == "owner:$2y$existing-hash"
    assert values["DASHBOARD_USERNAME"] == "owner"
    assert values["GRAFANA_ADMIN_PASSWORD"] == "old-local-test-password"
    assert values["SELECTELV2_PASSWORD"] == "preserve-this-provider-secret"
    assert values["ACME_DNS_RESOLVERS"] == "a.ns.example.test:53"
    assert values["BACKEND_OPENVPN"] == "http://192.0.2.30:8080"
    assert values["TRUSTED_CIDRS"] == "192.0.2.0/24,10.0.70.0/25"
    assert values["SIGNER_SOURCE_CIDRS"] == "192.0.2.30/32"
    assert values["BACKEND_HOST_ALIAS"] == "gateway"
    assert json.loads(values["EXTRA_TLS_CERTIFICATES"])[0]["keyFile"] == "/certs/extra.key"


def source_repo(path):
    path.mkdir()
    for name in ("docker-compose.yml", ".gitignore", ".env.example"):
        shutil.copy2(ROOT / name, path / name)
    for args in (["init", "-b", "main"], ["config", "user.email", "test@example.invalid"],
                 ["config", "user.name", "Configuration Test"], ["add", "."], ["commit", "-m", "Fixture"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


def test_adoption_preserves_local_state_backups_and_project_name(tmp_path):
    source = source_repo(tmp_path / "source")
    target = deployment(tmp_path / "traefik")
    original = (target / "docker-compose.yml").read_text()
    (target / "dynamic/custom.yml").write_text("http: {routers: {}}\n")
    backup = adopt.adopt(source, target)
    environment = settings.read_env(target / ".env")
    assert environment["GRAFANA_ADMIN_PASSWORD"] == "old-local-test-password"
    assert environment["COMPOSE_PROJECT_NAME"] == "traefik"
    assert (backup / "docker-compose.yml").read_text() == original
    assert (target / "dynamic/custom.yml").is_file()
    assert (backup / "active-dynamic/custom.yml").is_file()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    assert (target / "letsencrypt/acme.json").read_text() == "local-acme-store"
    assert (target / "certs/extra.key").read_text() == "local-private-key"
    tracked = subprocess.run(["git", "-C", str(target), "ls-files"], capture_output=True, text=True, check=True).stdout
    assert ".env\n" not in tracked and "extra.key" not in tracked and "acme.json" not in tracked


def test_invalid_domain_and_existing_git_stop_before_migration(tmp_path):
    root = fresh(tmp_path / "empty")
    with pytest.raises(ValueError, match="DOMAIN"):
        settings.prepare(root)
    source = source_repo(tmp_path / "source")
    target = deployment(tmp_path / "target")
    (target / ".git").mkdir()
    with pytest.raises(ValueError, match="without Git metadata"):
        adopt.adopt(source, target)
    assert not (target / "migration-backups").exists()


def test_mixed_metrics_auth_is_rejected(tmp_path):
    root = fresh(tmp_path / "deployment")
    settings.write_env(root / ".env", {"METRICS_USERNAME": "user", "METRICS_BEARER_TOKEN": "token"})
    with pytest.raises(ValueError, match="either"):
        prepared(root)


def test_upgrade_imports_previous_git_config_after_templates_replace_it(tmp_path, monkeypatch, capsys):
    root = deployment(tmp_path / "deployment")
    for args in (["init", "-b", "main"], ["config", "user.email", "test@example.invalid"],
                 ["config", "user.name", "Configuration Test"],
                 ["add", "docker-compose.yml", "traefik.yml", "dynamic"], ["commit", "-m", "Old deployment"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    revision = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    shutil.copy2(ROOT / ".env.example", root / ".env.example")
    shutil.copy2(ROOT / "docker-compose.yml", root / "docker-compose.yml")
    (root / "traefik.yml").unlink()
    for source in (ROOT / "dynamic").glob("*.yml"):
        shutil.copy2(source, root / "dynamic" / source.name)
    spec = importlib.util.spec_from_file_location("setup_command", ROOT / "scripts/setup.py")
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    monkeypatch.setattr(command, "ROOT", root)
    monkeypatch.setattr(sys, "argv", ["setup.py", "--import-revision", revision])
    command.main()
    values = settings.read_env(root / ".env")
    assert values["ACME_EMAIL"] == "existing@example.test"
    assert values["ACME_DNS_RESOLVERS"] == "a.ns.example.test:53"
    assert values["BACKEND_OPENVPN"] == "http://192.0.2.30:8080"
    assert values["GRAFANA_ADMIN_PASSWORD"] == "old-local-test-password"
    output = capsys.readouterr().out
    assert "preserve-this-provider-secret" not in output and "old-local-test-password" not in output
