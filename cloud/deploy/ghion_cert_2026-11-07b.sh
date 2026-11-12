#!/bin/bash
# Second try: certbot refused a new certificate because the old one's files exist without their renewal file.
# This writes the renewal file for the existing certificate, then renews it (and from now on it renews by itself).
#     bash ~/unio-cloud/deploy/ghion_cert_2026-11-07b.sh
set -euo pipefail
ssh ghion 'bash -s' <<'REMOTE'
set -euo pipefail
D=ghioninvestment.sonador.et
cat > /etc/letsencrypt/renewal/$D.conf <<CONF
# renew_before_expiry = 30 days
version = 2.9.0
archive_dir = /etc/letsencrypt/archive/$D
cert = /etc/letsencrypt/live/$D/cert.pem
privkey = /etc/letsencrypt/live/$D/privkey.pem
chain = /etc/letsencrypt/live/$D/chain.pem
fullchain = /etc/letsencrypt/live/$D/fullchain.pem

# Options used in the renewal process
[renewalparams]
account = 501929dd76fa6a44c485ae1e2ec43b8a
authenticator = nginx
server = https://acme-v02.api.letsencrypt.org/directory
key_type = ecdsa
CONF
certbot renew --cert-name $D --force-renewal --non-interactive 2>&1 | tail -4
nginx -t && systemctl reload nginx
REMOTE
echo | openssl s_client -connect ghioninvestment.sonador.et:443 -servername ghioninvestment.sonador.et 2>/dev/null | openssl x509 -noout -enddate
