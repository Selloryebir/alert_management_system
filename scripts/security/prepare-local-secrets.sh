#!/usr/bin/env bash
set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
secret_root=${APP_SECRETS_DIR:-"$repository_root/.runtime/compose-secrets"}

if [[ -e "$secret_root" && ! -d "$secret_root" ]]; then
  echo "密钥目标不是目录：$secret_root" >&2
  exit 1
fi
mkdir -p "$secret_root"
chmod 700 "$secret_root"

generate_secret() {
  local target=$1
  if [[ -e "$target" ]]; then
    if [[ ! -f "$target" || ! -s "$target" ]]; then
      echo "已有密钥路径不是非空普通文件，拒绝覆盖：$target" >&2
      exit 1
    fi
    chmod 644 "$target"
    return
  fi
  local value
  value=$(openssl rand -base64 32 | tr -d '\r\n')
  if [[ ${#value} -lt 40 ]]; then
    echo "无法生成足够长度的随机密钥" >&2
    exit 1
  fi
  (umask 077; printf '%s' "$value" > "$target")
  chmod 644 "$target"
}

generate_secret "$secret_root/database-password.txt"
generate_secret "$secret_root/bootstrap-admin-password.txt"

if [[ "$secret_root" == /mnt/?/* ]] \
    && command -v powershell.exe >/dev/null 2>&1 \
    && command -v wslpath >/dev/null 2>&1; then
  powershell.exe -NoProfile -ExecutionPolicy Bypass \
    -File "$(wslpath -w "$repository_root/scripts/security/protect-windows-secrets.ps1")" \
    -SecretRoot "$(wslpath -w "$secret_root")" </dev/null
fi

echo "本机 Compose 密钥已就绪：$secret_root"
echo "初始管理员：admin"
echo "首次登录密码文件：$secret_root/bootstrap-admin-password.txt"
