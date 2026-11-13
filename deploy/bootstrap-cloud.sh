#!/bin/sh
set -eu

project_dir=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
if [ "$#" -gt 1 ] || [ ! -f "$project_dir/deploy/compose.yaml" ] || [ ! -f "$project_dir/deploy/Dockerfile" ]; then
    echo '请在项目目录运行 sh deploy/bootstrap-cloud.sh [数据库快照路径]。' >&2
    exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
    echo '请用日常登录的 Ubuntu 用户运行脚本。需要管理员权限时脚本会调用 sudo。' >&2
    exit 1
fi

snapshot=${1:-}
if [ -n "$snapshot" ]; then
    if [ ! -f "$snapshot" ]; then
        echo '找不到指定的数据库快照。' >&2
        exit 1
    fi
    snapshot=$(realpath -- "$snapshot")
fi
cd "$project_dir"

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    . /etc/os-release
    if [ "$ID" != ubuntu ]; then
        echo '自动安装 Docker 适用于 Ubuntu。请先在当前系统安装 Docker Engine 和 Compose。' >&2
        exit 1
    fi
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo systemctl enable --now docker
fi

if docker info >/dev/null 2>&1; then
    use_sudo=0
    engine() { docker "$@"; }
else
    use_sudo=1
    engine() { sudo docker "$@"; }
fi
compose() {
    if [ "$use_sudo" -eq 1 ]; then
        sudo env CLASSROOM_DATA_DIR="$CLASSROOM_DATA_DIR" CLASSROOM_UID="$CLASSROOM_UID" \
            CLASSROOM_GID="$CLASSROOM_GID" docker compose -p classroom-bootstrap --project-directory "$project_dir" -f deploy/compose.yaml "$@"
    else
        docker compose -p classroom-bootstrap --project-directory "$project_dir" -f deploy/compose.yaml "$@"
    fi
}

# Pin storage and runtime ownership for this self-contained installation.
export CLASSROOM_DATA_DIR="$project_dir/data"
export CLASSROOM_UID=$(id -u) CLASSROOM_GID=$(id -g)
existing=$(compose ps -a -q app)
if [ -n "$existing" ]; then
    existing_dir=$(engine inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$existing")
    if [ "$existing_dir" != "$project_dir" ]; then
        echo '服务器上已有另一处 bootstrap 部署，请从原安装目录运行。' >&2
        exit 1
    fi
fi
install -d -m 700 "$CLASSROOM_DATA_DIR"
if [ -n "$snapshot" ]; then
    if [ -e data/classrooms.sqlite3 ] || [ -n "$existing" ]; then
        echo '已有课表或容器。请使用数据同步脚本导入快照。' >&2
        exit 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        sudo apt-get install -y python3
    fi
    python3 - "$snapshot" <<'PY'
import sqlite3
import sys
from pathlib import Path

with sqlite3.connect(Path(sys.argv[1]).as_uri() + '?mode=ro&immutable=1', uri=True) as db:
    if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
        raise SystemExit('数据库快照完整性检查失败。')
    for table in ('rooms', 'lessons', 'settings'):
        db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
PY
    install -m 600 -- "$snapshot" data/classrooms.sqlite3
fi

compose config -q
if ! compose up -d --build --wait --wait-timeout 120 app; then
    compose logs --tail 60 app >&2 || true
    exit 1
fi
compose ps app
address=$(compose port app 8000)
printf '部署完成。请访问 http://服务器公网IP:%s/\n' "${address##*:}"
