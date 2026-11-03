#!/usr/bin/env python3
"""Adopt an existing Pi deployment without moving its local keys or Docker data."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def adopt(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not (source / ".git").is_dir():
        raise ValueError("Run this helper from a normal clone of the Traefik repository")
    if source == destination or not destination.is_dir() or (destination / ".git").exists():
        raise ValueError("Destination must be an existing deployment directory without Git metadata; an existing checkout should use git pull")
    files = [p for p in git(source, "ls-files", "-z").decode().split("\0") if p]
    if not files or "docker-compose.yml" not in files:
        raise ValueError("Source repository does not contain the deployment files")
    environment = destination / ".env"
    previous_env = environment.read_text() if environment.exists() else ""
    updated_env = previous_env
    password_line = re.search(r"(?m)^GRAFANA_ADMIN_PASSWORD=(.*)$", previous_env)
    has_password = password_line and password_line.group(1).strip() not in ("", "''", '\"\"')
    if not has_password:
        original = (destination / "docker-compose.yml").read_text()
        match = re.search(r"(?m)^\s*-\s*GF_SECURITY_ADMIN_PASSWORD=([^\r\n]+)\s*$", original)
        value = match.group(1).strip() if match else ""
        # Complex YAML quoting/interpolation needs a deliberate operator edit.
        # The simple literal used by the existing deployment can be moved safely.
        if not value or not re.fullmatch(r"[A-Za-z0-9!@%^&*()_+=:;.,/?-]+", value):
            raise ValueError("Set GRAFANA_ADMIN_PASSWORD in the existing .env before migration; the current password could not be migrated safely")
        line = "GRAFANA_ADMIN_PASSWORD=" + value
        if password_line:
            updated_env = updated_env[:password_line.start()] + line + updated_env[password_line.end():]
        else:
            updated_env += ("\n" if updated_env and not updated_env.endswith("\n") else "") + line + "\n"
    if not re.search(r"(?m)^COMPOSE_PROJECT_NAME=", updated_env):
        project = re.sub(r"[^a-z0-9_-]", "", destination.name.lower()).lstrip("-_")
        if not project:
            raise ValueError("Set COMPOSE_PROJECT_NAME in .env to the existing deployment's project name")
        updated_env += ("\n" if not updated_env.endswith("\n") else "") + "COMPOSE_PROJECT_NAME=" + project + "\n"
    # Backups may contain the former inline password. Keep the directory private
    # and ignored, and never include certificate stores or Docker volume data.
    backups = destination / "migration-backups"
    backups.mkdir(mode=0o700, exist_ok=True)
    backups.chmod(0o700)
    backup = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-"), dir=backups))
    for name in [*files, ".env"]:
        old = destination / name
        if old.is_file():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, saved)
            saved.chmod(0o600)
    for name in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    environment.write_text(updated_env)
    environment.chmod(0o600)
    shutil.copytree(source / ".git", destination / ".git")
    (destination / ".git").chmod(0o700)
    git(destination, "reset", "--mixed", "HEAD")
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Existing deployment, normally ~/traefik")
    args = parser.parse_args()
    try:
        backup = adopt(Path(__file__).resolve().parents[1], args.directory)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Migration stopped: {exc}\n")
    print(f"Prepared {args.directory}; private configuration backup: {backup}")
    print("Local certificates, ACME store, API-key file and Docker volumes were retained. Containers have not been restarted.")
    print("Next: cd into that directory, run docker compose config --quiet, then docker compose up -d --build.")


if __name__ == "__main__":
    main()
