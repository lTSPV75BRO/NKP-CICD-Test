#!/bin/sh
# Inject cluster DNS from pod's resolv.conf so nginx can resolve backend at request time
RESOLVER_IP=$(grep '^nameserver' /etc/resolv.conf | head -1 | awk '{print $2}')
if [ -z "$RESOLVER_IP" ]; then
  RESOLVER_IP="10.96.0.10"
fi
sed "s/__RESOLVER_IP__/$RESOLVER_IP/g" /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf
exec nginx -g "daemon off;"
