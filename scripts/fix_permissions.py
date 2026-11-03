#!/usr/bin/env python3
"""Repair explicitly selected configuration paths; never traverse certificate/data stores."""
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys


PROTECTED = {".git", "certs", "letsencrypt", "state"}


def repair(root, names, uid, gid):
    root = Path(root).resolve()
    candidates = {root}
    for name in names:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("Permission repair accepts only relative configuration paths")
        if any(part in PROTECTED for part in relative.parts) or (relative.parts[0] == "migration-backups" and len(relative.parts) > 1):
            raise ValueError("Permission repair excludes certificate stores, data and Git metadata")
        path = root / relative
        candidates.update([path, *[p for p in path.parents if p != root and root in p.parents]])
    folder = root / "dynamic"
    if folder.is_symlink():
        raise ValueError("Move linked configuration directories before permission repair")
    if folder.is_dir():
        for parent, directories, files in os.walk(folder, followlinks=False):
            directories[:] = [name for name in directories if name not in PROTECTED]
            for name in files:
                if Path(name).suffix not in (".yml", ".yaml", ".toml"): continue
                path = Path(parent) / name
                candidates.add(path)
                candidates.update(p for p in path.parents if p != root and root in p.parents)
    candidates.update(root / ".runtime" / name for name in ("prometheus.yml", "metrics-secret"))
    # Validate the entire selection before modifying ownership or permissions.
    existing = []
    for path in sorted(candidates, key=lambda p: (len(p.parts), str(p))):
        if any(parent.is_symlink() for parent in (path, *[p for p in path.parents if root in p.parents])):
            raise ValueError("Permission repair does not follow configuration symlinks")
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise ValueError("Permission repair requires regular configuration files/directories")
        existing.append((path, metadata))
    changed = 0
    for path, metadata in existing:
        required = 0o700 if stat.S_ISDIR(metadata.st_mode) else 0o600
        mode = stat.S_IMODE(metadata.st_mode) | required
        if metadata.st_uid != uid or metadata.st_gid != gid or mode != stat.S_IMODE(metadata.st_mode):
            os.chown(path, uid, gid, follow_symlinks=False)
            os.chmod(path, mode)
            changed += 1
    return changed


def main():
    if os.geteuid() != 0:
        raise SystemExit("Permission repair must run through sudo")
    payload = json.load(sys.stdin)
    count = repair(payload["root"], payload["paths"], int(payload["uid"]), int(payload["gid"]))
    print(f"Repaired {count} configuration paths; certificate stores and service data were excluded.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Permission repair stopped: {exc}") from None
