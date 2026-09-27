#!/bin/bash
# Civil Buddy on one AWS Lightsail instance (Ubuntu 24.04 LTS): pass this file as the instance's launch script
# (`aws lightsail create-instances ... --user-data file://user-data.sh`). It runs once, as root, at first boot.
#
# NOT YET RUN ON LIGHTSAIL. What was tested, and how, is in docs/deploy-aws-lightsail.md ("What was tested").
#
# It installs Docker from Docker's apt repository, clones the repository at one pinned commit, writes a random
# CIVIL_TOKEN into /opt/civil-buddy/.env (mode 0600, never printed), starts the gateway on 127.0.0.1 behind Caddy
# (automatic HTTPS), and seeds the job folder with the SYNTHETIC demo files only. No model key is written anywhere
# (model keys are opt-in afterwards: deploy/lightsail/civil-admin.sh model-on).
# Output goes to /var/log/cloud-init-output.log; the token is not in it. The operator gets the access link once,
# over SSH: sudo bash /opt/civil-buddy/deploy/lightsail/civil-admin.sh show-link
set -euo pipefail

# ---- set these before launching (docs/deploy-aws-lightsail.md fills them in with sed) ------------------------
CIVIL_REF="${CIVIL_REF:-__CIVIL_REF__}"             # the full 40-character commit to deploy
SITE_ADDRESS="${SITE_ADDRESS:-__SITE_ADDRESS__}"    # 203-0-113-10.sslip.io (static IP, dashed) or your domain
REPO_URL="${CIVIL_REPO_URL:-__CIVIL_REPO_URL__}"      # empty/placeholder: the public showcase repository below
# ---- local-test hooks: leave unset on Lightsail ----------------------------------------------------------------
INSTALL_DIR="${CIVIL_INSTALL_DIR:-/opt/civil-buddy}"
SKIP_DOCKER_INSTALL="${CIVIL_SKIP_DOCKER_INSTALL:-0}"
GATEWAY_PORT="${GATEWAY_PORT:-8000}"

export HOME="${HOME:-/root}"
[ "$SITE_ADDRESS" = "__SITE_ADDRESS__" ] && SITE_ADDRESS=""
case "$REPO_URL" in ""|__CIVIL_REPO_URL__) REPO_URL="https://github.com/LUOaini1213/civil-buddy-sme.git" ;; esac

say() { printf '[civil-buddy] %s\n' "$*"; }
die() { say "ERROR: $*" >&2; exit 1; }

[[ "$CIVIL_REF" =~ ^[0-9a-f]{40}$ ]] || die "CIVIL_REF must be a full 40-character commit SHA (got '$CIVIL_REF')"

if [ -z "$SITE_ADDRESS" ]; then
  # Fallback only: the address this instance has now. A static IP attached later changes it, so set SITE_ADDRESS.
  imds_token="$(curl -fsS -m 3 -X PUT http://169.254.169.254/latest/api/token \
                 -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
  ip="$(curl -fsS -m 3 -H "X-aws-ec2-metadata-token: ${imds_token}" \
         http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  [ -n "$ip" ] || ip="$(curl -fsS -m 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
  [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "SITE_ADDRESS is empty and the public IP could not be found"
  SITE_ADDRESS="${ip//./-}.sslip.io"
  say "SITE_ADDRESS was empty; using ${SITE_ADDRESS} (wrong if a different static IP is attached later)"
fi
[[ "$SITE_ADDRESS" =~ ^(https?://)?[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || die "SITE_ADDRESS '$SITE_ADDRESS' is not a host name"

if [ "$SKIP_DOCKER_INSTALL" != "1" ]; then
  export DEBIAN_FRONTEND=noninteractive
  # 2 GB of swap: the image build (pip install) on a 2 GB instance should not meet the OOM killer
  if ! swapon --show | grep -q .; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
  say "installing Docker from Docker's apt repository"
  apt-get update -q
  apt-get install -y -q ca-certificates curl git openssl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck disable=SC1091
  codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi
docker compose version >/dev/null || die "docker compose is not available"

say "cloning ${REPO_URL} at ${CIVIL_REF}"
if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" fetch -q origin
else
  git clone -q "$REPO_URL" "$INSTALL_DIR"
fi
git -C "$INSTALL_DIR" -c advice.detachedHead=false checkout -q --detach "$CIVIL_REF"
[ "$(git -C "$INSTALL_DIR" rev-parse HEAD)" = "$CIVIL_REF" ] || die "checkout is not at $CIVIL_REF"
cd "$INSTALL_DIR"
for f in deploy/lightsail/compose.override.yml deploy/lightsail/Caddyfile deploy/lightsail/civil-admin.sh; do
  [ -f "$f" ] || die "commit $CIVIL_REF has no $f: deploy a commit that contains the Lightsail kit"
done

# The token: generated here, written only to .env (0600, root), never echoed. A re-run keeps the existing token.
env_file="$INSTALL_DIR/.env"
if [ ! -f "$env_file" ]; then
  ( umask 077
    printf 'CIVIL_TOKEN=%s\n' "$(openssl rand -hex 32)" > "$env_file" )
fi
chmod 600 "$env_file"
grep -q '^CIVIL_TOKEN=[0-9a-f]\{64\}$' "$env_file" || die "$env_file has no well-formed CIVIL_TOKEN"
( umask 077
  grep -v '^SITE_ADDRESS=' "$env_file" > "$env_file.new" || true
  printf 'SITE_ADDRESS=%s\n' "$SITE_ADDRESS" >> "$env_file.new"
  mv "$env_file.new" "$env_file" )
chmod 600 "$env_file"

compose() { docker compose -f docker-compose.yml -f deploy/lightsail/compose.override.yml --env-file "$env_file" "$@"; }

say "building and starting (the first build takes several minutes)"
compose up -d --build

say "waiting for the gateway on 127.0.0.1:${GATEWAY_PORT}"
for _ in $(seq 1 300); do
  if curl -fsS -m 3 -o /dev/null "http://127.0.0.1:${GATEWAY_PORT}/api/health"; then break; fi
  sleep 2
done
curl -fsS -m 3 -o /dev/null "http://127.0.0.1:${GATEWAY_PORT}/api/health" || die "the gateway did not come up; see: docker compose logs gateway"

say "seeding /app/output/job with the SYNTHETIC demo files (examples/facade-demo)"
compose exec -T gateway sh -c 'mkdir -p /app/output/job && cp -n examples/facade-demo/facade_itt_doc.md \
  examples/facade-demo/facade_panels.xlsx examples/facade-demo/facade_panels_rev_b.xlsx /app/output/job/'

scheme="https://"
case "$SITE_ADDRESS" in http://*|https://*) scheme="" ;; esac
say "up: ${scheme}${SITE_ADDRESS}/  (the certificate can take a minute after the static IP is attached)"
say "the access link, shown once, over SSH:  sudo bash ${INSTALL_DIR}/deploy/lightsail/civil-admin.sh show-link"
