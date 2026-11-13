#!/bin/sh
set -eu

bundle_dir=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
archive="$bundle_dir/classroom-cloud-update.tar.gz"
cd -- "${1:-$bundle_dir/..}"
config=deploy/compose.yaml
if [ -f compose.yaml ]; then config=compose.yaml; fi
if [ ! -f "$config" ] || [ ! -f "$archive" ]; then
    printf '%s\n' '请把更新包放在脚本旁，并指定正在运行的项目根目录。' >&2
    exit 1
fi

if docker info >/dev/null 2>&1; then
    compose() { docker compose --project-directory "$PWD" -f "$config" "$@"; }
elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null; then
    compose() { sudo docker compose --project-directory "$PWD" -f "$config" "$@"; }
else
    printf '%s\n' '无法连接 Docker 引擎，请检查 Docker 是否已启动以及当前账号的权限。' >&2
    exit 1
fi

compose config -q
staging=$(mktemp -d)
trap 'rm -rf -- "$staging"' 0
tar -xzf "$archive" -C "$staging"

for file in app.py timetable.py requirements.txt deploy/Dockerfile .dockerignore deploy/requirements.txt templates/index.html static/app.js static/style.css; do
    if [ ! -f "$staging/$file" ]; then
        printf '更新包缺少 %s。\n' "$file" >&2
        exit 1
    fi
done

mkdir -p deploy
cp -- "$staging/app.py" "$staging/timetable.py" "$staging/requirements.txt" "$staging/.dockerignore" .
cp -- "$staging/deploy/Dockerfile" "$staging/deploy/requirements.txt" deploy/
if [ "$config" = compose.yaml ]; then cp -- deploy/Dockerfile Dockerfile; fi
cp -R -- "$staging/templates" "$staging/static" .

if ! compose up -d --build --wait app; then
    compose logs --tail=60 app >&2 || true
    exit 1
fi
compose ps app
printf '%s\n' '更新完成，应用已通过健康检查。'
