# Deploying to GCP

How Software Factory runs on `gcp-vm-rebee` (GCP project `fine-transit-497214-q7`,
zone `us-central1-f`), behind Cloudflare:

- `https://app.buildmill.dev` → web app (port 3050)
- `https://api.buildmill.dev` → API (port 3060)
- `https://buildmill.dev` → public marketing site (port 3040)

> **Superseded hostnames.** `factory.nexdb.cloud` and `factory-api.nexdb.cloud` were the
> original names and **no longer resolve** — following them produces
> `Name or service not known`. They cost a debugging cycle on 2026-07-26 (US-27.13);
> anywhere you still see them outside this file, they are stale.
>
> The install directory was `/opt/factory.nexdb.cloud` until 2026-07-29, when it was
> renamed on the VM to `/opt/buildmill.dev`; the deploy workflow writes there now. If you
> see the old path in a unit file or script on the box, it is stale.

This mirrors the existing `nexdb-web`/`nexdb-api` deployment already on this VM — same
`/opt/<appname>` layout, same systemd-service pattern, same Cloudflare Tunnel.

## Architecture recap

Per [ARCHITECTURE.md](ARCHITECTURE.md): the browser talks to Supabase directly for CRUD
(auth, RLS, Realtime) and only calls the FastAPI `api` for orchestration (dispatch,
runner callbacks, GitHub App operations). Neither service touches a local database on the
GCP box — Supabase is fully hosted, so the VM is stateless app-wise.

The **runner** (invokes the Claude Code CLI against real repos) intentionally does **not**
run on this cloud box — it runs on the operator's own machine, per the trust boundary in
ARCHITECTURE.md. Don't deploy `apps/runner` to GCP.

## How ingress actually works

This VM does **not** expose a public nginx/Apache reverse proxy for these apps. Apache is
running on port 80 but is unrelated to `factory.*` / `nexdb.*` — leave it alone.

Instead, a **Cloudflare Tunnel** (`cloudflared`, running as a systemd service, token-based)
makes outbound-only connections to Cloudflare's edge. Public hostname → local port mappings
are configured in the **Cloudflare Zero Trust dashboard** (Networks → Tunnels → this
tunnel → Public Hostname), not in any file on the VM:

- `app.buildmill.dev` → `http://localhost:3050`
- `api.buildmill.dev` → `http://localhost:3060`
- `buildmill.dev` → `http://localhost:3040`   (public marketing site)
- `www.buildmill.dev` → `http://localhost:3040`

Changing these requires the Cloudflare dashboard — there's nothing to edit on the VM for
routing. Port 22 (SSH) and 80/443 (Apache, default site) are the only things actually
listening on the public interface; 3050/3060 are `127.0.0.1`-reachable via the tunnel only.

## Directory & process layout

```
/opt/buildmill.dev/                # owned by the `deploy` user
├── apps/web/                      # Next.js — built here, run via systemd
│   ├── .env.local                 # NOT in git; created once, never overwritten by deploys
│   └── .next/                     # build output
└── apps/api/
    ├── .env                       # NOT in git; created once
    └── .venv/                     # Python 3.13 venv
```

Two systemd units (`/etc/systemd/system/factory-web.service`,
`factory-api.service`), running as the `deploy` user:

- `factory-web` — `npm run start` (Next.js `next start`), `PORT=3050`
- `factory-api` — `uvicorn app.main:app --host 127.0.0.1 --port 3060`, reads
  `EnvironmentFile=/opt/buildmill.dev/apps/api/.env`

```bash
sudo systemctl status factory-web factory-api
sudo journalctl -u factory-web -f      # logs
sudo systemctl restart factory-web factory-api
```

The `deploy` user has passwordless sudo scoped to exactly those service restarts
(`/etc/sudoers.d/factory-deploy`) — nothing broader. When you add `factory-public`
(below), widen that sudoers entry to include it, e.g.:

```
deploy ALL=(root) NOPASSWD: /bin/systemctl restart factory-web factory-api factory-public
```

## Public marketing site — `buildmill.dev` (port 3040)

`apps/public` is a **static single page** (`index.html` + `/assets`, the Build Mill
branding) served by a tiny zero-dependency Node server (`apps/public/server.js`). It just
redirects visitors to the app: the **Sign up** / **Log in** buttons point at
`https://app.buildmill.dev`. There is **no build step** and it touches no database or
secrets — `npm run build` deliberately does not include it.

It runs as a third systemd unit on this same VM, fronted by the same Cloudflare Tunnel.
One-time setup on the box:

```bash
sudo tee /etc/systemd/system/factory-public.service >/dev/null <<'UNIT'
[Unit]
Description=Build Mill public marketing site (static, port 3040)
After=network.target

[Service]
Type=simple
User=deploy
WorkingDirectory=/opt/buildmill.dev/apps/public
Environment=PORT=3040
Environment=HOST=127.0.0.1
ExecStart=/usr/bin/node server.js
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now factory-public
sudo systemctl status factory-public --no-pager
curl -I http://127.0.0.1:3040
```

Then, in the **Cloudflare Zero Trust dashboard** (Networks → Tunnels → this tunnel →
Public Hostname), add two mappings — `buildmill.dev` and `www.buildmill.dev` — both →
`http://localhost:3040`.

