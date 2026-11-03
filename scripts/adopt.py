#!/usr/bin/env python3
"""Adopt an existing server without moving its keys, ACME store or Docker data."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import re
import subprocess
import tempfile

from settings import ROOT, materialize, prepare, read_env


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def adopt(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not (source / ".git").is_dir():
        raise ValueError("Run this helper from a normal clone of the Traefik repository")
    if source == destination or not destination.is_dir() or (destination / ".git").exists():
        raise ValueError("Destination must be an existing deployment without Git metadata; an existing checkout should use git pull, then ./manage setup")
    files = [p for p in git(source, "ls-files", "-z").decode().split("\0") if p and (source / p).is_file()]
    if "docker-compose.yml" not in files:
        raise ValueError("Source does not contain the deployment files")
    # Resolve/import all settings before changing the destination.
    values = prepare(source, import_from=destination, use_local_env=False)
    previous = (destination / ".env").read_text() if (destination / ".env").exists() else ""
    if not read_env(destination / ".env").get("COMPOSE_PROJECT_NAME"):
        project = re.sub(r"[^a-z0-9_-]", "", destination.name.lower()).lstrip("-_")
        if not project: raise ValueError("Set COMPOSE_PROJECT_NAME in the existing .env")
        values["COMPOSE_PROJECT_NAME"] = project
    backups = destination / "migration-backups"
    backups.mkdir(mode=0o700, exist_ok=True)
    backups.chmod(0o700)
    backup = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-"), dir=backups))
    for name in dict.fromkeys([*files, ".env", "traefik.yml", "prometheus.yml"]):
        old = destination / name
        if old.is_file():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, saved)
            saved.chmod(0o600)
    # Stage the watched directory separately. Existing bind-mounted containers
    # keep their old directory until Compose recreates them with the new env.
    staged_dynamic = backup / "prepared-dynamic"
    current_dynamic = destination / "dynamic"
    if current_dynamic.is_dir():
        shutil.copytree(current_dynamic, staged_dynamic)
    else:
        staged_dynamic.mkdir()
    for name in files:
        if name.startswith("dynamic/"):
            target = staged_dynamic / Path(name).relative_to("dynamic")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target)
    for name in files:
        if name.startswith("dynamic/"): continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    if current_dynamic.exists():
        current_dynamic.rename(backup / "active-dynamic")
    staged_dynamic.rename(current_dynamic)
    materialize(destination, values, previous)
    shutil.copytree(source / ".git", destination / ".git")
    (destination / ".git").chmod(0o700)
    git(destination, "reset", "--mixed", "HEAD")
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Existing deployment, normally ~/traefik")
    args = parser.parse_args()
    try:
        backup = adopt(ROOT, args.directory)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Migration stopped: {exc}\n")
    print(f"Prepared {args.directory}; private backup: {backup}")
    print("Kept local certificates, ACME state and Docker volumes. Containers were not restarted.")
    print("Next: cd into that directory and run ./manage up --remove-orphans.")


if __name__ == "__main__":
    main()
