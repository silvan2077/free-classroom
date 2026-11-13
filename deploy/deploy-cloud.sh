#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
project_dir=$PWD
config=deploy/compose.yaml

if [ ! -f "$config" ] || [ ! -f deploy/Dockerfile ]; then
    printf '%s\n' '请保留项目中的 deploy 目录结构。' >&2
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' '请先在服务器安装 Docker Engine 和 Docker Compose 插件。' >&2
    exit 1
fi

if docker info >/dev/null 2>&1; then
    compose() { docker compose --project-directory "$project_dir" -f "$config" "$@"; }
elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null; then
    compose() { sudo docker compose --project-directory "$project_dir" -f "$config" "$@"; }
else
    printf '%s\n' '无法连接 Docker 引擎，请检查 Docker 是否已启动以及当前账号的权限。' >&2
    exit 1
fi

compose version >/dev/null
compose config -q

port_pattern='^([[:blank:]]*-[[:blank:]]*)"(127\.0\.0\.1|0\.0\.0\.0):\$\{CLASSROOM_PORT:-8000\}:[0-9]+"[[:space:]]*$'
if ! grep -Eq -- "$port_pattern" "$config"; then
    printf '%s\n' 'compose.yaml 的端口配置与项目模板不同，请核对 app 的端口绑定。' >&2
    exit 1
fi

public='"0.0.0.0:${CLASSROOM_PORT:-8000}:8000"'
if ! grep -Fq -- "$public" "$config"; then
    temporary=$(mktemp deploy/.compose.yaml.XXXXXX)
    trap 'rm -f -- "$temporary"' 0
    sed -E "s/$port_pattern/\1$public/" "$config" > "$temporary"
    mv -- "$temporary" "$config"
fi

mkdir -p -- data
if [ ! -e .env ]; then
    printf 'CLASSROOM_UID=%s\nCLASSROOM_GID=%s\n' "$(id -u)" "$(id -g)" > .env
fi

if ! compose up -d --build --wait --wait-timeout 120 app; then
    compose logs --tail=60 app >&2 || true
    exit 1
fi

compose ps app
address=$(compose port app 8000)
printf '\n服务已通过健康检查，服务器端口为 %s。\n' "${address##*:}"
printf '请在浏览器打开 http://服务器公网IP:%s/\n' "${address##*:}"
