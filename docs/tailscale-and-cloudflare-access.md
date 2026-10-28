# Reaching the docker-public apps: Tailscale and Cloudflare

This file is shared across the hungry-hippos, macro-log, reconcile and watchlist repos. It was written on 2026-10-28.

## Summary

The apps sometimes felt slow. Measurements on 2026-10-28 traced this to **Cloudflare's routing**, not the apps:

| Path | Typical | Worst seen |
|---|---|---|
| Inside the app container (no network) | 1–30 ms reads, 30–60 ms writes | ~85 ms |
| Through Cloudflare, served from Chicago (ORD) | ~150 ms | ~440 ms |
| Through Cloudflare, served from far away (Tokyo, São Paulo, London, Manchester, Paris) | 0.7–2.4 s | 11 s |

- Over 40 minutes, only ~14% of requests went through Chicago, and ~60% went through Atlanta. Stretches of several minutes went entirely through Tokyo or São Paulo.
- The tunnel itself only connects to Chicago, so every far-away request also crosses Cloudflare's network back to Chicago.
- Ruled out: the apps, Postgres (tiny and healthy), uvicorn/gunicorn, and the VM's CPU and swap.
- No Cloudflare plan officially guarantees which data center serves you. Only Enterprise lists "network prioritization".

**The fix:** each app is now also on the tailnet through its own Tailscale container. At home the phone connects to the server over the LAN, and away from home it connects directly (or through Tailscale's Chicago relay). Cloudflare is unchanged and still works as a fallback.

## How to reach each app

| App | Tailscale (fast; phone only) | Cloudflare (fallback; Okta login) | Backend container |
|---|---|---|---|
| Hungry Hippos | https://hungryhippos.tail3a09ed.ts.net | https://hungryhippos.stizzle.org | `hungry-hippos:8000` |
| Macro Log | https://macrolog.tail3a09ed.ts.net | https://macrolog.stizzle.org | `macrolog-app:8000` |
| Reconcile | https://reconcile.tail3a09ed.ts.net | https://reconcile.stizzle.org | `reconcile:8000` |
| Watchlist | https://watchlist.tail3a09ed.ts.net | https://watchlist.stizzle.org | `watchlist:8000` |

The phone's home-screen apps (PWAs) are installed from the **Tailscale** URLs.

## Tailscale

### Using it
- The Tailscale app must be connected on the phone. Keep Android's always-on VPN enabled for Tailscale.
- Only the phone `adams-s24` is allowed through the tailnet policy (see below). Other devices get "connection aborted".
- In the Tailscale app, tap a machine to see whether the connection is **direct** or relayed (`relay "ord"`). Both are fine.
- The server's NAT is the easy kind (`MappingVariesByDestIP: false`), and its nearest relay is Chicago at ~20 ms.

### How it's set up on docker-public
Each app has its own sidecar container in `/home/dockerapps/ts-<app>`:

| Directory | Container | Tailnet name |
|---|---|---|
| `ts-hungry-hippos` | `hungry-hippos-ts` | `hungryhippos` |
| `ts-macrolog` | `macrolog-ts` | `macrolog` |
| `ts-reconcile` | `reconcile-ts` | `reconcile` |
| `ts-watchlist` | `watchlist-ts` | `watchlist` |

Each directory holds:
- `compose.yaml`: the `tailscale/tailscale:stable` image in userspace mode (`TS_USERSPACE=true`), with state in a `ts-state` volume and joined to the external `cf-tunnel` network, so it reaches the app by container name.
- `serve.json`: `tailscale serve` config, HTTPS on 443 proxying `/` to `http://<backend>:8000`, tailnet only.
- `.env`: `TS_AUTHKEY=` is left **blank**. The auth key was single-use; the login lives in the `ts-state` volume.

These directories are separate from each app's own deploy (`update-app.sh`), so app deploys don't touch them.

**Gotcha: the container hostname must not equal the backend's name.** Inside a container, its own hostname resolves to itself. So a sidecar with `hostname: reconcile` that proxies to `http://reconcile:8000` connects to itself and gets "connection refused". The fix is to set the tailnet name with `TS_HOSTNAME` and give the container a different hostname. reconcile and watchlist use `hostname: <app>-ts` plus `TS_HOSTNAME: <app>`. hungry hippos and macrolog didn't hit this, because their backends are named `hungry-hippos` and `macrolog-app`.

In the admin console, **key expiry is disabled** on all four sidecar machines.

### Common commands (on docker-public)

```bash
cd /home/dockerapps/ts-<app>
docker compose up -d                                 # start / apply compose changes
docker compose pull && docker compose up -d          # update Tailscale
docker exec <app>-ts tailscale status                # logged in? which peers?
docker exec <app>-ts tailscale serve status          # https://<name>.tail3a09ed.ts.net -> proxy
docker exec <app>-ts tailscale netcheck              # NAT type, nearest relay
docker exec <app>-ts wget -qS -O /dev/null http://<backend>:8000/   # can the sidecar reach the app?
docker logs --since 10m <app>-ts | grep Drop          # connections blocked by the tailnet policy
```

| Symptom | Likely cause |
|---|---|
| "Connection aborted" on the phone, and `Drop: ... no rules matched` in the sidecar logs | The tailnet policy doesn't allow that device. Add it (see below). |
| `http: proxy error: ... connection refused` in the sidecar logs | The backend is down, or the hostname gotcha above. |
| The sidecar is logged out after a volume wipe | Generate a new single-use auth key, put it in `.env`, run `docker compose up -d`, then blank the key again and disable key expiry. |

### Re-creating a sidecar for a new app
1. Copy an existing `ts-<app>` directory.
2. In `compose.yaml`, change `name`, `container_name`, `hostname` (use `<app>-ts`) and `TS_HOSTNAME` (the tailnet name).
3. Point `serve.json` at `http://<backend>:8000`.
4. Generate a single-use auth key, put it in `.env`, run `docker compose up -d`, then blank the key.
5. Disable key expiry for the new machine.
6. Add it to the tailnet policy (next section).

## Tailnet policy

Managed in the admin console under **Access controls**. It allows nothing by default, and every connection needs a grant:

| Device | Can reach |
|---|---|
| `adams-s24` (phone) | the 4 app sidecars, on tcp:443 only |
| `mediapc` | `parentsthinkpad` (any port), nothing else |
| `parentsthinkpad` | nothing |
| the 4 app sidecars | nothing |

- **Tailscale SSH is off.** The policy has no `ssh` section.
- The policy has a `tests` section that checks the rules above on every save. A save that breaks them is rejected.
- `hosts` maps names to each machine's fixed `100.x` address. If a machine is removed and re-added it gets a new address, so update `hosts` too.
- **To let another device reach the apps:** add its `100.x` address to `hosts`, add `host:<name>` to the `src` of the phone's grant, and add it to the tests.

## Cloudflare (unchanged)

- Each app is still published through the Cloudflare tunnel (`cloudflare-tunnel` container, ingress `<name>.stizzle.org` → `http://<backend>:8000` over `cf-tunnel`). Each is protected by **Cloudflare Access**, logging in with Okta.
- Use it from any browser or device that isn't on the tailnet. Expect it to sometimes be slow, for the routing reason above.
- To see which data center served you, open `https://<name>.stizzle.org/cdn-cgi/trace` and look at `colo=`. `ORD` is Chicago, which is the fast case.

## App changes made on 2026-10-28

- **macro-log:**
  - The in-app access check now also accepts requests from its Tailscale sidecar. The `TAILNET_PROXY_HOST=macrolog-ts` setting turns this on.
  - A request only passes if the TCP peer really is that container *and* it carries the `Tailscale-User-Login` header that `tailscale serve` adds. The header alone isn't trusted, and neither is a sidecar name that resolves to more than one container.
  - Forwarded headers are now applied inside the app, and uvicorn runs with `--no-proxy-headers`.
  - **Note:** in production the Cloudflare Access check itself is **off** (`CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` are empty), and was left that way by choice. To turn it on, set both in `/home/dockerapps/macrolog/.env` (team domain `stizzle.cloudflareaccess.com`, AUD tag from the Access application) and redeploy. Tailscale access keeps working when it's on.
- **watchlist:**
  - Added a padded `maskable-512.png`, and the old icons are now marked `any`.
  - The old maskable-only icons had artwork reaching 93% of the way to the edge, so Android's crop clipped it once the PWA was reinstalled from the tailnet URL.
  - Background on that reinstall: Chrome normally has Google's servers build installed apps, but those servers can't reach private `ts.net` addresses, so Chrome uses a simpler install that renders icons differently.
- **hungry-hippos, reconcile:** no code changes. Both work behind `tailscale serve` as they are.

## Open items

- In Cloudflare Zero Trust, delete the `claude debug token` Service Auth policy and revoke the "app debug token for claude" service token. Both were created for the 2026-10-28 latency tests.
- Optional: update cloudflared. It's on 2026.10.3, and its logs recommend 2026.10.5.
- Optional: the VM has 2 GB of RAM shared by ~10 containers and is often near full. It wasn't the cause of the slowness, but it has little headroom.
