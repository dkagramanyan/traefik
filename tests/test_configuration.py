import importlib.util
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_routes_and_credentials():
    assert module("validate").validate(compose_check=False) >= 20


def test_duplicate_yaml_is_rejected(tmp_path):
    path = tmp_path / "duplicate.yml"
    path.write_text("services: {}\nservices: {}\n")
    with pytest.raises(ValueError, match="Duplicate"):
        module("validate").load(path)


def source_repo(path):
    path.mkdir()
    for name in ("docker-compose.yml", ".gitignore", "traefik.yml"):
        shutil.copy2(ROOT / name, path / name)
    for args in (["init", "-b", "main"], ["config", "user.email", "test@example.invalid"],
                 ["config", "user.name", "Configuration Test"], ["add", "."], ["commit", "-m", "Test configuration"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


def deployment(path):
    path.mkdir()
    (path / "docker-compose.yml").write_text("services:\n  grafana:\n    environment:\n      - GF_SECURITY_ADMIN_PASSWORD=old-local-test-password\n")
    (path / ".env").write_text("DOMAIN=dgkagramanyan.ru\nSELECTEL_EXISTING_VARIABLE=keep-this-local-value\n")
    (path / "letsencrypt").mkdir()
    (path / "letsencrypt/acme.json").write_text("local-acme-store")
    (path / "certs").mkdir()
    (path / "certs/server.key").write_text("local-private-key")
    (path / "kuma_api_key").write_text("local-metrics-key")
    return path


def test_adoption_preserves_local_state_and_moves_inline_password(tmp_path):
    source = source_repo(tmp_path / "source")
    target = deployment(tmp_path / "traefik")
    original = (target / "docker-compose.yml").read_text()
    backup = module("adopt").adopt(source, target)
    environment = (target / ".env").read_text()
    assert "GRAFANA_ADMIN_PASSWORD=old-local-test-password" in environment
    assert "SELECTEL_EXISTING_VARIABLE=keep-this-local-value" in environment
    assert "COMPOSE_PROJECT_NAME=traefik" in environment
    assert "old-local-test-password" not in (target / "docker-compose.yml").read_text()
    assert (backup / "docker-compose.yml").read_text() == original
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    assert stat.S_IMODE((target / ".env").stat().st_mode) == 0o600
    assert (target / "letsencrypt/acme.json").read_text() == "local-acme-store"
    assert (target / "certs/server.key").read_text() == "local-private-key"
    assert (target / "kuma_api_key").read_text() == "local-metrics-key"
    files = subprocess.run(["git", "-C", str(target), "ls-files"], capture_output=True, text=True, check=True).stdout
    assert ".env\n" not in files and "server.key" not in files and "acme.json" not in files


def test_adoption_keeps_existing_environment_password(tmp_path):
    source = source_repo(tmp_path / "source")
    target = deployment(tmp_path / "traefik")
    with (target / ".env").open("a") as stream:
        stream.write("GRAFANA_ADMIN_PASSWORD=already-managed-in-env\nCOMPOSE_PROJECT_NAME=existing-project\n")
    module("adopt").adopt(source, target)
    environment = (target / ".env").read_text()
    assert "already-managed-in-env" in environment and "old-local-test-password" not in environment
    assert "COMPOSE_PROJECT_NAME=existing-project" in environment


def test_ambiguous_password_stops_before_mutation(tmp_path):
    source = source_repo(tmp_path / "source")
    target = deployment(tmp_path / "traefik")
    original = "services:\n  grafana:\n    environment:\n      GF_SECURITY_ADMIN_PASSWORD: '${CUSTOM_PASSWORD}'\n"
    (target / "docker-compose.yml").write_text(original)
    with pytest.raises(ValueError, match="Set GRAFANA_ADMIN_PASSWORD"):
        module("adopt").adopt(source, target)
    assert (target / "docker-compose.yml").read_text() == original
    assert not (target / ".git").exists() and not (target / "migration-backups").exists()
