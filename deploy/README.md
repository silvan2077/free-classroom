# Linux 云服务器部署

应用在 Docker Compose 中运行，默认将 `data/classrooms.sqlite3` 挂载到容器并通过服务器的 `8000` 端口提供网页。云端按 `Asia/Shanghai` 计算教学周和星期。若只通过 SSH 隧道访问，把 `compose.yaml` 的端口绑定地址设为 `127.0.0.1`，然后用 Compose 启动。

## 1. 准备服务器并上传代码

使用可以登录的 Linux 云服务器。安装 Docker Engine 和 Compose 插件，然后在服务器上运行 `docker version` 和 `docker compose version`。下面的命令假设当前 SSH 用户可以运行 Docker。Docker 文档说明了本机端口发布在 Engine 28.0.0 之前的限制，部署时使用当前受支持的版本。 [Docker 安装说明](https://docs.docker.com/engine/install/) · [端口发布说明](https://docs.docker.com/engine/network/port-publishing/)

在 Windows 项目目录打开 PowerShell，执行以下命令。将 `user@server.example` 替换成云服务器的 SSH 用户名和域名或 IP。

```powershell
ssh user@server.example 'install -d -m 700 ~/classroom ~/classroom/data'
tar.exe -czf "$env:TEMP\classroom-app.tar.gz" .dockerignore app.py timetable.py requirements.txt templates static deploy
scp "$env:TEMP\classroom-app.tar.gz" user@server.example:classroom/
```

如果要沿用现有课表，先退出 Windows 上运行的本机网页服务，再将数据库上传到服务器的暂存路径。首次部署可在启动前移入数据目录。如果服务器已经有课表，先按下方备份步骤保存一份。

```powershell
scp '.\data\classrooms.sqlite3' user@server.example:classroom/classrooms.incoming.sqlite3
```

## 2. 在服务器启动

全新 Ubuntu 服务器可以先解压程序包，再在项目根目录执行 `sh deploy/bootstrap-cloud.sh`。脚本会按需安装 Docker Engine 和 Compose、创建数据目录、构建容器并等待健康检查。首次部署时，可以在命令末尾加上已上传的 SQLite 快照路径。此方式使用 `classroom-bootstrap` 项目名，后续管理时在 Compose 命令中加入 `-p classroom-bootstrap`，运行更新或同步脚本前在项目根目录的 `.env` 中设置 `COMPOSE_PROJECT_NAME=classroom-bootstrap`。

已安装 Docker Engine 和 Compose 插件时，可以在项目根目录运行 `sh deploy/deploy-cloud.sh`。脚本会将应用端口绑定到 `0.0.0.0`、构建镜像并等待健康检查。个人使用时可将云安全组的入站来源限制为自己的公网 IP。手动运行下文的 `docker compose` 命令时，请从项目根目录执行，并将 `docker compose` 写成 `docker compose --project-directory . -f deploy/compose.yaml`。

登录服务器后，执行以下命令。用当前登录用户的 UID 和 GID 运行应用，使容器能写入该用户管理的 `data/` 目录。

```sh
cd ~/classroom
tar -xzf classroom-app.tar.gz
install -d -m 700 data
printf 'CLASSROOM_UID=%s\nCLASSROOM_GID=%s\n' "$(id -u)" "$(id -g)" > .env
```

如果刚上传了 Windows 数据库并且这是首次部署，执行下列命令。新建课表可直接进入启动步骤，应用会创建数据库。

```sh
install -m 600 classrooms.incoming.sqlite3 data/classrooms.sqlite3
```

启动应用并检查状态。

```sh
docker compose config -q
docker compose up -d --build --wait
docker compose ps
curl -fsS http://127.0.0.1:8000/api/state
```

如果构建或启动失败，用 `docker compose logs --tail=100 app` 查看日志。若服务器的 `8000` 端口已被占用，在 `.env` 里添加 `CLASSROOM_PORT=8001`，启动后将下文的服务器端口改为 `8001`。

Gunicorn 使用一个工作进程和四个线程，导入预览和确认请求可以访问同一份暂存数据。容器重启后，已导入的课表继续从挂载的 SQLite 文件读取。尚未确认的导入需要重新预览。 [Gunicorn 应用和进程设置](https://gunicorn.org/reference/settings/)

## 3. 远程访问

在自己的电脑上建立 SSH 隧道，使用期间保留这个终端窗口。示例使用本机 `8080` 转发到服务器 `8000`。

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8080:127.0.0.1:8000 user@server.example
```

打开 [远程课表](http://127.0.0.1:8080/)。云服务器安全组只需允许自己连接 SSH。如果 SSH 使用自定义端口，在命令中加上 `-p 端口号`。

### 可选的域名入口

如果需要直接用域名在手机或其他设备访问，可在服务器安装 Caddy，让域名解析到该服务器，并开放 TCP 80 和 443。应用端口保持绑定在服务器回环地址。使用 `caddy hash-password` 交互生成密码哈希，然后将下面的站点配置加入 `/etc/caddy/Caddyfile`。把示例域名和 `REPLACE_WITH_PASSWORD_HASH` 替换成实际值。此配置要求浏览器先认证，适用于查询、导入和学期设置等所有路径。 [Caddy 安装说明](https://caddyserver.com/docs/install) · [自动 HTTPS](https://caddyserver.com/docs/automatic-https) · [密码认证](https://caddyserver.com/docs/caddyfile/directives/basic_auth) · [反向代理](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)

```caddyfile
classroom.example.com {
    basic_auth {
        classroom REPLACE_WITH_PASSWORD_HASH
    }
    reverse_proxy 127.0.0.1:8000
}
```

```sh
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

用浏览器访问 `https://你的域名`，确认先要求密码，登录后再检查查询和导入页面。

## 4. 更新、备份和恢复

也可以用以下命令生成更新包，与 `update-cloud.sh` 一起上传，然后运行 `sh update-cloud.sh '/现有项目目录'`。更新沿用目标目录中的 `compose.yaml` 和数据挂载。

```powershell
tar.exe -czf deploy/classroom-cloud-update.tar.gz app.py timetable.py requirements.txt .dockerignore deploy/Dockerfile deploy/requirements.txt templates/index.html static/app.js static/style.css
```

数据库快照可以与 `sync-cloud-data.sh` 放在同一目录，命名为 `classrooms.incoming.sqlite3`，再运行 `sh sync-cloud-data.sh '/现有项目目录'`。同步会备份并替换云端课表，然后启动原容器。

更新时重新打包和上传源码，在服务器项目目录解压，然后运行 `docker compose up -d --build --wait`。数据库保存在挂载目录，随更新一起保留。

备份前暂停应用，完成复制后再启动。

```sh
cd ~/classroom
install -d -m 700 backups
docker compose stop app
install -m 600 data/classrooms.sqlite3 "backups/classrooms-$(date +%Y%m%d-%H%M%S).sqlite3"
docker compose up -d --wait
```

恢复时先备份当前数据库，再停应用，用备份文件替换 `data/classrooms.sqlite3`，重新运行 `docker compose up -d --wait`。启动后在网页中核对学期、教室和课表。
