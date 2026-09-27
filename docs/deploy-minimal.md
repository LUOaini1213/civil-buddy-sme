# Deploying Civil Buddy: the minimal routes

> Scope: this kit deploys the Python gateway and its tender-packing demonstration. It does not deploy the Rust unified workbench or its named-user isolation. Use one shared demo token with synthetic files; for the desktop release and per-person workspaces, see [release handoff](civil-buddy/release-handoff.md).

Goal: a fixed HTTPS address for the gateway (the browser pages and the API), with the access token required on
every route except `/`, `/workbench` and `/api/health`.

What every route below has in common:

- **`CIVIL_TOKEN` is required.** Without it the container refuses to start on 0.0.0.0, and `docker compose`
  refuses to start at all. Keep it in the host's environment or a 0600 `.env`, never in the repository.
- **No model key is needed.** The linked tender ↔ packing run, the demo page and the packing engine are
  deterministic (`steps` mode). Add a key only if you want the model mode, and never on a demo server.
- **No Java service is needed.** 3D packing uses the Python solver (`PACKING_SKIP_SKJOLBER=1` by default).
- The image starts only the gateway. Data lives in two volumes: `packing_output` at `/app/output` (SQLite, upload
  jobs) and `agent_out` at `/app/demo/out` (what agent turns write). `docker compose down` and `up --build` keep
  them; only `docker compose down -v` deletes them.
- Whole-image check: `docker build -t civil-buddy-gateway:smoke . && bash scripts/docker_smoke.sh` (the CI job
  `docker-smoke` runs it on every PR).

---

## Route A · AWS Lightsail (recommended)

One Lightsail Linux instance per company, used in a browser. The full procedure, with the exact `aws lightsail`
commands, the launch script, Caddy with automatic HTTPS, token handover and cost, is in
**[deploy-aws-lightsail.md](deploy-aws-lightsail.md)**. That procedure **has not been run on Lightsail yet**; its
local rehearsal (`deploy/lightsail/test-local.sh`) and the image's smoke test have.

In short: reserve a static IP, launch Ubuntu 24.04 (`small_3_0`, 2 GB) with `deploy/lightsail/user-data.sh` as the
launch script, attach the IP, open 80/443 (22 from your IP only), enable automatic snapshots, read the token over
SSH. The gateway is on 127.0.0.1:8000 behind Caddy; port 8000 is never opened.

---

## Route B · Any Linux host with Docker (2 vCPU / 2 GB is enough for a demo)

```bash
# 1. Docker (Docker's own packages; see https://docs.docker.com/engine/install/)
# 2. The code at one commit
git clone https://github.com/LUOaini1213/civil-buddy-sme.git civil-buddy && cd civil-buddy
git checkout --detach <commit>

# 3. The access token (required) and, behind the proxy, the site address
( umask 077; printf 'CIVIL_TOKEN=%s\nSITE_ADDRESS=%s\n' "$(openssl rand -hex 32)" "<your host name>" > .env )

# 4. With HTTPS in front (Caddy, gateway on 127.0.0.1 only; the same files as the Lightsail route)
docker compose -f docker-compose.yml -f deploy/lightsail/compose.override.yml --env-file .env up -d --build
#    or, on a laptop only, without a proxy:  docker compose up -d --build   -> http://localhost:8000

# 5. Self-check (scripts use a Bearer header; /api/health is the public liveness probe)
curl -s http://127.0.0.1:8000/api/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/tools                                # 401
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $(sed -n 's/^CIVIL_TOKEN=//p' .env)" \
  http://127.0.0.1:8000/api/tools                                                                      # 200
```

Then open `https://<host>/?token=<token>` once (it sets an HttpOnly cookie and redirects without the token) and
`https://<host>/demo`.

### Rules for a server that others reach

1. **Only through HTTPS.** Publish the gateway on `127.0.0.1:8000` (the override does this) and open only 80/443
   in the host firewall; never 8000.
