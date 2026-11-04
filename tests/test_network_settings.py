import json

import pytest

from test_configuration import fresh, prepared
import network_settings
import settings


def export(**changes):
    data = {"version": 2, "domain": "example.test", "openvpn_host": "vpn.example.test",
            "openvpn_backend": "http://192.0.2.20:8080", "relay_host": "relay.example.test", "relay_backend": "192.0.2.20:8444",
            "relay_public_url": "", "portal_listener": "192.0.2.10:9443", "trusted_cidrs": ["192.0.2.0/24"],
            "guest_links": [{"host": "app.example.test", "path": "/share/abc"}],
            "services": [{"name": "example", "host": "app.example.test", "url": "http://192.0.2.30:8080"}]}
    data.update(changes)
    return data


def write(tmp_path, data):
    path = tmp_path / "network.private.json"
    path.write_text(json.dumps(data))
    return path


def test_ui_export_import_preserves_credentials(tmp_path):
    root = fresh(tmp_path / "gateway")
    old = prepared(root)
    settings.materialize(root, old)
    values = settings.prepare(root, network_file=write(tmp_path, export()))
    assert values["DASHBOARD_AUTH"] == old["DASHBOARD_AUTH"]
    assert values["OPENVPN_HOST"] == "vpn.example.test"
    assert (values["PORTAL_LISTEN_ADDRESS"], values["PORTAL_HTTPS_PORT"]) == ("192.0.2.10", "9443")
    assert json.loads(values["SERVICE_ROUTES_JSON"])[0]["public"] is False
    settings.materialize(root, values)
    assert (root / ".env").stat().st_mode & 0o777 == 0o600


def test_the_openvpn_server_is_the_only_portal_and_signer_source(tmp_path):
    values = network_settings.from_file(write(tmp_path, export()))
    assert values["PORTAL_CIDRS"] == values["SIGNER_SOURCE_CIDRS"] == "192.0.2.20/32"
    assert values["TRUSTED_CIDRS"] == "192.0.2.0/24"
    # A new backend address moves both, even when .env already had values.
    root = fresh(tmp_path / "gateway")
    settings.materialize(root, prepared(root))
    moved = settings.prepare(root, network_file=write(tmp_path, export(openvpn_backend="http://192.0.2.21:8080")))
    assert moved["PORTAL_CIDRS"] == moved["SIGNER_SOURCE_CIDRS"] == "192.0.2.21/32"


def test_public_relay_url_preserves_existing_gateway_listener(tmp_path):
    values = network_settings.from_file(write(tmp_path, export(relay_public_url="https://relay.example.test:9444")))
    assert values["APPLE_RELAY_URL"] == "https://relay.example.test:9444"
    assert "APPLE_RELAY_PORT" not in values
    assert values["APPLE_RELAY_BACKEND"] == "192.0.2.20:8444"


def test_without_a_listener_the_portal_stays_on_loopback(tmp_path):
    values = network_settings.from_file(write(tmp_path, export(portal_listener="")))
    assert (values["PORTAL_LISTEN_ADDRESS"], values["PORTAL_HTTPS_PORT"]) == ("127.0.0.1", "9443")


def test_service_inventory_is_kept_unless_the_file_lists_services(tmp_path):
    root = fresh(tmp_path / "gateway")
    old = prepared(root)
    old["SERVICE_ROUTES_JSON"] = json.dumps([{"name": "existing", "host": "existing.example.test", "url": "http://192.0.2.30:8000"}])
    old["IMPORTED_DYNAMIC_JSON"] = json.dumps({"http": {"routers": {"legacy": {"rule": "Host(`legacy.example.test`)"}}}})
    settings.materialize(root, old)
    values = settings.prepare(root, network_file=write(tmp_path, export(services=None, guest_links=[])))
    assert json.loads(values["SERVICE_ROUTES_JSON"])[0]["name"] == "existing"
    assert values["IMPORTED_DYNAMIC_JSON"] == old["IMPORTED_DYNAMIC_JSON"]
    values = settings.prepare(root, network_file=write(tmp_path, export(services=[], guest_links=[])))
    assert json.loads(values["SERVICE_ROUTES_JSON"]) == []


def test_empty_trusted_list_keeps_gateway_administrator_access(tmp_path):
    root = fresh(tmp_path / "gateway")
    old = prepared(root)
    old["TRUSTED_CIDRS"] = "192.0.2.77/32,2001:db8::77/128"
    settings.materialize(root, old)
    values = settings.prepare(root, network_file=write(tmp_path, export(trusted_cidrs=[])))
    assert values["TRUSTED_CIDRS"] == old["TRUSTED_CIDRS"]
    assert values["PORTAL_CIDRS"] == "192.0.2.20/32"


def test_first_import_keeps_loopback_only_admin_defaults(tmp_path):
    values = settings.prepare(fresh(tmp_path / "gateway"), network_file=write(tmp_path, export(trusted_cidrs=[])))
    assert values["TRUSTED_CIDRS"] == "127.0.0.1/32,::1/128"


def test_guest_links_use_the_existing_route_of_their_hostname(tmp_path):
    root = fresh(tmp_path / "gateway")
    values = settings.prepare(root, network_file=write(tmp_path, export()))
    assert json.loads(values["GUEST_LINK_ROUTES_JSON"]) == [{"name": "guest-link-1",
        "rule": "Host(`app.example.test`) && (Path(`/share/abc`) || PathPrefix(`/share/abc/`))",
        "service": "custom-example", "middlewares": ["portal-sources"]}]
    # Adopted routes work too; their private-network allowlist is not copied.
    imported = {"http": {"routers": {"files": {"rule": "Host(`files.example.test`)", "service": "files", "middlewares": ["trusted-only", "auth"]}}}}
    routes = network_settings.link_routes([{"host": "files.example.test", "path": "/"}], {"IMPORTED_DYNAMIC_JSON": json.dumps(imported)})
    assert routes == [{"name": "guest-link-1", "rule": "Host(`files.example.test`)", "service": "files", "middlewares": ["portal-sources", "auth"]}]
    with pytest.raises(ValueError, match="no route"):
        network_settings.link_routes([{"host": "missing.example.test", "path": "/"}], {})


@pytest.mark.parametrize("case", ["old_version", "public_portal", "wide_network", "template_url", "string_bool",
                                  "admin_port", "loopback_listener", "relay_url_host", "link_off_domain", "link_portal", "link_path"])
def test_invalid_files_are_rejected(tmp_path, case):
    data = export()
    service = data["services"][0]
    if case == "old_version": data["version"] = 1
    if case == "public_portal": service.update(host="vpn.example.test", public=True)
    if case == "wide_network": data["trusted_cidrs"] = ["0.0.0.0/0"]
    if case == "template_url": service["url"] = "http://{{env`SECRET`}}"
    if case == "string_bool": service["public"] = "false"
    if case == "admin_port": data["portal_listener"] = "192.0.2.10:443"
    if case == "loopback_listener": data["portal_listener"] = "127.0.0.1:9443"
    if case == "relay_url_host": data["relay_public_url"] = "https://other.example.test:8443"
    if case == "link_off_domain": data["guest_links"] = [{"host": "other.test", "path": "/"}]
    if case == "link_portal": data["guest_links"] = [{"host": "vpn.example.test", "path": "/"}]
    if case == "link_path": data["guest_links"] = [{"host": "app.example.test", "path": "/{{env}}"}]
    with pytest.raises(ValueError):
        network_settings.from_file(write(tmp_path, data))
