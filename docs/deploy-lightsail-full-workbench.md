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
CIVIL_API_BASE=https://api.softwaresystems.app/v1
CIVIL_MODEL=global.anthropic.claude-sonnet-4-5-20250929-v1:0
CIVIL_API_KEY=<the team's LLM gateway key>
PYTHON_DOTENV_DISABLED=1
EOF
sudo chmod 600 /etc/civil-buddy/env
```

Never commit this file, paste it into chat, or show it in a screenshot. Rotate the token after the judging.

## 1. Packing gateway on 127.0.0.1:8000

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

## 2. Python workbench on 127.0.0.1:8765

```bash
cd /opt/civil-buddy/app
.venv/bin/pip install -r demo/requirements.txt       # once
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
Environment=CIVIL_HOST=127.0.0.1
Environment=CIVIL_PORT=8765
Environment=PACKING_AGENT_URL=http://127.0.0.1:8000
ExecStart=/opt/civil-buddy/app/.venv/bin/python demo/serve.py
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
		# never log the one-time ?token= link or the Authorization header
		format filter {
			request>uri query {
				delete token
			}
			request>headers>Authorization delete
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

In a browser: open `$H/?token=<token>` once (the token becomes an HttpOnly cookie). Upload the two SYNTHETIC files from
`examples/facade-demo/` (`facade_itt_doc.md`, `facade_panels.xlsx`) and send:

> Link the tender facade_itt_doc.md and the panel list facade_panels.xlsx and draft the logistics response

Expected: 5 logistics clauses, 7 statements (1 covered, 2 partial, 4 for a person), 6 x 40HQ with the type taken from
Clause 4.8, heaviest container 6,472.8 kg gross against the 20,000 kg limit. `/demo` does the same in one click.

## Notes

- Everything listens on 127.0.0.1; only Caddy faces the internet.
- Put only synthetic files on a judging instance. No partner document, no real tender.
- The model key costs the team's shared credits (Lightsail and LLM calls come from the same USD 100). Steps mode, the
  default, calls no model.
- Tear down after the event to stop charges: Lightsail console -> the instance -> Delete (take a snapshot first if you
  want to keep it).
