#!/usr/bin/env python3
"""The deployment files say what the Lightsail guide promises (static checks; no Docker, no network).

  compose     image 0.7.0, CIVIL_TOKEN required, the data volume and the agent-output volume (/app/demo/out), no
              model key and no CIVIL_WORKTREE_ROOT passed in
  override    the gateway published on 127.0.0.1 only (!override, so the base 8000:8000 is replaced, not added to),
              Caddy 2 in front on 80/443, the job folder inside /app/output (already a sandbox root)
  caddy       reverse_proxy to the gateway, a body limit above the upload route's, HSTS, and an access log with the
              token, the cookie and the Authorization header filtered out; no `tls internal` on the real site
  user-data   bash with set -euo pipefail and no xtrace, a pinned 40-hex commit, the token from openssl into a 0600
              .env and never printed, only the SYNTHETIC facade files seeded, no model key, the kit present at the commit
  admin       civil-admin.sh prints the token in one line only (show_link), once per token (a marker with a
              fingerprint, not the token), rotates it with openssl, reads a model key hidden (read -s) into a 0600
              model.env and never echoes it; the override reads model.env only if it exists (required: false)
  image       uvicorn without its access log (it would print the ?token= link); .dockerignore keeps demo/.env,
              demo/out, demo/data, .civil-buddy and output out of the build context at any depth
  docs        docs/deploy-aws-lightsail.md in English with the exact aws lightsail commands and "not run on
              Lightsail"; docs/deploy-minimal.md in English with AWS first and Render last
Run by scripts/check_project.py (deploy-config). The live rehearsal is deploy/lightsail/test-local.sh (Docker).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LS = ROOT / "deploy" / "lightsail"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def cjk(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


def yaml_load(text: str):
    try:
        import yaml
    except ImportError:          # PyYAML is not a runtime requirement; the text checks below still run
        return None

    class Loader(yaml.SafeLoader):
        pass

    def tagged(loader, suffix, node):
        value = loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode) else (
            loader.construct_mapping(node) if isinstance(node, yaml.MappingNode) else loader.construct_scalar(node))
        return {"__tag__": suffix, "value": value}

    Loader.add_multi_constructor("!", tagged)
    return yaml.load(text, Loader=Loader)


class ComposeTests(unittest.TestCase):
    def test_base_compose(self) -> None:
        text = read(ROOT / "docker-compose.yml")
        self.assertIn("image: civil-buddy-gateway:0.7.0", text)
        self.assertRegex(text, r"CIVIL_TOKEN=\$\{CIVIL_TOKEN:\?")
        self.assertIn("packing_output:/app/output", text)
        self.assertIn("agent_out:/app/demo/out", text)
        self.assertNotRegex(text, r"(?m)^\s*-\s*[A-Z_]*(API_KEY|SECRET)[A-Z_]*=")
        self.assertNotRegex(text, r"(?m)^\s*-\s*CIVIL_WORKTREE_ROOT")
        data = yaml_load(text)
        if data is not None:
            self.assertEqual({"packing_output", "agent_out"}, set(data["volumes"]))

    def test_override_binds_the_gateway_to_loopback_behind_caddy(self) -> None:
        text = read(LS / "compose.override.yml")
        self.assertNotIn("CIVIL_WORKTREE_ROOT", text)
        self.assertNotRegex(text, r"(API_KEY|OPENAI|DEEPSEEK|GOOGLE_API|BEDROCK|AWS_SECRET)")
        data = yaml_load(text)
        if data is None:
            self.assertIn("ports: !override", text)
            self.assertIn('"127.0.0.1:${GATEWAY_PORT:-8000}:8000"', text)
            return
        ports = data["services"]["gateway"]["ports"]
        self.assertEqual("override", ports["__tag__"], "without !override compose appends to the base 8000:8000")
        self.assertEqual(["127.0.0.1:${GATEWAY_PORT:-8000}:8000"], ports["value"])
        env = data["services"]["gateway"]["environment"]
        self.assertIn("CIVIL_JOB_ROOT=/app/output/job", env)
        self.assertEqual([{"path": "./model.env", "required": False}], data["services"]["gateway"]["env_file"],
                         "the model key is opt-in: read from model.env only when the operator wrote one")
        caddy = data["services"]["caddy"]
        self.assertEqual("caddy:2", caddy["image"])
        self.assertEqual(["${CADDY_HTTP_PORT:-80}:80", "${CADDY_HTTPS_PORT:-443}:443"], caddy["ports"])
        self.assertIn("./deploy/lightsail/Caddyfile:/etc/caddy/Caddyfile:ro", caddy["volumes"])
        self.assertTrue(any(v.startswith("caddy_data:/data") for v in caddy["volumes"]))
        self.assertTrue(any(e.startswith("SITE_ADDRESS=${SITE_ADDRESS:?") for e in caddy["environment"]))
        self.assertEqual("unless-stopped", data["services"]["gateway"]["restart"])


class CaddyTests(unittest.TestCase):
    def test_caddyfile(self) -> None:
        text = read(LS / "Caddyfile")
        self.assertIn("{$SITE_ADDRESS} {", text)
        self.assertRegex(text, r"reverse_proxy gateway:8000")
        size = re.search(r"max_size (\d+)MB", text)
        self.assertIsNotNone(size)
        self.assertGreaterEqual(int(size.group(1)), 15, "Caddy must not refuse what the upload route accepts (10 + 5 MB)")
        self.assertIn("Strict-Transport-Security", text)
        for needle in ("delete token", "request>headers>Cookie delete", "request>headers>Authorization delete",
                       "resp_headers>Set-Cookie delete"):
            self.assertIn(needle, text)
        self.assertNotRegex(text, r"(?m)^\s*tls internal")
        self.assertEqual(text.count("{"), text.count("}"))


class UserDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(LS / "user-data.sh")

    def test_shape(self) -> None:
        t = self.text
        self.assertTrue(t.startswith("#!/bin/bash\n"), "Lightsail runs the launch script as a shell script")
        self.assertIn("\nset -euo pipefail\n", t)
        self.assertNotRegex(t, r"(?m)^\s*set\s+-[a-z]*x", "xtrace would print the token")
        self.assertNotIn("\r", t)
        self.assertIn("NOT YET RUN ON LIGHTSAIL", t)
        self.assertIn('CIVIL_REF="${CIVIL_REF:-__CIVIL_REF__}"', t)
        self.assertIn("^[0-9a-f]{40}$", t)
        self.assertIn("checkout -q --detach \"$CIVIL_REF\"", t)
        self.assertIn("deploy/lightsail/compose.override.yml", t)
        self.assertIn("download.docker.com/linux/ubuntu", t)
        self.assertNotRegex(t, r"curl[^\n|]*\|\s*(sudo\s+)?(ba)?sh", "no curl | sh")

    def test_token_is_generated_into_a_0600_file_and_never_printed(self) -> None:
        t = self.text
        self.assertIn("openssl rand -hex 32", t)
        self.assertIn("umask 077", t)
        self.assertIn('chmod 600 "$env_file"', t)
        for line in t.splitlines():
            code = line.split("#", 1)[0] if not line.lstrip().startswith("say ") else line
            if "CIVIL_TOKEN" in code and re.search(r"\b(echo|say|printf|cat|tee)\b", code):
                ok_write = "printf 'CIVIL_TOKEN=%s\\n'" in code and '> "$env_file"' in code
                ok_hint = code.lstrip().startswith("say ") and "civil-admin.sh show-link" in code
                self.assertTrue(ok_write or ok_hint, f"prints the token? {line.strip()}")
        self.assertNotRegex(t, r"\$\{?CIVIL_TOKEN\}?")

    def test_seeds_only_synthetic_files_and_no_model_key(self) -> None:
        t = self.text
        seeded = re.findall(r"examples/facade-demo/([\w.]+)", t)
        self.assertEqual({"facade_itt_doc.md", "facade_panels.xlsx", "facade_panels_rev_b.xlsx"}, set(seeded))
        self.assertNotRegex(t, r"(API_KEY|OPENAI|DEEPSEEK|BEDROCK|AWS_SECRET|aws configure)")

    def test_refuses_a_commit_without_the_kit(self) -> None:
        self.assertIn('die "commit $CIVIL_REF has no $f', self.text)
        self.assertIn('REPO_URL="${CIVIL_REPO_URL:-__CIVIL_REPO_URL__}"', self.text)


class AdminTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(LS / "civil-admin.sh")

    @staticmethod
    def body(text: str, name: str) -> str:
        return text.split(f"{name}() {{", 1)[1].split("\n}\n", 1)[0]

    def test_shape(self) -> None:
        t = self.text
        self.assertTrue(t.startswith("#!/bin/bash\n"))
        self.assertIn("\nset -euo pipefail\n", t)
        self.assertNotRegex(t, r"(?m)^\s*set\s+-[a-z]*x")
        self.assertNotIn("\r", t)
        self.assertIn("NOT YET RUN ON LIGHTSAIL", t)
        for cmd in ("show-link", "rotate-token", "set-site", "model-on", "model-off", "status"):
            self.assertIn(f"  {cmd})", t)

    def test_the_token_is_printed_in_one_line_and_once(self) -> None:
        t = self.text
        printing = [line.strip() for line in t.splitlines()
                    if "$token" in line and re.search(r"\b(echo|printf|say|cat|tee)\b", line.split("#", 1)[0])]
        self.assertEqual(["printf '%s/?token=%s\\n' \"$(site_url)\" \"$token\"    # the one place the token is printed"],
                         printing)
        body = self.body(t, "show_link")
        refuse = body.index('die "the link for this token was already shown')
        self.assertLess(refuse, body.index("printf '%s/?token=%s"), "the refusal comes before the print")
        self.assertIn("fingerprint=%s", body)       # the marker stores a hash prefix, not the token
        self.assertIn("umask 077", body)
        self.assertIn("openssl rand -hex 32", self.body(t, "rotate_token"))
        self.assertIn('rm -f "$shown_file"', self.body(t, "rotate_token"))

    def test_model_key_is_read_hidden_into_a_0600_file_and_never_echoed(self) -> None:
        t = self.text
        body = self.body(t, "model_on")
        self.assertIn('read -r -s -p "API key', body)
        self.assertIn("umask 077", body)
        self.assertIn('chmod 600 "$model_file"', body)
        self.assertIn("OPT-IN", body)
        for line in t.splitlines():
            if "$key" in line and re.search(r"\b(echo|say|cat|tee)\b", line):
                self.fail(f"echoes the key? {line.strip()}")
        writes = [line for line in t.splitlines() if "$key" in line and "printf" in line]
        self.assertEqual(1, len(writes))
        self.assertIn('> "$model_file.new"', body)
        # single-quoted values: Compose interpolates $ in an unquoted env_file value and would cut a key short
        self.assertIn("CIVIL_API_KEY='%s'", body)
        self.assertIn("""*"'"*) die""", body)
        self.assertIn('rm -f "$model_file"', self.body(t, "model_off"))

    def test_set_site_restarts_caddy_only(self) -> None:
        # --force-recreate on caddy alone would also recreate the gateway it depends on (a needless restart)
        self.assertIn("up -d --no-deps --force-recreate caddy", self.body(self.text, "set_site"))


class ImageTests(unittest.TestCase):
    def test_uvicorn_access_log_off(self) -> None:
        self.assertIn("--no-access-log", read(ROOT / "Dockerfile"))

    def test_dockerignore_recurses(self) -> None:
        lines = {line.strip() for line in read(ROOT / ".dockerignore").splitlines()}
        for pattern in ("**/.env", "**/.env.*", "!**/.env.example", "demo/out", "demo/data", "output",
                        "**/.civil-buddy", "**/*.log", "**/model.env", "**/.link-shown"):
            self.assertIn(pattern, lines)

    def test_gitignore_keeps_the_model_key_and_the_filled_script_out(self) -> None:
        lines = {line.strip() for line in read(ROOT / ".gitignore").splitlines()}
        for pattern in ("model.env", ".link-shown", "deploy/lightsail/*.filled.sh"):
            self.assertIn(pattern, lines)


class DocTests(unittest.TestCase):
    def test_lightsail_guide(self) -> None:
        text = read(ROOT / "docs" / "deploy-aws-lightsail.md")
        self.assertLess(cjk(text), 5, "the Lightsail guide is in English")
        self.assertIn("has not been run on Lightsail", text)
        for needle in ("aws lightsail create-instances", "--region ap-southeast-1", "--blueprint-id ubuntu_24_04",
                       "aws lightsail allocate-static-ip", "aws lightsail attach-static-ip",
                       "aws lightsail put-instance-public-ports", "aws lightsail enable-add-on", "AutoSnapshot",
                       "10 Oct", "sslip.io", "deploy/lightsail/test-local.sh",
                       # both paths, the operator commands, and the teardown that stops the charges
                       "Path A", "Path B", "Lightsail console", "civil-admin.sh show-link", "civil-admin.sh model-on",
                       "rotate-token", "aws lightsail delete-instance", "aws lightsail release-static-ip",
                       "get-instance-snapshots", "small_3_0", "cidrs=[${MYIP}/32]"):
            self.assertIn(needle, text)
        self.assertRegex(text, r"(?i)tear.?down")
        self.assertRegex(text, r"(?i)synthetic")
        self.assertNotRegex(text, r"sk-[A-Za-z0-9]{16,}", "no key-shaped string in the guide")

    def test_minimal_guide_is_english_aws_first_render_last(self) -> None:
        text = read(ROOT / "docs" / "deploy-minimal.md")
        self.assertLess(cjk(text), 5, "the guide is in English")
        heads = [h for h in re.findall(r"(?m)^## (.+)$", text)]
        routes = [h for h in heads if re.search(r"(?i)aws|lightsail|linux|railway|render", h)]
        self.assertTrue(routes and re.search(r"(?i)aws|lightsail", routes[0]), routes)
        self.assertRegex(routes[-1], r"(?i)render", routes)
        self.assertNotIn("满载演示", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
