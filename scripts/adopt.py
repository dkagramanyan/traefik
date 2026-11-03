#!/usr/bin/env python3
"""Adopt an existing server without moving its keys, ACME store or Docker data."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import shutil
import re
import subprocess
import tempfile

from settings import ROOT, materialize, prepare, read_env


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def permission_issues(destination, files):
    """Check paths needed for copies, backups and private runtime output."""
    issues = set()
    def check(path, mask, owner=False):
        if path.is_symlink():
            raise ValueError("Migration does not replace linked configuration paths: " + str(path))
        if not os.access(path, mask) or (owner and path.stat().st_uid != os.getuid()):
            issues.add(path)
    paths = [destination, destination / "dynamic", destination / "migration-backups", destination / ".runtime"]
    paths += [destination / name for name in dict.fromkeys([*files, ".env", "traefik.yml", "prometheus.yml"])]
    for path in paths:
        for parent in path.parents:
            if parent == destination or destination in parent.parents:
                if parent.exists(): check(parent, os.R_OK | os.W_OK | os.X_OK)
            else: break
        if path.exists():
            if path.is_dir():
                check(path, os.R_OK | os.W_OK | os.X_OK, path.name in ("migration-backups", ".runtime"))
            else:
                check(path, os.R_OK | os.W_OK)
    for name in ("dynamic", ".runtime"):
        folder = destination / name
        if folder.is_dir():
            def inaccessible(error): issues.add(Path(error.filename))
            for parent, directories, children in os.walk(folder, onerror=inaccessible, followlinks=False):
                check(Path(parent), os.R_OK | os.W_OK | os.X_OK)
                for child in directories + children:
                    path = Path(parent) / child
                    check(path, os.R_OK | os.W_OK | (os.X_OK if path.is_dir() else 0), name == ".runtime")
    return sorted(issues)


def ensure_permissions(destination, files, fix_permissions=False):
    issues = permission_issues(destination, files)
    if issues and fix_permissions:
        # The explicit flag authorizes a narrow sudo operation, not a root migration.
        paths = sorted(set([*files, ".env", "traefik.yml", "prometheus.yml", "dynamic", ".runtime", "migration-backups"]))
        payload = {"root": str(destination), "paths": paths, "uid": os.getuid(), "gid": os.getgid()}
        subprocess.run(["sudo", sys.executable, str(ROOT / "scripts/fix_permissions.py")],
                       input=json.dumps(payload), text=True, check=True)
        issues = permission_issues(destination, files)
    if issues:
        names = ", ".join(str(path.relative_to(destination)) or "." for path in issues[:8])
        raise ValueError("Configuration permissions need repair: " + names +
                         ". Retry manage adopt --fix-permissions with the same destination; "
                         "it uses sudo for configuration files only. No migration files were changed.")


def adopt(source, destination, fix_permissions=False):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not (source / ".git").is_dir():
        raise ValueError("Run this helper from a normal clone of the Traefik repository")
    if source == destination or not destination.is_dir() or (destination / ".git").exists():
        raise ValueError("Destination must be an existing deployment without Git metadata; an existing checkout should use git pull, then ./manage setup")
    if destination == Path(destination.anchor) or not (destination / "docker-compose.yml").is_file():
        raise ValueError("Destination must be a deployment folder containing docker-compose.yml")
    files = [p for p in git(source, "ls-files", "-z").decode().split("\0") if p and (source / p).is_file()]
    if "docker-compose.yml" not in files:
        raise ValueError("Source does not contain the deployment files")
    ensure_permissions(destination, files, fix_permissions)
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
    parser.add_argument("--fix-permissions", action="store_true", help="Use sudo to repair configuration access for the invoking user before migration")
    args = parser.parse_args()
    try:
        backup = adopt(ROOT, args.directory, fix_permissions=args.fix_permissions)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Migration stopped: {exc}\n")
    print(f"Prepared {args.directory}; private backup: {backup}")
    print("Kept local certificates, ACME state and Docker volumes. Containers were not restarted.")
    print("Next: cd into that directory, run sudo docker compose config --quiet, then sudo docker compose up -d --build --remove-orphans.")


if __name__ == "__main__":
    main()
