# Lightsail: run the full workbench and the packing engine behind one HTTPS site

Runbook for a Lightsail instance that already serves the Rust workbench behind Caddy (HTTPS, access token).
It adds the two Python services that carry the rest of the product, both on loopback only, and puts them behind
the same Caddy site. About 20 minutes. Commands assume Ubuntu 24.04, the checkout at `/opt/civil-buddy/app` with
its virtualenv at `/opt/civil-buddy/app/.venv`, and the service user `ubuntu`; adjust the paths to your instance.

> 中文摘要：服务器上现在跑的是 Rust 版工作台，它的 `/api/health` 里 `packing` 为 false（装箱引擎没接上），
> `task_routing`、`word_export`、`task_memory`、`expert_contracts`、`local_rag`、`semantic_summary` 在 Rust 版里是写死为关的
> （`workbench/src/api.rs` 的 `health()`），这些功能只在 Python 版工作台里有。
> 下面三步：① 启动 Python 装箱网关（127.0.0.1:8000）并让 Rust 工作台连上它；② 启动 Python 工作台（127.0.0.1:8765）；
> ③ 用 Caddy 对外只开 443，把主入口指向 Python 工作台。所有服务只监听本机，口令与模型密钥只放在服务器上的 0600 文件里。
> 注意：Rust 桥接不带口令，网关设了 `CIVIL_TOKEN` 后它的装箱请求会被 401（健康检查仍显示 packing=true），见第 1 节末尾。

## Why

`GET /api/health` on the current instance reports:

- `capabilities.packing = false` and `packing_agent.http.up = false`: the Python packing gateway (`gateway.app`) is not running,
  so the tender <-> packing link and `/demo` are unavailable (404).
- `task_routing`, `word_export`, `task_memory`, `expert_contracts`, `local_rag`, `semantic_summary` = false: these are fixed
  to false in the Rust workbench (`workbench/src/api.rs`, `health()`); they exist in the Python workbench (`demo/app.py`).
- `asr` (speech input) needs a speech model; leave it off on a 2 GB instance.

## 0. Secrets file (once)

One access token for every entry point, and the model settings, in a file only root and the service user can read:

```bash
sudo install -d -m 700 -o ubuntu /etc/civil-buddy
sudo -u ubuntu tee /etc/civil-buddy/env >/dev/null <<'EOF'
CIVIL_TOKEN=<the same access token the Rust workbench uses>
CIVIL_API_BASE=<the team's OpenAI-compatible LLM gateway base URL>
CIVIL_MODEL=<the model name that gateway lists>
CIVIL_API_KEY=<the team's LLM gateway key>
PYTHON_DOTENV_DISABLED=1
EOF
sudo chmod 600 /etc/civil-buddy/env
```

Never commit this file, paste it into chat, or show it in a screenshot. Rotate the token after the judging.

## 1. Packing gateway on 127.0.0.1:8000

This runbook is for an instance where no gateway runs yet. If the Docker kit of
[deploy-aws-lightsail.md](deploy-aws-lightsail.md) runs there, it already holds 127.0.0.1:8000 and ports 80/443: do not
mix the two set-ups on one instance.

The gateway needs the root `requirements.txt` (LangGraph and friends), not only the workbench's:

```bash
cd /opt/civil-buddy/app
test -x .venv/bin/python || python3 -m venv .venv          # once
.venv/bin/pip install -r requirements.txt -r demo/requirements.txt   # once
```

Try it in the foreground first:

```bash
cd /opt/civil-buddy/app
set -a; . /etc/civil-buddy/env; set +a
.venv/bin/python -m uvicorn gateway.app:app --host 127.0.0.1 --port 8000 --no-access-log
# in a second SSH window:
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/health   # expect 200
```

Then make it a service, `/etc/systemd/system/civil-gateway.service`:

```ini
[Unit]
Description=civil-buddy packing gateway (loopback only)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/civil-buddy/app
EnvironmentFile=/etc/civil-buddy/env
ExecStart=/opt/civil-buddy/app/.venv/bin/python -m uvicorn gateway.app:app --host 127.0.0.1 --port 8000 --no-access-log
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now civil-gateway
```

Connect the Rust workbench to it: add `PACKING_AGENT_URL=http://127.0.0.1:8000` next to its existing `PACKING_AGENT_ROOT`
(its service file or env file), then restart the Rust workbench service. Its `/api/health` should now show
`capabilities.packing = true` and `packing_agent.http.up = true`.

That flag only says the gateway's public `/api/health` answers. The Rust bridge (`workbench/src/packing_bridge.rs`)
sends no token, and a gateway with `CIVIL_TOKEN` set asks every caller for it, loopback included: its
`POST /api/pipeline` and `POST /api/table/parse` answer the Rust workbench with 401. A plain packing turn then falls
back to the `PACKING_AGENT_ROOT` sidecar (keep that variable set); packing an attached table has no fallback and
fails with "表解析 HTTP 401". Check with a real packing turn, not with the health flag. The Python workbench below
runs the packing engine in its own process and is not affected.

