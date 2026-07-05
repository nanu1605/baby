# Reaching Baby's UI from your phone — securely, via Tailscale

Baby's web UI binds to `127.0.0.1:8765` on purpose: nothing on your LAN or
the internet can reach it. Tailscale Serve proxies it onto your **private
tailnet** over HTTPS — only devices logged into YOUR Tailscale account can
connect. No port forwarding, no code change, the localhost bind stays.

## One-time setup

1. Install Tailscale on the PC: <https://tailscale.com/download/windows>
   (or `winget install tailscale.tailscale`). Sign in — Google/GitHub login
   is fine; the free Personal plan covers this.
2. Install the Tailscale app on your phone (Play Store / App Store) and sign
   in to the **same account**. Both devices now share a tailnet.
3. On the PC, expose Baby to the tailnet (PowerShell):

   ```powershell
   tailscale serve --https=443 --bg localhost:8765
   ```

   `--bg` keeps it running in the background across reboots. The first run
   prints your machine's tailnet URL, something like:

   ```
   https://<machine-name>.<tailnet-name>.ts.net/
   ```

4. Open that URL on your phone. Baby's chat + activity feed load over HTTPS
   (Tailscale provisions the certificate automatically).

## Verify

- Phone on mobile data (Wi-Fi off): the URL still works — traffic rides the
  encrypted tailnet, not your LAN.
- A browser on any device NOT in your tailnet: connection refused. That is
  the security model working.
- `tailscale serve status` on the PC shows the active proxy.

## Important: Serve, not Funnel

`tailscale serve` = tailnet-only (your devices). **Never** use
`tailscale funnel` for Baby — Funnel publishes to the open internet, and the
UI has no login of its own. The confirm modal approves gated actions; do not
expose it publicly.

## Undo

```powershell
tailscale serve reset
```

removes the proxy; Baby is localhost-only again.
