#!/bin/bash
# Renew the expired certificate of ghioninvestment.sonador.et and recreate its renewal file (it renews by itself after).
#     bash ~/unio-cloud/deploy/ghion_cert_2026-11-07.sh
set -euo pipefail
ssh ghion 'certbot certonly --nginx -d ghioninvestment.sonador.et --cert-name ghioninvestment.sonador.et --non-interactive --agree-tos && systemctl reload nginx'
echo | openssl s_client -connect ghioninvestment.sonador.et:443 -servername ghioninvestment.sonador.et 2>/dev/null | openssl x509 -noout -enddate
