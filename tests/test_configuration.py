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
                 "middlewares": {"trusted-only": {"ipAllowList": {"sourceRange": ["192.0.2.0/24", "10.8.0.0/25"]}}}},
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
    assert values["TRUSTED_CIDRS"] == "192.0.2.0/24,10.8.0.0/25"
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


def test_preflight_stops_before_backup_or_copy_on_restricted_configuration(tmp_path):
    source = source_repo(tmp_path / "source")
    target = deployment(tmp_path / "target")
    restricted = target / ".env.example"
    restricted.write_text("old-example")
    restricted.chmod(0)
    before = (target / "docker-compose.yml").read_bytes()
    environment = (target / ".env").read_bytes()
    try:
        with pytest.raises(ValueError, match="--fix-permissions"):
            adopt.adopt(source, target)
        assert (target / "docker-compose.yml").read_bytes() == before
        assert (target / ".env").read_bytes() == environment
        assert not (target / "migration-backups").exists()
        assert not (target / ".git").exists()
    finally:
        restricted.chmod(0o600)


@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_import_legacy_doubled_bcrypt_preserves_the_original_login(tmp_path, quote):
    import bcrypt
    root = fresh(tmp_path / "target")
    digest = bcrypt.hashpw(b"test-existing-password", bcrypt.gensalt(rounds=5)).decode()
    escaped = ("admin:" + digest).replace("$", "$$")
    (root / ".env").write_text("DOMAIN=example.test\nDASHBOARD_AUTH=" + quote + escaped + quote + "\nPROVIDER_PASSWORD='fake-#-punctuation['\n")
    values = settings.prepare(root)
    assert values["DASHBOARD_AUTH"] == "admin:" + digest
    assert values["PROVIDER_PASSWORD"] == "fake-#-punctuation["
    settings.materialize(root, values)
    imported = settings.read_env(root / ".env")["DASHBOARD_AUTH"]
    assert bcrypt.checkpw(b"test-existing-password", imported.split(":", 1)[1].encode())
    assert "DASHBOARD_AUTH='admin:$2" in (root / ".env").read_text()


def test_dotenv_escaped_dollars_do_not_expand_the_following_variable(tmp_path):
    path = tmp_path / ".env"
    path.write_text("BASE=expanded\nLITERAL=$${BASE}\nNORMAL=${BASE}\n")
    values = settings.read_env(path)
    assert values["LITERAL"] == "${BASE}"
    assert values["NORMAL"] == "expanded"


def test_permission_repair_excludes_keys_stores_and_backup_contents(tmp_path):
    import fix_permissions
    import os
    root = tmp_path / "deployment"
    root.mkdir()
    (root / "dynamic").mkdir()
    config = root / "dynamic/extra.yml"
    config.write_text("http: {}\n")
    config.chmod(0o400)
    protected = []
    for name in ("letsencrypt/acme.json", "certs/server.key", "state/wal", "migration-backups/old/.env", "dynamic/server.key"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private-local-state")
        path.chmod(0o400)
        protected.append((path, path.stat()))
    fix_permissions.repair(root, ["dynamic", "migration-backups"], os.getuid(), os.getgid())
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    for path, before in protected:
        assert (path.stat().st_uid, path.stat().st_gid, path.stat().st_mode) == (before.st_uid, before.st_gid, before.st_mode)
        assert path.read_text() == "private-local-state"


def test_permission_repair_rejects_key_store_paths_and_symlinks_before_changes(tmp_path):
    import fix_permissions
    import os
    root = tmp_path / "deployment"
    root.mkdir()
    path = root / ".env.example"
    path.write_text("placeholder")
    path.chmod(0o400)
    with pytest.raises(ValueError, match="excludes"):
        fix_permissions.repair(root, [".env.example", "letsencrypt/acme.json"], os.getuid(), os.getgid())
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    (root / "dynamic").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="linked"):
        fix_permissions.repair(root, [".env.example", "dynamic"], os.getuid(), os.getgid())
    assert stat.S_IMODE(path.stat().st_mode) == 0o400


def test_adoption_repairs_root_owned_configuration_using_scoped_helper(tmp_path, monkeypatch):
    import bcrypt
    import os
    source = source_repo(tmp_path / "source")
    target = deployment(tmp_path / "target")
    (target / ".env.example").write_text("restricted-old-example")
    digest = bcrypt.hashpw(b"test-existing-password", bcrypt.gensalt(rounds=5)).decode()
    (target / ".env").write_text("DOMAIN=example.test\nDASHBOARD_AUTH=" + ("admin:" + digest).replace("$", "$$") + "\nPROVIDER_PASSWORD='fake-#-punctuation['\n")
    image = os.environ.get("PYTHON_TEST_IMAGE", "python:3.14-alpine3.24")
    subprocess.run(["docker", "run", "--rm", "-v", f"{tmp_path}:{tmp_path}", image,
                    "python", "-c", '''import os,pathlib,sys
root=pathlib.Path(sys.argv[1])
for name in (".env.example","docker-compose.yml","dynamic/native-services.yml","letsencrypt/acme.json","certs/extra.key"):
 p=root/name; os.chown(p,0,0); p.chmod(0o600)
''', str(target)], check=True, capture_output=True)
    stores = [target / name for name in ("letsencrypt/acme.json", "certs/extra.key")]
    before = [(p.stat().st_uid, p.stat().st_gid, p.stat().st_mode, p.stat().st_size) for p in stores]
    environment = (target / ".env").read_bytes()
    try:
        with pytest.raises(ValueError, match="--fix-permissions"):
            adopt.adopt(source, target)
        assert not (target / "migration-backups").exists()
        assert environment == (target / ".env").read_bytes()
        original_run = subprocess.run
        invoked = []
        def docker_sudo(args, *rest, **kwargs):
            if args[0] == "sudo":
                invoked.append(json.loads(kwargs["input"]))
                args = ["docker", "run", "--rm", "-i", "-v", f"{ROOT}:/code:ro", "-v", f"{tmp_path}:{tmp_path}",
                        image, "python", "/code/scripts/fix_permissions.py"]
            return original_run(args, *rest, **kwargs)
        monkeypatch.setattr(subprocess, "run", docker_sudo)
        adopt.adopt(source, target, fix_permissions=True)
        assert len(invoked) == 1
        assert invoked[0]["uid"] == os.getuid()
        assert "letsencrypt/acme.json" not in invoked[0]["paths"]
        assert "certs/extra.key" not in invoked[0]["paths"]
        values = settings.read_env(target / ".env")
        assert bcrypt.checkpw(b"test-existing-password", values["DASHBOARD_AUTH"].split(":", 1)[1].encode())
        assert values["PROVIDER_PASSWORD"] == "fake-#-punctuation["
        assert (target / ".env.example").stat().st_uid == os.getuid()
        assert before == [(p.stat().st_uid, p.stat().st_gid, p.stat().st_mode, p.stat().st_size) for p in stores]
    finally:
        # Restore only synthetic fixture ownership for pytest's temporary cleanup.
        subprocess.run(["docker", "run", "--rm", "-v", f"{tmp_path}:{tmp_path}", image,
                        "python", "-c", '''import os,pathlib,sys
root=pathlib.Path(sys.argv[1]); uid=int(sys.argv[2]); gid=int(sys.argv[3])
for parent,dirs,files in os.walk(root):
 for name in dirs+files: os.chown(pathlib.Path(parent)/name,uid,gid)
''', str(tmp_path), str(os.getuid()), str(os.getgid())], check=True, capture_output=True)
