#!/usr/bin/env python3
"""Fill missing environment values and prepare local runtime files."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

from settings import ROOT, materialize, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain")
    parser.add_argument("--email")
    imports = parser.add_mutually_exclusive_group()
    imports.add_argument("--import-from", type=Path)
    imports.add_argument("--import-revision", help="Import tracked legacy config from a previous Git revision")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    try:
        if args.import_revision:
            with tempfile.TemporaryDirectory(prefix="traefik-import-") as temp:
                source = Path(temp)
                for name in ("docker-compose.yml", "traefik.yml", "dynamic/native-services.yml", "dynamic/certs.yml"):
                    result = subprocess.run(["git", "-C", str(ROOT), "show", args.import_revision + ":" + name], capture_output=True)
                    if result.returncode == 0:
                        target = source / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(result.stdout)
                if not (source / "docker-compose.yml").exists():
                    raise ValueError("The import revision does not contain docker-compose.yml")
                if (ROOT / ".env").exists(): shutil.copy2(ROOT / ".env", source / ".env")
                values = prepare(root=ROOT, import_from=source, domain=args.domain, email=args.email)
        else:
            values = prepare(root=ROOT, import_from=args.import_from, domain=args.domain, email=args.email)
        missing = materialize(ROOT, values)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Setup stopped: {exc}\n")
    if not args.quiet:
        print("Prepared .env with private permissions. Existing credentials were preserved.")
        print("Dashboard login: dashboard." + values["DOMAIN"] + "; credentials are in .env (not printed).")
        print("Profiles: " + (values["COMPOSE_PROFILES"] or "core services only"))
    if missing:
        print("Fill these DNS-provider settings in .env before requesting certificates: " + ", ".join(missing))
    if not values.get("METRICS_REMOTE_WRITE_URL") and not args.quiet:
        print("Metrics forwarding is optional until METRICS_REMOTE_WRITE_URL is filled in .env.")


if __name__ == "__main__":
    main()
