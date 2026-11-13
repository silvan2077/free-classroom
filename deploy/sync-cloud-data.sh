#!/bin/sh
set -eu

bundle=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
snapshot="$bundle/classrooms.incoming.sqlite3"
cd -- "${1:-$bundle/..}"
config=deploy/compose.yaml
if [ -f compose.yaml ]; then config=compose.yaml; fi
if [ ! -f "$config" ] || [ ! -s "$snapshot" ]; then
    echo '请指定云端项目根目录，并将本机数据库快照与脚本放在一起。' >&2
    exit 1
fi

if docker info >/dev/null 2>&1; then
    engine() { docker "$@"; }
else
    engine() { sudo docker "$@"; }
fi

container=$(engine compose --project-directory "$PWD" -f "$config" ps -a -q app)
if [ -z "$container" ]; then
    echo '找不到现有云端 app 容器，请检查项目目录。' >&2
    exit 1
fi
data_dir=$(engine inspect --format '{{range .Mounts}}{{if and (eq .Type "bind") (eq .Destination "/app/data")}}{{.Source}}{{end}}{{end}}' "$container")
database="$data_dir/classrooms.sqlite3"
if [ -z "$data_dir" ] || [ ! -f "$database" ] || [ ! -w "$data_dir" ] || [ ! -O "$database" ]; then
    echo '请使用云端课表数据库的所有者账号运行脚本，并确认数据目录可写。' >&2
    exit 1
fi

start_container() {
    engine start "$container" >/dev/null || return 1
    attempt=0
    while [ "$attempt" -lt 60 ]; do
        health=$(engine inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container") || return 1
        case "$health" in
            healthy|running) return 0 ;;
            unhealthy|exited|dead) break ;;
        esac
        attempt=$((attempt + 1))
        sleep 2
    done
    engine logs --tail 40 "$container" >&2 || true
    return 1
}

engine stop "$container" >/dev/null
backup=''
temporary=''
installed=0
cleanup() {
    status=$?
    trap - 0
    if [ "$status" -ne 0 ]; then
        if [ "$installed" -eq 1 ]; then
            engine stop "$container" >/dev/null && mv -f -- "$backup" "$database" || exit 1
        fi
        start_container || true
    fi
    if [ -n "$temporary" ]; then rm -f -- "$temporary"; fi
    exit "$status"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

for suffix in -wal -shm -journal; do
    if [ -e "$database$suffix" ]; then
        echo '发现 SQLite 日志文件，请先确认所有数据库连接都已关闭。' >&2
        exit 1
    fi
done

backup=$(mktemp "$data_dir/classrooms.before-sync.XXXXXX.sqlite3")
cp -p -- "$database" "$backup"
temporary=$(mktemp "$data_dir/.classrooms.incoming.XXXXXX")
cp -p -- "$database" "$temporary"
cat -- "$snapshot" > "$temporary"
mv -f -- "$temporary" "$database"
installed=1
start_container
printf '课表已同步。原云端课表备份在 %s\n' "$backup"
