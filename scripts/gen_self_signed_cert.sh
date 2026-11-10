#!/usr/bin/env bash
# 生成本地测试用自签证书。生产环境请换成正式证书（同样放到 deploy/certs/）。
set -euo pipefail
cd "$(dirname "$0")/.."

CERT_DIR="${CERT_DIR:-deploy/certs}"
mkdir -p "$CERT_DIR"

openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -keyout "$CERT_DIR/privkey.pem" \
    -out "$CERT_DIR/fullchain.pem" \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 "$CERT_DIR/privkey.pem"
echo "自签证书已生成：$CERT_DIR/fullchain.pem, $CERT_DIR/privkey.pem"
