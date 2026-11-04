"""Exercise actual Go templates and IP/path enforcement with a local Traefik binary."""
import http.server
import json
import os
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


@pytest.mark.parametrize("server_listed", [False, True])
def test_guest_portal_routes(tmp_path, server_listed):
    binary = os.environ.get("TRAEFIK_BINARY") or shutil.which("traefik")
    if not binary:
        pytest.skip("Set TRAEFIK_BINARY to run live routing checks without Docker")

    class Backend(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            # Model the service-owned boundary. Actual application middleware
            # is tested in openvpn-server; Traefik must always supply the flag.
            restricted = self.headers.get('X-OpenVPN-Portal-Only') == '1'
            permitted = self.path in ('/portal', '/api/portal') or self.path.startswith(('/api/portal/', '/static/'))
            status = 303 if restricted and self.path == '/' else 404 if restricted and not permitted else 200
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b"backend-ok")

        def log_message(self, *_):
            pass

        do_POST = do_GET

    backend = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    root = fresh(tmp_path / "config")
    values = prepared(root)
    values["TRUSTED_CIDRS"] = "127.0.0.2/32"
    # 127.0.0.2 is a trusted admin; 127.0.0.3 the OpenVPN server; 127.0.0.4 anyone else.
    values["PORTAL_CIDRS"] = "127.0.0.3/32" if server_listed else ""
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
    if not server_listed:
        env.pop("PORTAL_CIDRS", None)  # Falls back to TRUSTED_CIDRS.

    def request(entry, source, path, host="openvpn.example.test", method="GET"):
        sock = socket.create_connection(("127.0.0.1", ports[entry]), timeout=2, source_address=(source, 0))
        with ssl._create_unverified_context().wrap_socket(sock, server_hostname=host) as conn:
            conn.sendall((f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nX-Forwarded-For: 127.0.0.2\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").encode())
            return int(conn.recv(4096).split()[1])

    with (root / "traefik.log").open("w+") as log:
        process = subprocess.Popen(args, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    if request("websecure", "127.0.0.2", "/api/clients") == 200:
                        break
                except (OSError, ValueError, IndexError):
                    pass
                if process.poll() is not None or time.monotonic() > deadline:
                    log.seek(0)
                    pytest.fail(log.read())
                time.sleep(0.1)
            portal_source = "127.0.0.3" if server_listed else "127.0.0.2"
            for path in ("/portal", "/api/portal", "/api/portal/me", "/api/portal/relay/status", "/static/app.js"):
                assert request("portal", portal_source, path) == 200
                # Spoofed X-Forwarded-For cannot grant an unlisted source access.
                assert request("portal", "127.0.0.4", path) == 403
                # The regular listener has no portal exception: trusted networks only.
                assert request("websecure", "127.0.0.3", path) == 403
            for path in ("/api/portal/relay.mobileconfig", "/api/portal/password"):
                assert request("portal", portal_source, path, method="POST") == 200
                assert request("portal", "127.0.0.4", path, method="POST") == 403
            # The application refuses everything but the portal on this listener.
            for path in ("/", "/api/clients", "/internal/relay-sign", "/portal-admin", "/api/portal-admin"):
                assert request("portal", portal_source, path) == (303 if path == "/" else 404)
            assert request("portal", portal_source, "/portal", "grafana.example.test") == 404
            assert request("portal", portal_source, "/", "app.example.test") == 404
            assert request("websecure", "127.0.0.2", "/api/clients") == 200
            assert request("websecure", "127.0.0.2", "/", "app.example.test") == 200
            assert request("websecure", "127.0.0.4", "/", "app.example.test") == 403
            for path in ("/health", "/public/example"):
                assert request("websecure", "127.0.0.4", path, "status.example.test") == 200
            for path in ("/admin", "/publicity"):
                assert request("websecure", "127.0.0.2", path, "status.example.test") == 404
        finally:
            process.terminate()
            process.wait(timeout=10)
            backend.shutdown()
            backend.server_close()