After that first setup, deploys are automatic: the CI job rsyncs `apps/public` and
restarts `factory-public` like the other two services (it needs no `npm install`/build).

### Env vars

`apps/web/.env.local` (NEXT_PUBLIC_* are inlined at *build* time, so this must exist
before `npm run build` runs):
```
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
NEXT_PUBLIC_API_URL=https://api.buildmill.dev
```

`apps/api/.env` (minimum required — see `apps/api/app/config.py` for the full list):
```
SUPABASE_URL=...
SUPABASE_PUBLISHABLE_KEY=...
CORS_ORIGINS=https://app.buildmill.dev
WEB_BASE_URL=https://app.buildmill.dev
# US-27.13: the address AGENT MACHINES dial. It is written into every agent
# slot's env file as FACTORY_API_URL, so it must be reachable from those
# machines — the default (http://localhost:8000) silently produces agents that
# can never connect, and registering an agent server is now refused while it
# is unset or loopback.
API_BASE_URL=https://api.buildmill.dev
```

Values live in the local (git-ignored) `supabase.txt` / Supabase dashboard. These two
files are created once directly on the VM and are excluded from every deploy sync — they
never travel through git or CI.

### Swap

The VM (`e2-small`, 2GB RAM) had no swap and often runs at <1GB free alongside the nexdb
services. A 2GB swapfile (`/swapfile`) was added so `next build` doesn't risk the OOM
killer taking down another app's process. Check with `free -h`.

## CI/CD — auto-deploy on push to `prod`

[.github/workflows/deploy-prod.yml](.github/workflows/deploy-prod.yml) deploys
automatically whenever commits land on the `prod` branch (i.e. when a PR is merged into
`prod`, or on manual `workflow_dispatch`). It:

1. Checks out the repo on the Actions runner.
2. `rsync`s the source to `/opt/buildmill.dev` over SSH, excluding
   `node_modules`, `.next`, `apps/api/.venv`, and both `.env`/`.env.local` files (so the
   VM's real secrets are never touched or deleted by a deploy).
3. SSHes in and runs `npm install && npm run build`, `pip install -r requirements.txt`
   for the API, then `sudo systemctl restart factory-web factory-api factory-public`
   (the static `apps/public` site needs no build — the restart just picks up any change).
4. Curls all three services locally on the VM as a smoke test; fails the job if any is down.

**UAT mirror.** [.github/workflows/deploy-uat.yml](.github/workflows/deploy-uat.yml) does
the same on pushes to the `uat` branch: same VM and secrets, rsyncing to
`/opt/uat.buildmill.dev` and restarting `factory-web-uat` (:3051) / `factory-api-uat`
(:3061). There is no UAT copy of `apps/public`. The `deploy` user's sudoers entry covers
the `-uat` service restarts too. Each `uat` push also refreshes a rolling "UAT \<date\>"
prerelease (tag `uat-latest`) in GitHub Releases via `uat-release.yml`.

**Secrets** (repo → Settings → Secrets and variables → Actions, already set):
- `GCP_VM_SSH_KEY` — private half of a dedicated ed25519 keypair generated for CI. The
  public half is appended to `deploy`'s `~/.ssh/authorized_keys` on the VM (alongside the
  VM's own pre-existing `github-actions` key used by the nexdb pipeline).
- `GCP_VM_HOST` — `136.116.81.238` (the VM's external IP is ephemeral: it changed on
  2026-07-29 and the deploy failed at `ssh-keyscan` until this secret was updated. If a
  deploy dies in "Set up SSH" after ~15s, check the current IP first.)
- `GCP_VM_USER` — `deploy`

**To deploy:** merge (or push) a PR into `prod`. To trigger manually: Actions tab →
"Deploy to Production" → Run workflow.

## Manual deploy (fallback, if you ever need to bypass CI)

```bash
# from a local checkout, gcloud CLI authenticated + gcp-vm-rebee reachable
tar --exclude='node_modules' --exclude='.git' --exclude='.next' \
    --exclude='apps/api/.venv' --exclude='supabase.txt' \
    --exclude='.env.local' --exclude='.env' -czf /tmp/factory-deploy.tar.gz .
gcloud compute scp /tmp/factory-deploy.tar.gz gcp-vm-rebee:/tmp/ --zone=us-central1-f
gcloud compute ssh gcp-vm-rebee --zone=us-central1-f --command="
  sudo tar -xzf /tmp/factory-deploy.tar.gz -C /opt/buildmill.dev &&
  sudo chown -R deploy:deploy /opt/buildmill.dev &&
  cd /opt/buildmill.dev && sudo -u deploy npm install && sudo -u deploy npm run build &&
  sudo systemctl restart factory-web factory-api"
```

## Verifying

```bash
gcloud compute ssh gcp-vm-rebee --zone=us-central1-f --command="curl -I http://127.0.0.1:3050; curl -I http://127.0.0.1:3060/docs"
```

Then check `https://app.buildmill.dev` and `https://api.buildmill.dev/docs`
externally. `sudo journalctl -u factory-web -u factory-api -f` for live logs.
