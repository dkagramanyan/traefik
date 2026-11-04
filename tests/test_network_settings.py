import json
from pathlib import Path

import pytest

from test_configuration import fresh, prepared
import network_settings
import settings


def export():
    return {"version": 1, "gateway": {"domain": "example.test", "openvpn_host": "vpn.example.test",
        "openvpn_backend": "http://192.0.2.20:8080", "relay_host": "relay.example.test", "relay_backend": "192.0.2.20:8444",
        "trusted_cidrs": ["192.0.2.0/24"], "portal_cidrs": ["192.0.2.0/24", "10.8.0.0/24"],
        "services": [{"name": "example", "host": "app.example.test", "url": "http://192.0.2.30:8080"}]},
        "portal": {"enabled": True, "host": "vpn.example.test", "target": "192.0.2.10:9443", "destinations": ["198.51.100.10"]}}


def test_ui_export_import_preserves_credentials(tmp_path):
    root = fresh(tmp_path / "gateway")
    old = prepared(root)
    settings.materialize(root, old)
    path = tmp_path / "network.private.json"
    path.write_text(json.dumps(export()))
    values = settings.prepare(root, network_file=path)
    assert values["DASHBOARD_AUTH"] == old["DASHBOARD_AUTH"]
    assert values["OPENVPN_HOST"] == "vpn.example.test"
    assert values["PORTAL_LISTEN_ADDRESS"] == "192.0.2.10"
    assert json.loads(values["SERVICE_ROUTES_JSON"])[0]["public"] is False
    settings.materialize(root, values)
    assert (root / ".env").stat().st_mode & 0o777 == 0o600


def test_public_relay_url_preserves_existing_gateway_listener(tmp_path):
    data = export()
    data["gateway"].update(relay_public_url="https://relay.example.test:9444")
    path = tmp_path / "network.private.json"
    path.write_text(json.dumps(data))
    values = network_settings.from_file(path)
    assert values["APPLE_RELAY_URL"] == "https://relay.example.test:9444"
    assert "APPLE_RELAY_PORT" not in values
    assert values["APPLE_RELAY_BACKEND"] == "192.0.2.20:8444"


def test_portal_listener_can_be_deployed_before_guest_access_is_enabled(tmp_path):
    data = export()
    data["portal"]["enabled"] = False
    path = tmp_path / "network.private.json"
    path.write_text(json.dumps(data))
    values = network_settings.from_file(path)
    assert values["PORTAL_LISTEN_ADDRESS"] == "192.0.2.10"
    assert values["PORTAL_HTTPS_PORT"] == "9443"


def test_optional_service_inventory_is_preserved_until_explicit_replacement(tmp_path):
    root = fresh(tmp_path / "gateway")
    old = prepared(root)
    old["SERVICE_ROUTES_JSON"] = json.dumps([{"name": "existing", "host": "existing.example.test", "url": "http://192.0.2.30:8000"}])
    old["IMPORTED_DYNAMIC_JSON"] = json.dumps({"http": {"routers": {"legacy": {"rule": "Host(`legacy.example.test`)"}}}})
    settings.materialize(root, old)
    data = export()
    data["gateway"].update(replace_services=False, services=[])
    path = tmp_path / "network.private.json"
    path.write_text(json.dumps(data))
    values = settings.prepare(root, network_file=path)
    assert json.loads(values["SERVICE_ROUTES_JSON"])[0]["name"] == "existing"
    assert values["IMPORTED_DYNAMIC_JSON"] == old["IMPORTED_DYNAMIC_JSON"]


@pytest.mark.parametrize("case", ["public_portal", "wide_network", "template_url", "string_bool", "admin_port"])
def test_invalid_exports_are_rejected(tmp_path, case):
    data = export()
    service = data["gateway"]["services"][0]
    if case == "public_portal": service.update(host="vpn.example.test", public=True)
    if case == "wide_network": data["gateway"]["trusted_cidrs"] = ["0.0.0.0/0"]
    if case == "template_url": service["url"] = "http://{{env`SECRET`}}"
    if case == "string_bool": service["public"] = "false"
    if case == "admin_port": data["portal"]["target"] = "192.0.2.10:443"
    path = tmp_path / "network.private.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError): network_settings.from_file(path)
