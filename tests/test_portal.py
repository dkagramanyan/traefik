"""Exercise actual Go templates and IP/path enforcement with a local Traefik binary."""
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import threading
import time

import pytest

from test_configuration import ROOT, fresh, prepared


def test_portal_settings(tmp_path):
    import settings
    root = fresh(tmp_path / "config")
    assert prepared(root)["PORTAL_CIDRS"] == ""
    settings.write_env(root / ".env", {"PORTAL_CIDRS": "192.0.2.13/24, 10.8.0.128/25"})
    assert prepared(root)["PORTAL_CIDRS"] == "192.0.2.0/24,10.8.0.128/25"
    for invalid in ("0.0.0.0/0", "::/0", "not-a-network"):
        settings.write_env(root / ".env", {"PORTAL_CIDRS": invalid})
        with pytest.raises(ValueError):
            prepared(root)


@pytest.mark.parametrize("guest_enabled", [False, True])
def test_private_portal_routes(tmp_path, guest_enabled):
    binary = os.environ.get("TRAEFIK_BINARY") or shutil.which("traefik")
    if not binary:
        pytest.skip("Set TRAEFIK_BINARY to run live routing checks without Docker")

    class Backend(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"backend-ok")

        def log_message(self, *_):
            pass

    backend = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    root = fresh(tmp_path / "config")
    values = prepared(root)
    values["TRUSTED_CIDRS"] = "127.0.0.2/32"
    values["PORTAL_CIDRS"] = "127.0.0.2/32,127.0.0.3/32" if guest_enabled else ""
    for key in values:
        if key.startswith("BACKEND_") and key not in ("BACKEND_HOST_ALIAS", "BACKEND_HOST_ADDRESS"):
            values[key] = f"http://127.0.0.1:{backend.server_port}"
    values["SERVICE_ROUTES_JSON"] = json.dumps([
        {"name": "extra", "host": "app.example.test", "url": values["BACKEND_OPENVPN"], "public": False, "paths": []},
        {"name": "status", "host": "status.example.test", "url": values["BACKEND_OPENVPN"], "public": True, "paths": ["/health", "/public/"]},
    ])
    dynamic = root / "dynamic"
    dynamic.mkdir()
    for name in ("native-services.yml", "portal.yml"):
        content = (ROOT / "dynamic" / name).read_text().replace("        certResolver: letsencrypt\n", "")
        (dynamic / name).write_text(content)
    ports = {}
    for name in ("websecure", "portal", "web", "apple-relay"):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            ports[name] = sock.getsockname()[1]
    args = [binary, f"--providers.file.directory={dynamic}"]
    args += [f"--entrypoints.{name}.address=127.0.0.1:{port}" for name, port in ports.items()]
    env = os.environ | values
    if not guest_enabled:
        env.pop("PORTAL_CIDRS", None)  # Existing container upgraded without a new env value.

    def request(entry, source, path, host="openvpn.example.test"):
        sock = socket.create_connection(("127.0.0.1", ports[entry]), timeout=2, source_address=(source, 0))
        with ssl._create_unverified_context().wrap_socket(sock, server_hostname=host) as conn:
            conn.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nX-Forwarded-For: 127.0.0.2\r\nConnection: close\r\n\r\n").encode())
            return int(conn.recv(4096).split()[1])

    with (root / "traefik.log").open("w+") as log:
        process = subprocess.Popen(args, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    if request("websecure", "127.0.0.2", "/portal") == 200:
                        break
                except (OSError, ValueError, IndexError):
                    pass
                if process.poll() is not None or time.monotonic() > deadline:
                    log.seek(0)
                    pytest.fail(log.read())
                time.sleep(0.1)
            for entry in ("websecure", "portal"):
                for path in ("/portal", "/api/portal", "/api/portal/me", "/static/app.js"):
                    assert request(entry, "127.0.0.2", path) == 200
                    assert request(entry, "127.0.0.3", path) == (200 if guest_enabled else 403)
                    # Spoofed X-Forwarded-For cannot grant an unlisted source access.
                    assert request(entry, "127.0.0.4", path) == 403
                for path in ("/", "/api/clients", "/internal/relay-sign", "/portal-admin", "/api/portal-admin"):
                    assert request(entry, "127.0.0.3", path) == (403 if entry == "websecure" else 404)
            assert request("websecure", "127.0.0.2", "/api/clients") == 200
            assert request("portal", "127.0.0.2", "/api/clients") == 404
            assert request("portal", "127.0.0.2", "/portal", "grafana.example.test") == 404
            assert request("websecure", "127.0.0.2", "/", "app.example.test") == 200
            assert request("websecure", "127.0.0.4", "/", "app.example.test") == 403
            assert request("portal", "127.0.0.2", "/", "app.example.test") == 404
            for path in ("/health", "/public/example"):
                assert request("websecure", "127.0.0.4", path, "status.example.test") == 200
            for path in ("/admin", "/publicity"):
                assert request("websecure", "127.0.0.2", path, "status.example.test") == 404
        finally:
            process.terminate()
            process.wait(timeout=10)
            backend.shutdown()
            backend.server_close()
