#!/bin/sh
set -eu

read_secret() {
    variable_name=$1
    file_variable_name=$2
    eval "secret_path=\${$file_variable_name:-}"
    if [ -z "$secret_path" ] || [ ! -f "$secret_path" ] || [ ! -r "$secret_path" ] || [ ! -s "$secret_path" ]; then
        echo "启动失败：密钥文件不可读或为空：$file_variable_name" >&2
        exit 1
    fi
    secret_value=$(tr -d '\r\n' < "$secret_path")
    if [ -z "$secret_value" ]; then
        echo "启动失败：密钥文件内容为空：$file_variable_name" >&2
        exit 1
    fi
    export "$variable_name=$secret_value"
}

read_secret DB_PASSWORD DB_PASSWORD_FILE

if [ -n "${APP_BOOTSTRAP_ADMIN_PASSWORD_FILE:-}" ]; then
    if [ ! -f "$APP_BOOTSTRAP_ADMIN_PASSWORD_FILE" ] || [ ! -r "$APP_BOOTSTRAP_ADMIN_PASSWORD_FILE" ] || [ ! -s "$APP_BOOTSTRAP_ADMIN_PASSWORD_FILE" ]; then
        echo "启动失败：初始管理员密钥文件不可读或为空" >&2
        exit 1
    fi
fi

if [ "${APP_DEPLOYMENT_MODE:-}" = "NETWORK" ]; then
    read_secret SERVER_SSL_KEY_STORE_PASSWORD SERVER_SSL_KEY_STORE_PASSWORD_FILE
fi

exec java -jar /app/core-api.jar
