#!/bin/sh
# Optional TLS: mount cert/key and set SG_SSL_CERT / SG_SSL_KEY.
EXTRA=""
if [ -n "$SG_SSL_CERT" ] && [ -n "$SG_SSL_KEY" ]; then
  EXTRA="--ssl-certfile $SG_SSL_CERT --ssl-keyfile $SG_SSL_KEY"
fi
# Behind a reverse proxy set SG_FORWARDED_ALLOW_IPS to the proxy's address (or "*" if only the proxy can reach this port) so the real client IP
# is used for login lockouts, the audit log and SG_IP_RPM; otherwise every visitor looks like the proxy.
EXTRA="$EXTRA --proxy-headers --forwarded-allow-ips ${SG_FORWARDED_ALLOW_IPS:-127.0.0.1}"
# Run unprivileged: a file-read or code bug then cannot touch /etc/shadow or anything outside the app and its data volume.
# Older volumes are owned by root, so fix ownership once while still root, then drop privileges.
if [ "$(id -u)" = "0" ]; then
  chown -R sg:sg "${SG_DATA_DIR:-/data}" 2>/dev/null
  exec setpriv --reuid=sg --regid=sg --init-groups uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-access-log $EXTRA
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-access-log $EXTRA
