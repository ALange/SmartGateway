#!/bin/sh
# Optional TLS: mount cert/key and set SG_SSL_CERT / SG_SSL_KEY.
EXTRA=""
if [ -n "$SG_SSL_CERT" ] && [ -n "$SG_SSL_KEY" ]; then
  EXTRA="--ssl-certfile $SG_SSL_CERT --ssl-keyfile $SG_SSL_KEY"
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-access-log $EXTRA
