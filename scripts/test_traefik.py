#!/usr/bin/env python3
"""Exercise portable routes, relay TLS forwarding and metrics in real containers."""
import json
import base64
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import tempfile
import time
from urllib.request import ProxyHandler, Request, build_opener

import yaml
from settings import ROOT, materialize, password_hash
from validate import fixture


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Command {args[0]} {args[1]} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


def eventually(check, description, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if result := check(): return result
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.3)
    raise RuntimeError("Timed out: " + description)


def main():
    prefix = "traefik-check-" + secrets.token_hex(4)
    containers = []
    network_created = False
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix=prefix) as temp:
        folder = Path(temp)
        try:
            values = fixture(folder)
            values.update({"METRICS_REMOTE_WRITE_URL": "http://receiver:9090/api/v1/write", "TRUSTED_CIDRS": "127.0.0.1/32", "METRICS_USERNAME": "metrics", "METRICS_PASSWORD": "metrics-$literal-password"})
            values["SELECTELV2_PASSWORD"] = "fake-$literal-${CREDENTIAL}-'value"
            # Real bootstrap bcrypt hash, using fake credentials.
            values["DASHBOARD_AUTH"] = password_hash("test", "generated-test-password")
            for key in values:
                if key.startswith("BACKEND_") and key not in ("BACKEND_HOST_ALIAS", "BACKEND_HOST_ADDRESS"):
                    values[key] = "http://backend:8000"
            values["APPLE_RELAY_BACKEND"] = "backend:8444"
            values["SERVICE_ROUTES_JSON"] = json.dumps([
                {"name": "private", "host": "private.example.test", "url": "http://backend:8000", "public": False, "paths": [], "insecure_tls": False},
                {"name": "public", "host": "public.example.test", "url": "http://backend:8000", "public": True, "paths": [], "insecure_tls": False},
            ])
            materialize(folder, values)
            run("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "1",
                "-subj", "/CN=example.test", "-addext", "subjectAltName=DNS:example.test,DNS:*.example.test",
                "-keyout", str(folder / "test.key"), "-out", str(folder / "test.crt"))
            dynamic = folder / "dynamic"
            dynamic.mkdir()
            for path in (ROOT / "dynamic").glob("*.yml"):
                (dynamic / path.name).write_text(path.read_text())
            # Load the test identity without making a production ACME request.
            (dynamic / "test-cert.yml").write_text("tls:\n  certificates:\n    - certFile: /certs/test.crt\n      keyFile: /certs/test.key\n")
            native = dynamic / "native-services.yml"
            native.write_text(native.read_text().replace("        certResolver: letsencrypt\n", ""))
            backend_code = '''import http.server, socketserver, threading
class HTTP(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write(b"backend-ok")
 def log_message(self,*args): pass
class Echo(socketserver.BaseRequestHandler):
 def handle(self):
  self.request.sendall(self.request.recv(4096))
threading.Thread(target=http.server.ThreadingHTTPServer(("0.0.0.0",8000),HTTP).serve_forever,daemon=True).start()
socketserver.TCPServer(("0.0.0.0",8444),Echo).serve_forever()
'''
            (folder / "backend.py").write_text(backend_code)
            run("docker", "network", "create", network := prefix + "-net")
            network_created = True
            def start(name, image, *args, options=()):
                name = prefix + "-" + name
                run("docker", "run", "-d", "--name", name, "--network", network, *options, image, *args)
                containers.append(name)
                return name
            python_image = os.environ.get("PYTHON_TEST_IMAGE", "python:3.14-alpine3.24")
            start("backend", python_image, "python", "/backend.py", options=("--network-alias", "backend", "-v", f"{folder / 'backend.py'}:/backend.py:ro"))
            prom_image = os.environ.get("PROMETHEUS_TEST_IMAGE", "prom/prometheus:v3.13.3")
            (folder / "receiver.yml").write_text("global: {scrape_interval: 1s}\nscrape_configs: []\n")
            (folder / "receiver-web.yml").write_text(yaml.safe_dump({"basic_auth_users": {"metrics": password_hash("metrics", values["METRICS_PASSWORD"]).split(":", 1)[1]}}))
            receiver = start("receiver", prom_image, "--config.file=/etc/prometheus/prometheus.yml", "--web.enable-remote-write-receiver", "--web.config.file=/etc/prometheus/web.yml",
                             options=("--network-alias", "receiver", "-p", "127.0.0.1::9090", "-v", f"{folder / 'receiver.yml'}:/etc/prometheus/prometheus.yml:ro", "-v", f"{folder / 'receiver-web.yml'}:/etc/prometheus/web.yml:ro"))
            trusted = start("trusted", python_image, "sleep", "180")
            trusted_ip = run("docker", "inspect", trusted, "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}")
            values["TRUSTED_CIDRS"] += "," + trusted_ip + "/32"
            materialize(folder, values)
            compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
            # The bootstrap user and Traefik can have different file ownership.
            # Exercise the signer's actual read-only capability with a private store.
            private_store = folder / "private-store-test"
            private_store.write_text("operator-owned-test-store")
            private_store.chmod(0o600)
            caps = []
            for capability in compose["services"]["profile-signer"].get("cap_add", []):
                caps.extend(["--cap-add", capability])
            run("docker", "run", "--rm", "--cap-drop", "ALL", *caps, "--read-only",
                "-v", f"{private_store}:/letsencrypt/acme.json:ro", python_image, "python", "-c",
                'assert open("/letsencrypt/acme.json").read() == "operator-owned-test-store"')
            print("PASS: signer permissions read an operator-owned private ACME store through a read-only mount")
            # Resolve production CLI interpolation through Compose itself.
            (folder / "docker-compose.yml").write_text((ROOT / "docker-compose.yml").read_text())
            resolved = json.loads(run("docker", "compose", "--project-directory", str(folder), "config", "--format", "json"))
            traefik = resolved["services"]["traefik"]
            assert traefik["environment"]["SELECTELV2_PASSWORD"].replace("$$", "$") == values["SELECTELV2_PASSWORD"]
            cli = [arg.replace("$$", "$") for arg in traefik["command"] if not arg.startswith("--providers.docker.")]
            cli = ["--certificatesresolvers.letsencrypt.acme.caserver=https://127.0.0.1:1/directory" if ".caserver=" in arg else arg for arg in cli]
            cli.append("--api.insecure=true")
            (dynamic / "dashboard-test.yml").write_text(yaml.safe_dump({"http": {
                "routers": {"dashboard-test": {"rule": "Host(`dashboard.example.test`)", "entryPoints": ["websecure"],
                    "tls": {}, "service": "api@internal", "middlewares": ["dashboard-test-auth"]}},
                "middlewares": {"dashboard-test-auth": {"basicAuth": {"users": [values["DASHBOARD_AUTH"]]}}},
            }}))
            env_options = []
            # Docker env-file syntax differs from Compose dotenv quoting.
            # These are fake credentials; pass their literal values explicitly.
            for key, value in values.items(): env_options.extend(["-e", key + "=" + value])
            proxy = start("proxy", os.environ.get("TRAEFIK_TEST_IMAGE", "traefik:v3.7.15"), *cli,
                          options=("--network-alias", "traefik", "-p", "127.0.0.1::8080", "-p", "127.0.0.1::80", "-p", "127.0.0.1::8443",
                                   "-v", f"{dynamic}:/etc/traefik/dynamic:ro", "-v", f"{folder}:/certs:ro", "-v", f"{folder / 'letsencrypt'}:/letsencrypt", *env_options))
            def port(container, number):
                return run("docker", "port", container, str(number) + "/tcp").splitlines()[0].rsplit(":", 1)[1]
            api_port = port(proxy, 8080)
            def get(url, headers=None):
                with opener.open(Request(url, headers=headers or {}), timeout=3) as response:
                    return response.read()
            def routes():
                http = json.loads(get(f"http://127.0.0.1:{api_port}/api/http/routers"))
                tcp = json.loads(get(f"http://127.0.0.1:{api_port}/api/tcp/routers"))
                found = {r["name"]: r for r in http + tcp if r["name"].endswith("@file")}
                return found if len(found) == 7 and all(r["status"] == "enabled" for r in found.values()) else None
            loaded = eventually(routes, "enabled core and synthetic service routes")
            for name in ("openvpn", "custom-private"):
                assert "trusted-only@file" in loaded[name + "@file"]["middlewares"]
            # Unlisted sources cannot reach the portal or management APIs.
            proxy_ip = run("docker", "inspect", proxy, "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}")
            client = start("client", python_image, "sleep", "120", options=("--add-host", "openvpn.example.test:" + proxy_ip, "--add-host", "dashboard.example.test:" + proxy_ip))
            request_code = '''import urllib.request,urllib.error
u=urllib.request.Request("https://openvpn.example.test"+__import__("sys").argv[1],headers={"Host":"openvpn.example.test"})
try:
 r=urllib.request.urlopen(u,context=__import__("ssl")._create_unverified_context(),timeout=5)
 print(r.status,r.read().decode())
except urllib.error.HTTPError as e: print(e.code)
'''
            assert run("docker", "exec", client, "python", "-c", request_code, "/portal") == "403"
            assert run("docker", "exec", client, "python", "-c", request_code, "/api/clients") == "403"
            assert run("docker", "exec", client, "python", "-c", request_code, "/api/portal/me") == "403"
            trusted_code = '''import socket,ssl,sys
s=ssl._create_unverified_context().wrap_socket(socket.create_connection((sys.argv[1],443),timeout=5),server_hostname="openvpn.example.test")
s.sendall(b"GET /api/clients HTTP/1.1\\r\\nHost: openvpn.example.test\\r\\nConnection: close\\r\\n\\r\\n")
print(s.recv(4096).split(b"\\r\\n")[0].decode())
'''
            assert "200" in run("docker", "exec", trusted, "python", "-c", trusted_code, proxy_ip)
            dashboard_code = '''import urllib.request,urllib.error,ssl,sys
request=urllib.request.Request("https://dashboard.example.test/api/overview",headers={"Authorization":"Basic "+sys.argv[1]})
try:
 print(urllib.request.urlopen(request,context=ssl._create_unverified_context()).status)
except urllib.error.HTTPError as e: print(e.code)
'''
            good_auth = base64.b64encode(b"test:generated-test-password").decode()
            bad_auth = base64.b64encode(b"test:wrong-password").decode()
            assert run("docker", "exec", client, "python", "-c", dashboard_code, good_auth) == "200"
            assert run("docker", "exec", client, "python", "-c", dashboard_code, bad_auth) == "401"

            # Real TLS termination, h2 ALPN and bidirectional relay bytes.
            context = ssl.create_default_context(cafile=str(folder / "test.crt"))
            context.set_alpn_protocols(["h2"])
            with context.wrap_socket(socket.create_connection(("127.0.0.1", int(port(proxy, 8443))), timeout=5), server_hostname="example.test") as connection:
                assert connection.selected_alpn_protocol() == "h2"
                message = b"relay-forwarding-check"
                connection.sendall(message)
                assert connection.recv(4096) == message
            print("PASS: private access rules, private portal, configured services and TLS/h2 relay forwarding")
            # Run the actual agent command, with a real remote-write receiver.
            agent_options = ("--user", values["LOCAL_UID"] + ":" + values["LOCAL_GID"],
                             "-v", f"{folder / '.runtime'}:/etc/prometheus:ro", "-v", f"{folder / 'state/metrics-agent'}:/prometheus")
            agent = start("agent", prom_image, *compose["services"]["metrics-agent"]["command"], options=agent_options)
            receiver_port = port(receiver, 9090)
            def received():
                auth = "Basic " + base64.b64encode(("metrics:" + values["METRICS_PASSWORD"]).encode()).decode()
                response = json.loads(get(f"http://127.0.0.1:{receiver_port}/api/v1/query?query=up%7Bjob%3D%22traefik%22%7D", {"Authorization": auth}))
                result = response["data"]["result"]
                return result and result[0]["value"][1] == "1"
            eventually(received, "Traefik metrics received through the agent", timeout=70)
            assert run("docker", "inspect", agent, "--format", "{{.State.Running}}") == "true"
            print("PASS: agent scraped only Traefik and forwarded samples to a real remote-write receiver")
        except Exception:
            for name in containers:
                print(name, run("docker", "logs", "--tail=12", name))
            raise
        finally:
            for name in reversed(containers):
                subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            if network_created:
                subprocess.run(["docker", "network", "rm", network], capture_output=True)


if __name__ == "__main__":
    main()
