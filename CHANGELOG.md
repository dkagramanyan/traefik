# Changelog

## 1.3.0 - 2026-11-26

* The 3x-ui panel of the OpenVPN server next to its website: when the gateway
  file has `xui_panel` (OpenVPN UI 2.9.0), router `xui-panel` serves
  `OPENVPN_HOST` paths under the panel's random base path from `BACKEND_XUI`,
  for `TRUSTED_CIDRS` only. Without it the route is gone.

## 1.2.0 - 2026-11-26

* One-time profile links of the OpenVPN server open from anywhere: router
  `openvpn-share` routes `OPENVPN_HOST` paths under `/p/` on the public HTTPS
  listener, with a per-address rate limit. The rest of the OpenVPN website
  stays for trusted networks.

## 1.1.0 - 2026-11-04

* Guest links from the OpenVPN UI's gateway file (`guest_links`): a router per
  link on the guest portal listener, reusing the backend of the existing route
  for its hostname, limited to the link's path or the whole host.

## 1.0.0 - 2026-11-04

First release of the portable gateway, split out of the OpenVPN repository.

* Traefik v3.7 with DNS-challenge certificates (Selectel v2 by default), the
  dashboard, Grafana, an optional Prometheus metrics agent (remote write only)
  and private-service routes, all configured from `.env` by `./manage setup`.
* `./manage adopt` turns an existing non-Git deployment into this layout,
  keeping its certificates, ACME state, dashboard login and Grafana data.
* Apple Network Relay forwarding: TLS termination with ALPN `h2` to the
  OpenVPN server's relay backend.
* Profile signer for Apple relay profiles, built from a pinned openvpn-server
  revision; private keys stay on the gateway.
* **Guest portal listener**: guests of the OpenVPN server and its relay reach
  only the personal portal through the `portal` entrypoint; the regular HTTPS
  listener has no portal exception.
* `./manage setup --network-file` imports the gateway file (version 2) from the
  OpenVPN UI. The OpenVPN server's address now also sets `SIGNER_SOURCE_CIDRS`
  and `PORTAL_CIDRS` on every import (before, a changed server address left a
  stale signer allowlist behind).
