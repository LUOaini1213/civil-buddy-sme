#!/bin/bash
# Operator commands on the Lightsail instance, run over SSH (or the console's browser SSH) with sudo:
#
#   sudo bash /opt/civil-buddy/deploy/lightsail/civil-admin.sh show-link      the access link, printed once
#   sudo bash /opt/civil-buddy/deploy/lightsail/civil-admin.sh rotate-token   a new token; old links and cookies get 401;
#                                                                            the new link is printed once
#   sudo bash /opt/civil-buddy/deploy/lightsail/civil-admin.sh set-site [host]
#        the HTTPS host name Caddy serves; without [host]: this instance's public IP as <a-b-c-d>.sslip.io
#   sudo bash /opt/civil-buddy/deploy/lightsail/civil-admin.sh model-on       OPT-IN: asks for the model endpoint, the
#        model name and the key (typed hidden, never echoed), writes them to model.env (mode 0600), restarts the gateway
#   sudo bash /opt/civil-buddy/deploy/lightsail/civil-admin.sh model-off      deletes model.env, restarts the gateway
#   sudo bash /opt/civil-buddy/deploy/lightsail/civil-admin.sh status         what runs, the site, whether the link was
#        shown and whether a model key is set (never a value)
#
# The token is generated on the box by user-data.sh (openssl, into .env, mode 0600). This script prints it in exactly
# one place, show_link, and only once per token: a second show-link refuses and points at rotate-token. Root can still
# read .env; "once" means this tool never repeats it, so it does not end up in terminal scroll-back twice.
# NOT YET RUN ON LIGHTSAIL; rehearsed by deploy/lightsail/test-local.sh on a local Docker host.
set -euo pipefail

INSTALL_DIR="${CIVIL_INSTALL_DIR:-/opt/civil-buddy}"
env_file="$INSTALL_DIR/.env"
model_file="$INSTALL_DIR/model.env"
shown_file="$INSTALL_DIR/.link-shown"

say() { printf '[civil-admin] %s\n' "$*" >&2; }
die() { say "ERROR: $*"; exit 1; }
compose() { (cd "$INSTALL_DIR" && docker compose -f docker-compose.yml -f deploy/lightsail/compose.override.yml --env-file "$env_file" "$@"); }

[ -f "$env_file" ] || die "$env_file not found (did user-data.sh run?)"
[ -r "$env_file" ] || die "run with sudo: $env_file is readable by root only"

env_get() { grep "^$1=" "$env_file" | head -n 1 | cut -d= -f2-; }
env_set() {  # env_set KEY VALUE: rewrite .env keeping mode 0600, never through a world-readable temp file
  ( umask 077
    grep -v "^$1=" "$env_file" > "$env_file.new" || true
    printf '%s=%s\n' "$1" "$2" >> "$env_file.new"
    mv "$env_file.new" "$env_file" )
  chmod 600 "$env_file"
}
fingerprint() { printf '%s' "$(env_get CIVIL_TOKEN)" | sha256sum | cut -c1-12; }
site_url() {
  local site; site="$(env_get SITE_ADDRESS)"
  case "$site" in http://*|https://*) printf '%s' "$site" ;; *) printf 'https://%s' "$site" ;; esac
}

show_link() {
  local token fp; token="$(env_get CIVIL_TOKEN)"
  [[ "$token" =~ ^[0-9a-f]{64}$ ]] || die "$env_file has no well-formed CIVIL_TOKEN"
  fp="$(fingerprint)"
  if [ -f "$shown_file" ] && grep -q "fingerprint=$fp" "$shown_file"; then
    die "the link for this token was already shown ($(cut -d' ' -f1 "$shown_file")). Lost it? rotate-token prints a new one."
  fi
  ( umask 077; printf '%s fingerprint=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$fp" > "$shown_file" )
  say "the access link (shown this once; it is the key: only into the organisers' submission form, nowhere else):"
  printf '%s/?token=%s\n' "$(site_url)" "$token"    # the one place the token is printed
  say "opening it once sets a 30-day HttpOnly cookie; then $(site_url)/demo runs the linked demo"
}

rotate_token() {
  env_set CIVIL_TOKEN "$(openssl rand -hex 32)"
  rm -f "$shown_file"
  compose up -d --force-recreate gateway >&2
  say "new token in $env_file; every earlier link and cookie now gets 401"
  show_link
}

public_ip() {
  local t ip
  t="$(curl -fsS -m 3 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
  ip="$(curl -fsS -m 3 -H "X-aws-ec2-metadata-token: $t" http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  [ -n "$ip" ] || ip="$(curl -fsS -m 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
  printf '%s' "$ip"
}

set_site() {
  local site="${1:-}"
  if [ -z "$site" ]; then
    local ip; ip="$(public_ip)"
    [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "could not find this instance's public IP; pass the host name"
    site="${ip//./-}.sslip.io"
  fi
  [[ "$site" =~ ^(https?://)?[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || die "'$site' is not a host name"
  env_set SITE_ADDRESS "$site"
  compose up -d --no-deps --force-recreate caddy >&2    # --no-deps: the gateway keeps running
  say "Caddy now serves $(site_url) (a new certificate can take a minute; port 80 must be open for it)"
}

model_on() {
  local base model key
  say "OPT-IN: a model key on this box lets anyone with the access token spend the team's shared credits."
  read -r -p "model endpoint (OpenAI-compatible base URL, e.g. https://.../v1): " base
  read -r -p "model name: " model
  read -r -s -p "API key (hidden, not echoed): " key; printf '\n' >&2
  [[ "$base" =~ ^https?://[^[:space:]]+$ ]] || die "the endpoint must be an http(s) URL"
  [[ "$model" =~ ^[^[:space:]]+$ ]] || die "the model name is empty or has spaces"
  [[ "$key" =~ ^[^[:space:]]{8,}$ ]] || die "the key is empty, short or has spaces"
  # Single-quoted in model.env: Compose interpolates $ in an unquoted env_file value ("ab$cd" reached the container
  # as "ab"), so a key with a $ in it was cut short without a word. A single quote cannot be written that way.
  case "$base$model$key" in *"'"*) die "the endpoint, model name or key contains a single quote; model.env cannot hold it" ;; esac
  ( umask 077
    printf "CIVIL_API_BASE='%s'\nCIVIL_MODEL='%s'\nCIVIL_API_KEY='%s'\n" "$base" "$model" "$key" > "$model_file.new"
    mv "$model_file.new" "$model_file" )
  chmod 600 "$model_file"
  unset key
  compose up -d --force-recreate gateway >&2
  say "model.env written (mode 600) and the gateway restarted with it; model-off removes it"
}

model_off() {
  rm -f "$model_file"
  compose up -d --force-recreate gateway >&2
  say "model.env removed; the gateway runs without a model key"
}

status() {
  compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Ports}}' >&2 || true
  say "site: $(site_url)"
  if [ -f "$shown_file" ] && grep -q "fingerprint=$(fingerprint)" "$shown_file"; then
    say "access link: shown $(cut -d' ' -f1 "$shown_file")"
  else
    say "access link: not shown yet (show-link)"
  fi
  if [ -f "$model_file" ]; then say "model key: SET in $model_file (mode $(stat -c '%a' "$model_file"))"; else say "model key: none"; fi
}

case "${1:-}" in
  show-link) show_link ;;
  rotate-token) rotate_token ;;
  set-site) set_site "${2:-}" ;;
  model-on) model_on ;;
  model-off) model_off ;;
  status) status ;;
  *) sed -n '2,13p' "$0" >&2; exit 2 ;;
esac
