#!/bin/sh
# Derive nginx's DNS resolver from the container's /etc/resolv.conf.
#
# Why this exists: the API/socket.io locations proxy to a hostname that is NOT
# stable at the IP level — on Railway the backend's private domain
# (<service>.railway.internal) can resolve to a new IPv6 address on every
# redeploy, and Docker Compose's `backend-api` likewise. nginx resolves a
# literal hostname in proxy_pass ONCE at startup and then caches that IP
# forever, so after the backend redeploys the frontend serves 502s until it is
# manually restarted.
#
# The fix is to put the upstream in a variable (see nginx.conf.template), which
# makes nginx re-resolve per request — but that form REQUIRES a `resolver`
# directive, and nginx does not read /etc/resolv.conf on its own. So we write
# one here at container start, before the config is loaded.
set -e

CONF=/etc/nginx/conf.d/00-resolver.conf

# Railway's private network is IPv6; bracket v6 addresses for the directive.
NS=$(awk '/^nameserver/ { if ($2 ~ /:/) printf "[%s] ", $2; else printf "%s ", $2 }' /etc/resolv.conf 2>/dev/null || true)

# 127.0.0.11 is Docker's embedded DNS — the Compose fallback if resolv.conf
# could not be read for any reason.
[ -n "$NS" ] || NS="127.0.0.11"

echo "resolver ${NS}valid=10s ipv6=on;" > "$CONF"
echo "resolver_timeout 5s;" >> "$CONF"
echo "[10-resolver] $(cat "$CONF" | head -1)"