2. **Caddy is in the repository** (`deploy/lightsail/Caddyfile`). With nginx instead, the proxy needs:
   ```nginx
   location / {
       proxy_pass http://127.0.0.1:8000;
       proxy_http_version 1.1;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_set_header Upgrade $http_upgrade;      # /ws/
       proxy_set_header Connection "upgrade";
       client_max_body_size 16m;                    # the upload route takes 10 MB + 5 MB
   }
   ```
   A request with forwarding headers, or over HTTP/1.0, is treated as remote, and the token is required from
   loopback too, so no proxy setting bypasses it. `X-Forwarded-Proto: https` makes the cookie `Secure`. Do not set
   uvicorn's `FORWARDED_ALLOW_IPS` to `*`. Keep the token out of proxy access logs (the Caddyfile filters it).
3. **The `?token=` link** appears once in the browser history; do not forward it. Scripts use
   `Authorization: Bearer <token>`. Changing the token = edit `.env` and `up -d`; old cookies get 401 and are cleared.
   Never set `CIVIL_ALLOW_OPEN_LAN`.
4. **Secrets only in the environment.** No key files in the repository root or under `output/`
   (`/api/artifact` reads only `output/`, `PACKING_OUTPUT_DIR` and the runs folder). `.dockerignore` keeps `.env`
   files, `demo/out`, `demo/data` and `output/` out of an image built from a working copy.
5. Leave `PACKING_TMS_MODE` unset (stub); a request body cannot switch it to a live TMS.
6. The container starts only the gateway. To add the staff workbench as well:
   `CIVIL_HOST=127.0.0.1 CIVIL_TOKEN=<same token> python demo/serve.py`, reached only through the same-host proxy.
   Both apps must use the same token (the cookie has one name for both ports).
7. Mount the app at the domain root (`location /`); under a sub-path the `?token=` redirect goes to the root.
8. Without a token, an HTTP/1.1 proxy that strips forwarding headers and rewrites Host to 127.0.0.1, or any TCP
   port forward (socat, netsh portproxy, `ssh -R`), makes remote requests look local: never expose an instance
   without a token that way.

---

## Route C · Railway

1. [railway.app](https://railway.app) → New Project → Deploy from GitHub → this repository (the Dockerfile is found).
2. Variables: `CIVIL_TOKEN` (required). No model key.
3. Generate Domain → `https://<name>.up.railway.app`, HTTPS by the platform.

The upload jobs and the database live in the container's filesystem unless you attach a volume at `/app/output`
(and one at `/app/demo/out`).

---

## Route D · Render.com (last resort)

1. [render.com](https://render.com) → New → Web Service → this repository, branch `main`, Runtime **Docker**,
   Region Singapore.
2. Environment: `CIVIL_TOKEN` (required). No model key.
3. Address: `https://<service>.onrender.com/?token=<token>`; health: `/api/health`.

The free tier sleeps when idle (30–60 s cold start) and has no persistent disk: sessions and upload jobs are lost
on every restart. Use it only if no AWS or Linux host is available.

---

## Acceptance checklist (any route)

- [ ] `GET /api/health` → 200, `gateway: UP`
- [ ] `GET /api/tools` without the token → 401; with `Authorization: Bearer <token>` → 200
- [ ] `/` without the token shows the English page "This server is private", not an error
- [ ] `/?token=<token>` → redirect and cookie; `/demo` → **Run the linked demo** → rev A 6 × 40HQ, rev B 8 × 40HQ,
      stale statements S2, S3, S6, S7
- [ ] Upload `examples/facade-demo/facade_itt_doc.md` + `facade_panels.xlsx` on `/demo` → S1–S7 with sha256s
- [ ] Reachable from a phone on mobile data (not the office LAN)

## Running it locally instead

```bash
pip install -r requirements.txt
python scripts/demo_facade.py                                   # the linked run on the SYNTHETIC files, no server
uvicorn gateway.app:app --host 127.0.0.1 --port 8000            # the gateway on this machine only
```

On a server it is the same `gateway.app:app`, with `--host 0.0.0.0`, a public address and `CIVIL_TOKEN`
(binding 0.0.0.0 without the token refuses to start).