## 2. Python workbench on 127.0.0.1:8765

The typed link request reads the files it names from the job folder (`CIVIL_JOB_ROOT`), not from files uploaded
into the chat. Seed a job folder inside the checkout's `output/` (already a sandbox root) with the SYNTHETIC files
only, as the Docker kit does:

```bash
cd /opt/civil-buddy/app
sudo -u ubuntu install -d output/job
sudo -u ubuntu cp -n examples/facade-demo/facade_itt_doc.md examples/facade-demo/facade_panels.xlsx \
  examples/facade-demo/facade_panels_rev_b.xlsx output/job/
```

`/etc/systemd/system/civil-workbench-py.service`:

```ini
[Unit]
Description=civil-buddy Python workbench (loopback only)
After=network.target civil-gateway.service

[Service]
User=ubuntu
WorkingDirectory=/opt/civil-buddy/app
EnvironmentFile=/etc/civil-buddy/env
Environment=CIVIL_JOB_ROOT=/opt/civil-buddy/app/output/job
# uvicorn directly, not demo/serve.py: serve.py keeps uvicorn's access log on, which writes the one-time
# ?token= link into the journal; --no-access-log keeps it out (as the gateway above and the Docker image do)
ExecStart=/opt/civil-buddy/app/.venv/bin/python -m uvicorn app:app --app-dir demo --host 127.0.0.1 --port 8765 --no-access-log
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now civil-workbench-py
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/api/health       # expect 200
```

## 3. Caddy: one HTTPS site, 443 only

Make the Python workbench the main entry, keep the packing gateway's pages under their own paths, and keep the Rust
workbench on a second host name if you still want it. Adapt the site address to the instance's
(`<a-b-c-d>.sslip.io` works for a static IP):

```caddyfile
<a-b-c-d>.sslip.io {
	encode zstd gzip
	request_body {
		max_size 16MB
	}
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
		Referrer-Policy "no-referrer"
		-Server
	}
	# the packing gateway's one-click tender <-> packing page and its API
	# (routes checked in gateway/app.py and gateway/web_link.py: /demo, /api/tender/..., /api/tools, /api/pipeline...)
	@gateway path /demo /api/tender/* /api/tools /api/pipeline*
	reverse_proxy @gateway 127.0.0.1:8000 {
		flush_interval -1
	}
	# everything else: the Python workbench
	reverse_proxy 127.0.0.1:8765 {
		flush_interval -1
	}
	log {
		# never log the one-time ?token= link, the cookie it sets or a Bearer header
		# (the same filter as deploy/lightsail/Caddyfile, which the kit rehearsal checks)
		format filter {
			wrap json
			fields {
				request>uri query {
					delete token
				}
				request>headers>Cookie delete
				request>headers>Authorization delete
				resp_headers>Set-Cookie delete
			}
		}
	}
}

# optional second entry for the Rust workbench
rust.<a-b-c-d>.sslip.io {
	reverse_proxy 127.0.0.1:<rust-workbench-port>
}
```

```bash
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

Lightsail firewall: 80 and 443 open (80 only for the certificate challenge), 22 from your IP only; 8000 and 8765 are
never opened.

## 4. Check from outside

```bash
H=https://<a-b-c-d>.sslip.io
curl -s -o /dev/null -w "%{http_code}\n" $H/api/sessions                     # 401 without the token
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" $H/api/sessions   # 200
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" $H/demo           # 200
```

In a browser: open `$H/?token=<token>` once (the token becomes an HttpOnly cookie). The two SYNTHETIC files are already
in the job folder from step 2 (uploading them into the chat is not enough: the link reads the job folder). Send:

> Link the tender facade_itt_doc.md and the panel list facade_panels.xlsx and draft the logistics response

Expected: 5 logistics clauses, 7 statements (1 covered, 2 partial, 4 for a person), 6 x 40HQ with the type taken from
Clause 4.8; the drafted response gives the heaviest container as 6,472.8 kg gross against the 20,000 kg limit. Without
the job folder the reply is "this one names 0 document(s) and 0 table(s)". `/demo` does the same in one click.

## Notes

- Everything listens on 127.0.0.1; only Caddy faces the internet.
- Put only synthetic files on a judging instance. No partner document, no real tender.
- The model key costs the team's shared credits (Lightsail and LLM calls come from the same USD 100). Steps mode, the
  default, calls no model.
- Tear down after the event to stop charges: Lightsail console -> the instance -> Delete (take a snapshot first if you
  want to keep it).
