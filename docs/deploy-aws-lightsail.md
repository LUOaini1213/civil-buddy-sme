# Deploying Civil Buddy on AWS Lightsail

> Scope: this kit deploys the Python gateway and its tender-packing demonstration. It does not deploy the Rust unified workbench or its named-user isolation. Use one shared demo token with synthetic files; for the desktop release and per-person workspaces, see [release handoff](civil-buddy/release-handoff.md).

A copy-paste runbook for one small Lightsail instance that serves the tender ↔ packing link to the judges in a
browser: HTTPS, a random access token generated on the box, the SYNTHETIC demo files only, and no model key unless
you opt in. Two ways to do the same thing: **Path A** with the AWS CLI, **Path B** with the Lightsail console only.
Pick one; both end at the same instance. [Tear-down](#tear-down-stop-paying) is at the end.

> **Status: this procedure has not been run on Lightsail.** No Lightsail instance, static IP or Let's Encrypt
> certificate has been created for it yet. What *was* run is listed under [What was tested](#what-was-tested):
> the same launch script, operator tool, compose override and Caddy config on a local Docker host (WSL2), and the
> image's own smoke test in CI. When the team runs it on Lightsail, the URL (never the token) and the results go
> into this file.

## What gets deployed

```
browser ──HTTPS──> Caddy 2 (ports 80/443, automatic Let's Encrypt certificate for <static-ip>.sslip.io or a domain)
                     └─ reverse_proxy ──> gateway (FastAPI, 127.0.0.1:8000 only, CIVIL_TOKEN required)
                                            ├─ volume packing_output  /app/output   (SQLite, upload jobs, seeded job folder)
                                            └─ volume agent_out       /app/demo/out (what agent turns write)
```

| File | What it does |
| --- | --- |
| `docker-compose.yml` | The gateway image (`civil-buddy-gateway:0.7.0`), `CIVIL_TOKEN` required, the two volumes. |
| `deploy/lightsail/compose.override.yml` | Replaces `8000:8000` with `127.0.0.1:8000:8000`, adds `caddy:2` on 80/443, sets `CIVIL_JOB_ROOT=/app/output/job`, reads `model.env` only if it exists (it does not, unless you opt in). |
| `deploy/lightsail/Caddyfile` | `reverse_proxy gateway:8000` for `{$SITE_ADDRESS}`, 16 MB body limit, HSTS, WebSocket and server-sent events pass through, an access log with the token, cookie and `Authorization` header filtered out. |
| `deploy/lightsail/user-data.sh` | The setup script (the instance's launch script, or run once over SSH): Docker from Docker's apt repository, 2 GB swap, the repository at one pinned commit, a random token into a 0600 `.env`, `docker compose up`, the three SYNTHETIC files seeded. |
| `deploy/lightsail/civil-admin.sh` | The operator tool on the box: `show-link` (the access link, once), `rotate-token`, `set-site`, `model-on` / `model-off`, `status`. |
| `deploy/lightsail/test-local.sh` | The local rehearsal of all of the above (not a Lightsail run). |

What a signed-in visitor can do there: run the tender ↔ packing link on the demo files or on two files they upload
(`/demo`), use the bid-response page (`/`) and the packing workbench (`/workbench`). Every route except `/`,
`/workbench` and `/api/health` needs the token; without it, `/` and `/workbench` show an English page that says what
this is and how to get access.

**Synthetic data only.** This box holds `examples/facade-demo` (fictional tender, fictional panel lists, labelled
SYNTHETIC) and whatever a visitor uploads. Never upload a real customer's tender or packing list to it.

## The choices, and what they cost

| Choice | Value | Why |
| --- | --- | --- |
| Region / zone | `ap-southeast-1` (Singapore) / `ap-southeast-1a` | Closest to the judges. If the organisers' document restricts regions, use theirs and change every `ap-southeast-1` below. |
| Plan (bundle) | `small_3_0`: 2 GB RAM, 2 vCPU, 60 GB SSD, public IPv4 | The smallest plan that builds the image (the setup adds 2 GB swap); 1 GB would page during `pip install`. |
| OS (blueprint) | `ubuntu_24_04` | What the setup script was written and rehearsed for. |
| Address | a static IP, as `<a-b-c-d>.sslip.io` (e.g. `203-0-113-10.sslip.io`) | A real Let's Encrypt certificate without buying a domain. A domain works too: an A record to the static IP. |
| Firewall | 80 and 443 from anywhere, 22 from your IP only | 80 is needed for the certificate (HTTP-01) and redirects to 443. 8000 is never opened. |
| Backups | AutoSnapshot, daily | Undo for a visitor making a mess; costs cents. |
| Model key | none by default | The linked run and the demo are deterministic and call no model. See [Opt-in model key](#opt-in-model-key). |

**Budget.** The team's USD 100 is shared between AWS resources and LLM/API usage. At the list price of about
US$12 a month for `small_3_0` (about US$0.40 a day), the instance from tonight to the Finale on 10 Oct (14 days)
is about **US$6**, to 31 Oct about US$14; snapshots add cents (about US$0.05 per GB-month of stored data). The
static IP is free only while attached; Lightsail bills an instance that exists whether it runs or is stopped, so
stopping it does not stop the charge: [tear it down](#tear-down-stop-paying). These are estimates from the list
price in September 2026; check the price with the command in A1 before relying on them.

## Before you start (both paths)

1. Your AWS access, as the organisers' document describes. For Path A, the AWS CLI v2 configured for it; check:
   `aws sts get-caller-identity` prints your account, and `aws configure get region` (or `--region` on every
   command, as below).
2. **The commit to deploy**: a full 40-character SHA of a commit of this repository that contains
   `deploy/lightsail/civil-admin.sh` (`main` carries the kit since PR #2; the submitted v0.7.0 does not):

   ```bash
   REPO=https://github.com/LUOaini1213/civil-buddy-sme.git
   SHA=$(git ls-remote "$REPO" refs/heads/main | cut -f1)
   echo "$SHA"                                                   # 40 hex characters
   ```

   The setup refuses a commit without the kit, and refuses anything that is not a 40-character SHA.
3. Your own public IP, for the SSH rule: `curl -s https://checkip.amazonaws.com`.

On Windows, run the Path A commands in **Git Bash** (or WSL) from the root of a checkout of the repository.

---

## Path A: AWS CLI

Names used: instance `civil-buddy`, static IP `civil-buddy-ip`. Every command carries `--region ap-southeast-1`.

**A1. Check the plan and the OS exist in the region, and the price:**

```bash
aws lightsail get-bundles --region ap-southeast-1 \
  --query "bundles[?bundleId=='small_3_0'].[bundleId,ramSizeInGb,cpuCount,diskSizeInGb,price]" --output table
aws lightsail get-blueprints --region ap-southeast-1 \
  --query "blueprints[?blueprintId=='ubuntu_24_04'].[blueprintId,name,version]" --output table
```

Both must print one row. If `small_3_0` is missing, list the 2 GB Linux plans and use that id below:
`aws lightsail get-bundles --region ap-southeast-1 --query "bundles[?ramSizeInGb==\`2.0\` && contains(supportedPlatforms,'LINUX_UNIX')].[bundleId,price]" --output table`.

**A2. Reserve the static IP first**, so the HTTPS host name is known before the instance boots:

```bash
aws lightsail allocate-static-ip --region ap-southeast-1 --static-ip-name civil-buddy-ip
IP=$(aws lightsail get-static-ip --region ap-southeast-1 --static-ip-name civil-buddy-ip \
       --query 'staticIp.ipAddress' --output text)
SITE="${IP//./-}.sslip.io"
echo "$IP  $SITE"
```

With a domain of your own, create an A record pointing at `$IP` and set `SITE=demo.example.com` instead.

**A3. Fill in the launch script** (commit, host name, repository; nothing secret goes into it). The filled copy is
git-ignored:

```bash
sed -e "s|__CIVIL_REF__|$SHA|" -e "s|__SITE_ADDRESS__|$SITE|" -e "s|__CIVIL_REPO_URL__|$REPO|" \
  deploy/lightsail/user-data.sh > deploy/lightsail/user-data.filled.sh
grep -n '^CIVIL_REF=\|^SITE_ADDRESS=\|^REPO_URL=' deploy/lightsail/user-data.filled.sh   # three filled lines
```

**A4. Create the instance** (IPv4 only, so the firewall below is the whole story):

```bash
aws lightsail create-instances --region ap-southeast-1 \
  --instance-names civil-buddy \
  --availability-zone ap-southeast-1a \
  --blueprint-id ubuntu_24_04 \
  --bundle-id small_3_0 \
  --ip-address-type ipv4 \
  --user-data file://deploy/lightsail/user-data.filled.sh \
  --tags key=project,value=civil-buddy
```

**A5. Attach the static IP** as soon as the state is `running` (a minute or so; the setup keeps building meanwhile):

```bash
aws lightsail get-instance-state --region ap-southeast-1 --instance-name civil-buddy --query 'state.name'
aws lightsail attach-static-ip --region ap-southeast-1 --static-ip-name civil-buddy-ip --instance-name civil-buddy
```

**A6. Firewall: 80 and 443 from anywhere, 22 from your IP only.** `put-instance-public-ports` replaces every rule,
so all three go in one call; it also closes the default "22 from anywhere". `lightsail-connect` keeps the console's
browser SSH button working; drop it if you only use your own SSH client.

```bash
MYIP=$(curl -s https://checkip.amazonaws.com)
aws lightsail put-instance-public-ports --region ap-southeast-1 --instance-name civil-buddy --port-infos \
  "fromPort=80,toPort=80,protocol=tcp" \
  "fromPort=443,toPort=443,protocol=tcp" \
  "fromPort=22,toPort=22,protocol=tcp,cidrs=[${MYIP}/32],cidrListAliases=[lightsail-connect]"
aws lightsail get-instance-port-states --region ap-southeast-1 --instance-name civil-buddy \
  --query 'portStates[].[fromPort,state,join(`,`,cidrs)]' --output table     # 22 shows only your /32
```

**A7. Automatic snapshots** (daily; UTC, whole hours; 18:00 UTC is 02:00 in Singapore):

```bash
aws lightsail enable-add-on --region ap-southeast-1 --resource-name civil-buddy \
  --add-on-request 'addOnType=AutoSnapshot,autoSnapshotAddOnRequest={snapshotTimeOfDay=18:00}'
```

**A8. Wait for the setup, then check from your laptop.** The first build takes several minutes on 2 vCPU:

```bash
aws lightsail download-default-key-pair --region ap-southeast-1 --query privateKeyBase64 --output text > ~/.ssh/lightsail-sg.pem
chmod 600 ~/.ssh/lightsail-sg.pem
ssh -i ~/.ssh/lightsail-sg.pem ubuntu@$IP 'sudo cloud-init status --wait; sudo tail -n 5 /var/log/cloud-init-output.log'
curl -s "https://$SITE/api/health"                                   # 200 and a JSON body
curl -s -o /dev/null -w '%{http_code}\n' "https://$SITE/api/tools"   # 401: the token is required
```

The last setup line reads `[civil-buddy] up: https://<site>/`. If `download-default-key-pair` gives you a file that
does not start with `-----BEGIN`, use the console's browser SSH instead (Path B, B6) for A8 and A9. If HTTPS is not
there yet, Caddy retries on its own once the static IP is attached and port 80 is open.

**A9. The access link, shown once, into your own terminal:**

```bash
ssh -i ~/.ssh/lightsail-sg.pem ubuntu@$IP 'sudo bash /opt/civil-buddy/deploy/lightsail/civil-admin.sh show-link'
```

Continue at [After either path](#after-either-path).

---

## Path B: Lightsail console only

Everything in a browser: the Lightsail console at `https://lightsail.aws.amazon.com/` and its browser SSH. No
launch script is pasted (a pasted script can pick up Windows line endings); the setup runs once over browser SSH.

**B1. Create the instance.** *Create instance* →
- Instance location: **Singapore (ap-southeast-1)**, zone **A**.
- Platform **Linux/Unix**, blueprint **OS Only → Ubuntu 24.04 LTS**.
- Leave *Add launch script* empty.
- Networking type: **IPv4** only if the console offers it (otherwise dual-stack, and turn IPv6 off in B3).
- Plan: the **2 GB RAM, 2 vCPU, 60 GB SSD** plan (about US$12 a month).
- Name: `civil-buddy`. *Create instance*.

**B2. Static IP.** *Networking* → *Create static IP* → Singapore → attach to `civil-buddy` → name `civil-buddy-ip` →
*Create*. Note the address, e.g. `203.0.113.10`; the site is `203-0-113-10.sslip.io`.

**B3. Firewall.** Instance `civil-buddy` → *Networking* → *IPv4 Firewall*:
- the `SSH` rule: *Edit* → tick *Restrict to IP address* → your IP from "Before you start" → keep
  *Allow Lightsail browser SSH* ticked → *Save*;
- keep `HTTP` 80 (any IP);
- *Add rule* → `HTTPS` 443 (any IP) → *Create*.
- No rule for 8000. If there is an *IPv6 networking* toggle and it is on, turn it off (or give its firewall the
  same three rules).

**B4. Automatic snapshots.** Instance → *Snapshots* → *Automatic snapshots* → *Enable*, pick a night hour.

**B5. Choose the commit.** On your laptop, run the `git ls-remote` line from "Before you start" (or read the SHA of
`main` on GitHub). You will type it in B6.

**B6. Run the setup once, over browser SSH.** Instance → *Connect* → *Connect using SSH*. In that terminal, set the
commit, then paste the rest as it is (it downloads the setup script at that exact commit and runs it in the
background, so a closed browser tab does not stop it):

```bash
SHA=<the 40-character commit>
REPO=https://github.com/LUOaini1213/civil-buddy-sme.git
SITE="$(curl -s https://checkip.amazonaws.com | tr . -).sslip.io"; echo "$SITE"   # must be the static IP, dashed
curl -fsSL "https://raw.githubusercontent.com/LUOaini1213/civil-buddy-sme/$SHA/deploy/lightsail/user-data.sh" -o /tmp/user-data.sh
sudo nohup env CIVIL_REF="$SHA" CIVIL_REPO_URL="$REPO" SITE_ADDRESS="$SITE" bash /tmp/user-data.sh > ~/civil-setup.log 2>&1 &
tail -f ~/civil-setup.log          # Ctrl+C stops watching, not the setup; wait for "[civil-buddy] up: https://..."
```

If the `echo` in the third line does not print the static IP from B2, the IP was not attached yet: finish B2 and run
the three lines again before the `nohup` line. The log never contains the token.

**B7. The access link, shown once**, in the same browser SSH terminal:

```bash
sudo bash /opt/civil-buddy/deploy/lightsail/civil-admin.sh show-link
```

---

## After either path

**The access link.** `show-link` prints `https://<site>/?token=<64 hex>` once and records that it did (a hash
prefix of the token, never the token). A second `show-link` refuses and prints nothing. Lost the link? Run
`rotate-token`: a new token, the gateway restarted, every earlier link and cookie gets 401, the new link printed
once. (Root can still read `/opt/civil-buddy/.env`; "once" means the tool does not repeat it.)

Opening the link once sets an HttpOnly, SameSite=Strict, Secure cookie for 30 days and redirects to the same page
without the token. Then `https://<site>/demo` runs the linked demo in one click.

**Check it as a judge would**, in a private browser window:
1. `https://<site>/` without the link: the English "This server is private" page. `/api/tools`: 401.
2. The link: the bid-response page. Then `/demo` → *Run the linked demo*: revision A 6 × 40HQ, revision B
   8 × 40HQ, the statements S2, S3, S6 and S7 marked stale.

**The operator tool** (all over SSH, `sudo bash /opt/civil-buddy/deploy/lightsail/civil-admin.sh <command>`):

| Command | What it does |
| --- | --- |
| `show-link` | The access link, once per token. |
| `rotate-token` | New token; old links and cookies get 401; the new link printed once. |
| `set-site [host]` | The HTTPS host Caddy serves. Without `host`: this instance's public IP as `<a-b-c-d>.sslip.io` (after a new static IP); with a domain: `set-site demo.example.com`. |
| `model-on` / `model-off` | The opt-in model key, below. |
| `status` | Containers, the site, whether the link was shown, whether a model key is set (never the value). |

### Token handover and blast radius

- The link with the token goes **only into the organisers' submission form**. Never into the public repository,
  this file, the video, a slide, a chat channel or an email thread. This file may record the site URL, not the link.
- **Rotate after 10 Oct** (the Finale), and whenever it may have leaked: `civil-admin.sh rotate-token`.
- What someone with the token can do: everything a user can (write sessions, upload files, delete checkpoints,
  run evaluations). One token for all routes and no rate limit in front of it. That is why the box holds
  **synthetic data only and no model key by default**: the worst case is a mess that one snapshot restore undoes.
- Bounded by design: uploads at most 10 MB + 5 MB (Caddy refuses bodies over 16 MB), at most 2 linked runs at
  once, 10 upload jobs kept per session. The linked run never approves anything: its record stays
  `submit_blocked = true`, `confirmed_by_person = false`; only a person's typed sentence approves high-risk work,
  for one turn, and no model, MCP call or flag can.
- Logs: uvicorn's access log is off (it printed the `?token=` link); Caddy's access log filters the token query
  parameter, the cookie and the `Authorization` header; the setup log and `civil-admin.sh` output carry no token
  except the one `show-link` line.

### Opt-in model key

Leave it off for the judges unless a demo needs a model. With a key on the box, anyone holding the access token can
make the gateway call the model, and every call spends the team's shared USD 100. The linked run and `/demo` never
call a model either way.

```bash
sudo bash /opt/civil-buddy/deploy/lightsail/civil-admin.sh model-on
#   model endpoint (OpenAI-compatible base URL): the organisers' API URL
#   model name:                                  the model they name
#   API key (hidden, not echoed):                typed or pasted; nothing shows
sudo bash /opt/civil-buddy/deploy/lightsail/civil-admin.sh model-off    # afterwards
```

`model-on` writes `/opt/civil-buddy/model.env` (mode 0600, root) with `CIVIL_API_BASE`, `CIVIL_MODEL` and
`CIVIL_API_KEY`, and restarts the gateway, which reads it through `env_file` (`required: false`). It never prints
the key; the key is not in the repository, the image (`.dockerignore`) or any log. Only an OpenAI-compatible
Chat Completions endpoint works; the organisers' endpoint has not been tried with it.

## Updating to a newer commit

```bash
cd /opt/civil-buddy && sudo git fetch -q origin && sudo git checkout -q --detach <new 40-character sha>
sudo docker compose -f docker-compose.yml -f deploy/lightsail/compose.override.yml --env-file .env up -d --build
```

The token, the site, the database, the upload jobs and what agent turns wrote survive this (they are in `.env` and
the volumes); only `docker compose down -v` deletes the volumes.

## Tear-down: stop paying

Lightsail bills the instance while it exists, running or stopped, and a static IP while it is not attached. When
the judging is over (or tonight, if this was only a rehearsal), delete everything. **This cannot be undone**; the
box holds synthetic data only, so nothing is lost that the repository cannot rebuild.

Path A (CLI):

```bash
aws lightsail delete-instance --region ap-southeast-1 --instance-name civil-buddy --force-delete-add-ons
aws lightsail release-static-ip --region ap-southeast-1 --static-ip-name civil-buddy-ip
aws lightsail get-instance-snapshots --region ap-southeast-1 --query 'instanceSnapshots[].name' --output text
#   delete each name it prints:
#   aws lightsail delete-instance-snapshot --region ap-southeast-1 --instance-snapshot-name <name>
# check that nothing is left (each prints nothing):
aws lightsail get-instances --region ap-southeast-1 --query 'instances[].name' --output text
aws lightsail get-static-ips --region ap-southeast-1 --query 'staticIps[].name' --output text
aws lightsail get-instance-snapshots --region ap-southeast-1 --query 'instanceSnapshots[].name' --output text
```

Path B (console): instance `civil-buddy` → ⋮ → *Delete* → confirm; *Networking* → `civil-buddy-ip` → *Delete*;
*Snapshots* (top level) → delete any snapshot of `civil-buddy`; then the *Instances*, *Networking* and *Snapshots*
tabs list nothing of it. Also delete the downloaded `~/.ssh/lightsail-sg.pem` and
`deploy/lightsail/user-data.filled.sh` on your laptop if you made them.

## What was tested

Nothing below ran on Lightsail. It ran on a local Docker host, WSL2 on the team's Windows laptop, and in CI.

- **The setup script, the operator tool, the compose override and Caddy**: `deploy/lightsail/test-local.sh
  <commit>` runs `user-data.sh` with Docker already installed (`CIVIL_SKIP_DOCKER_INSTALL=1`) against a local clone
  at the commit, then checks through Caddy. Run on 26 Sep 2026 at commit `3afb036` (Docker Engine 29.1.3 in WSL2,
  Compose 2.29.7, `caddy:2`): **18 of 18 checks passed**, in 1,826 s, of which 1,177 s were the uncached image build
  and the clone on a busy laptop. The checks: `.env` mode 600 with a 64-hex token; the token not in the setup
  output; the gateway published on `127.0.0.1` only; through Caddy `/api/health` 200, `/api/tools` 401 without /
  401 wrong / 200 with the token; `/` without the token shows the English access page; `?token=` gives 303 and an
  HttpOnly cookie; `/demo` with the cookie; the demo route (rev A 6 × 40HQ, rev B 8 × 40HQ, stale S2, S3, S6, S7);
  a multipart upload 200; a 20 MB body 413; the WebSocket subscribed with the cookie and refused without; the job
  folder seeded with the three SYNTHETIC files only; the typed agent request linked on them and its record
  survived a gateway re-create; the token in no container log (Caddy access log on). Then with Caddy's internal CA
  (`SITE_ADDRESS=localhost`): HTTPS 401/200, a `Secure` cookie, HSTS, HTTP → 308, `wss://` subscribed.
  Run again on 27 Sep 2026 at commit `6e10b2d` (same host, image layers cached): **21 of 21 checks passed** in
  1,084 s (setup and build 211 s; the demo route 4.7 s). The three new checks cover `civil-admin.sh`: `show-link`
  printed exactly `http://localhost/?token=<token>` once, and a second call refused without printing it; `model-on`
  with a dummy key wrote `model.env` mode 600, the key reached the gateway's environment only after it, was in no
  command output and no container log, and `model-off` removed it; `rotate-token` left the old token and the old
  cookie at 401 and the new one at 200, `.env` still mode 600. `set-site localhost` then switched Caddy to its
  internal CA without recreating the gateway container. The request shapes of every `aws lightsail` call above
  (parameters, the `PortInfo` fields incl. `cidrListAliases`, `ipAddressType=ipv4`, the AutoSnapshot add-on, and
  the fields the `--query` expressions read) were checked against the Lightsail service model in botocore
  1.34.69; no AWS call was made.
  CI runs the same rehearsal in the `docker-smoke` job.
- **The image**: `scripts/docker_smoke.sh` (CI job `docker-smoke` on every PR): the image carries no `demo/.env`,
  `demo/out` or `demo/data`; it refuses to start without `CIVIL_TOKEN`; 401/200 on the token; the upload route and
  the demo route give S1–S7, 6 × 40HQ, and after rev B the stale statements S2, S3, S6, S7; the typed agent request
  writes its link record under `/app/demo/out`, and that record and the database survive a container re-create.
- **Static checks**: `scripts/test_deploy_config.py` (in `npm run check`): the override binds the gateway to
  127.0.0.1 with `!override` and reads `model.env` only if present; the Caddyfile filters the token from its log;
  the setup script never prints the token, seeds only the three SYNTHETIC files and refuses a commit without the
  kit; `civil-admin.sh` prints the token in one line, once, reads the model key hidden into a 0600 file and never
  echoes it; `.gitignore` and `.dockerignore` keep `model.env` out; this runbook names both paths and the tear-down.
- `shellcheck` 0.11.0 (koalaman/shellcheck:stable) reports nothing on `user-data.sh`, `civil-admin.sh`, `test-local.sh`
  and `scripts/docker_smoke.sh`.

Not tested anywhere yet: the Docker installation step of `user-data.sh` (the rehearsal skips it), a Let's Encrypt
certificate for an `sslip.io` name, every `aws lightsail` command and console step above (no AWS call was made),
`download-default-key-pair`, the organisers' model endpoint, and a build on a 2 GB instance. The setup is plain
bash rather than `#cloud-config`, because a Lightsail launch script is a shell script; `cloud-init schema`
(cloud-init 25.2 on Ubuntu 24.04) recognises it as `text/x-shellscript` and does not evaluate that type, so it was
checked with `bash -n`, `shellcheck` and by running it as above.
