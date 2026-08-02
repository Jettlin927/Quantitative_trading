#!/bin/sh
set -eu

config_path=/etc/nginx/personal-gateway.conf
token_file=${PERSONAL_GATEWAY_TOKEN_FILE:-}

if [ -n "$token_file" ] && [ -r "$token_file" ]; then
    token=$(tr -d '\r\n' < "$token_file")
    case "$token" in
        ''|*[!A-Za-z0-9._~-]*)
            echo "个人网关 token 文件格式无效" >&2
            exit 1
            ;;
    esac
    umask 077
    printf 'proxy_set_header X-Personal-Gateway "%s";\n' "$token" > "$config_path"
else
    printf 'proxy_set_header X-Personal-Gateway "";\n' > "$config_path"
fi

unset token
