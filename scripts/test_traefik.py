#!/usr/bin/env python3
"""Load the real routes in isolated Traefik, with fake certificates and no ACME."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from urllib.request import ProxyHandler, build_opener

import yaml

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def main():
    name = "traefik-config-test-" + secrets.token_hex(4)
    with tempfile.TemporaryDirectory(prefix=name) as temp:
        folder = Path(temp)
        (folder / "dynamic").mkdir()
        (folder / "certs").mkdir()
        run("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "1",
            "-subj", "/CN=dgkagramanyan.ru", "-addext", "subjectAltName=DNS:dgkagramanyan.ru,DNS:*.dgkagramanyan.ru,DNS:wiki.aripari.am,DNS:cloud.armclub.tech",
            "-keyout", str(folder / "test.key"), "-out", str(folder / "test.crt"))
        for domain in ("wiki.aripari.am", "cloud.armclub.tech"):
            (folder / "certs" / (domain + ".crt")).write_bytes((folder / "test.crt").read_bytes())
            (folder / "certs" / (domain + ".key")).write_bytes((folder / "test.key").read_bytes())
        static = yaml.safe_load((ROOT / "traefik.yml").read_text())
        static.pop("certificatesResolvers", None)
        static["providers"].pop("docker", None)
        static["api"]["insecure"] = True
        (folder / "traefik.yml").write_text(yaml.safe_dump(static))
        expected = {"http": set(), "tcp": set()}
        for path in (ROOT / "dynamic").glob("*.yml"):
            document = yaml.safe_load(path.read_text())
            for kind in expected:
                for router_name, router in document.get(kind, {}).get("routers", {}).items():
                    expected[kind].add(router_name + "@file")
                    if isinstance(router.get("tls"), dict):
                        router["tls"].pop("certResolver", None)
            (folder / "dynamic" / path.name).write_text(yaml.safe_dump(document))
        started = False
        try:
            run("docker", "run", "-d", "--name", name, "-p", "127.0.0.1::8080",
                "-v", f"{folder / 'traefik.yml'}:/etc/traefik/traefik.yml:ro",
                "-v", f"{folder / 'dynamic'}:/etc/traefik/dynamic:ro", "-v", f"{folder / 'certs'}:/certs:ro",
                os.environ.get("TRAEFIK_TEST_IMAGE", "traefik:v3.7.15"))
            started = True
            port = run("docker", "port", name, "8080/tcp").splitlines()[0].rsplit(":", 1)[1]
            opener = build_opener(ProxyHandler({}))
            for _ in range(100):
                try:
                    loaded = {}
                    for kind in expected:
                        with opener.open(f"http://127.0.0.1:{port}/api/{kind}/routers", timeout=2) as response:
                            loaded[kind] = {r["name"]: r for r in json.load(response)}
                    if all(expected[kind] <= loaded[kind].keys() for kind in expected):
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Traefik did not load the expected configuration")
            for kind in expected:
                for router in expected[kind]:
                    assert loaded[kind][router]["status"] == "enabled", router
            print(f"PASS: real Traefik enabled {sum(map(len, expected.values()))} production routes with isolated test certificates")
            (folder / "kuma_api_key").write_text("validation-only")
            run("docker", "run", "--rm", "--user", "0", "--entrypoint", "promtool",
                "-v", f"{ROOT / 'prometheus.yml'}:/etc/prometheus/prometheus.yml:ro",
                "-v", f"{folder / 'kuma_api_key'}:/etc/prometheus/kuma_api_key:ro",
                os.environ.get("PROMETHEUS_TEST_IMAGE", "prom/prometheus:v3.13.3"),
                "check", "config", "/etc/prometheus/prometheus.yml")
            print("PASS: promtool accepted the existing monitoring configuration")
        except Exception:
            if started:
                print(run("docker", "logs", "--tail=30", name))
            raise
        finally:
            if started:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True)


if __name__ == "__main__":
    main()
