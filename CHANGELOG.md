# Changelog

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
