# LockBot Docker 打包流程

本文档记录把 LockBot 打成 Docker 镜像、导出离线包、组装部署包（`deploy-bundle/`）的完整流程，方便后续重复打包与版本升级。

> 当前版本：`2.5.5`（见 [pyproject.toml](../pyproject.toml) 的 `version`）。下文凡出现 `2.5.5` 处，升级时统一替换为新版本号。

---

## 概述

镜像由 [docker/Dockerfile](../docker/Dockerfile) 多阶段构建：

1. **Stage 1（frontend-builder，node:20-alpine）**：`npm ci` + `npm run build` 编译 Vue3 前端，产出 `dist/`。
2. **Stage 2（python:3.10-slim）**：`pip install -e .` 安装后端依赖，拷入 Stage 1 的 `dist/` 作为 Platform 模式静态资源。

最终镜像默认以 Platform 模式启动：`uvicorn lockbot.backend.app.main:app --host 0.0.0.0 --port 8000`（`EXPOSE 8000`）。

**构建上下文是仓库根目录**（不是 `docker/`），因为 Dockerfile 里引用的是 `frontend/`、`pyproject.toml`、`python/`、`README.md`、`LICENSE`、`tools/` 等根目录相对路径。构建时用 `-f docker/Dockerfile` 指定 Dockerfile，最后的 `.` 表示上下文为根目录。

---

## 前置条件

- 打包机装好 **Docker**（含 buildx 即可，普通 `docker build` 也行）。
- 在**仓库根目录**执行所有命令：`cd /home/users/liujie63/lock_bot`。
- [.dockerignore](../.dockerignore) 已排除 `data/`、`*.env`、`frontend/node_modules`、`tests/`、`.git` 等，避免运行时数据与密钥泄进镜像，无需手动清理。

---

## 一、构建镜像

```bash
cd /home/users/liujie63/lock_bot

docker build -f docker/Dockerfile -t lockbot:2.5.5 .
```

构建完成后确认：

```bash
docker images lockbot      # 应看到 lockbot  2.5.5
```

### 内网/弱网环境用镜像源加速

Dockerfile 暴露了两个 `ARG`，可在构建时覆盖默认源（默认是官方源 `registry.npmjs.org` / `pypi.org`）：

```bash
docker build -f docker/Dockerfile -t lockbot:2.5.5 \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  .
```

> 上面是公共镜像示例；公司内网请替换为内部的 npm / pypi 源地址。

---

## 二、本地验证（可选，推荐）

导出前先在打包机起一份，确认镜像能正常跑：

```bash
mkdir -p /tmp/lockbot-data
docker run -d --name lockbot-test \
  -p 8000:8000 \
  -e DATA_DIR=/data -e DEV_MODE=false \
  -e JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_hex(32))") \
  -e ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())") \
  -v /tmp/lockbot-data:/data \
  lockbot:2.5.5

docker logs -f lockbot-test          # 看启动日志
curl -sf http://localhost:8000/ >/dev/null && echo OK   # 健康探活

# 验证完清理
docker rm -f lockbot-test && rm -rf /tmp/lockbot-data
```

---

## 三、导出离线镜像包

目标机器通常不联网拉镜像，所以把镜像 `save` 成压缩 tar 包传过去。**实际部署包用的是 gzip 压缩的 `.tar.gz`**：

```bash
docker save lockbot:2.5.5 | gzip > deploy-bundle/lockbot-2.5.5.tar.gz

ls -lh deploy-bundle/lockbot-2.5.5.tar.gz    # 约 70+ MB
```

> ⚠️ 仓库里的 [tools/save_image.sh](../tools/save_image.sh) **与当前产物不一致**：它用的是 `ghcr.io/dynamicheart/lockbot` 标签且导出未压缩 `.tar`，而真实部署包是 `lockbot:2.5.5` + `.tar.gz`。请以本文档的 `docker save ... | gzip` 命令为准，不要直接跑那个脚本。

---

## 四、组装 deploy-bundle

部署包目录 [deploy-bundle/](../deploy-bundle/) 需包含 4 个文件，拷给目标机器后开箱即用：

| 文件 | 来源 / 作用 |
| --- | --- |
| `lockbot-2.5.5.tar.gz` | 第三步 `docker save` 产出的离线镜像 |
| `docker-compose.yml` | 启动编排（端口 8000、自动重启、`./data` 持久化）。**`image:` 标签需与镜像版本一致** |
| `lockbot.env.example` | 配置模板，目标机拷成 `lockbot.env` 后填密钥 |
| `README.md` | 目标机部署步骤说明 |

`deploy-bundle/docker-compose.yml` 关键内容（升级版本时改 `image` 那一行）：

```yaml
services:
  lockbot:
    image: lockbot:2.5.5        # ← 与导出的镜像标签一致
    container_name: lockbot
    restart: unless-stopped
    ports:
      - "8000:8000"
    env_file:
      - ./lockbot.env
    volumes:
      - ./data:/data            # 数据持久化：SQLite + 各 bot 状态
    environment:
      DATA_DIR: /data
    healthcheck: ...            # 30s 探活 http://localhost:8000/
```

把整个 `deploy-bundle/` 打包传输：

```bash
tar czf lockbot-deploy-bundle-2.5.5.tar.gz deploy-bundle/
# scp / rsync / U盘 传到目标机器
```

---

## 五、目标机器部署（速查）

目标机只需装 Docker。完整步骤见 [deploy-bundle/README.md](../deploy-bundle/README.md)，核心三步：

```bash
cd deploy-bundle

# 1. 导入镜像（docker load 可直接读 .tar.gz）
docker load -i lockbot-2.5.5.tar.gz

# 2. 准备配置（务必生成独立 JWT_SECRET / ENCRYPTION_KEY）
cp lockbot.env.example lockbot.env && chmod 600 lockbot.env
#   编辑 lockbot.env 填入两个密钥（生成命令见文件内注释）

# 3. 启动
docker compose up -d
docker compose logs -f
```

首次启动后库是空的，建第一个超管：

```bash
docker compose exec lockbot \
  python3 tools/create_super_admin.py \
  --username superadmin --email admin@local --password '强密码'
```

浏览器访问 `http://<目标机IP>:8000` 登录。

> **密钥提醒**：`ENCRYPTION_KEY` 一旦设定不可再改（改了已加密的 bot 凭据全部无法解密）；`lockbot.env` 权限 600，勿提交 git、勿发群。

---

## 六、版本升级清单

每次发新版本，按顺序改这几处，保持版本号一致，避免 tag 对不上：

- [ ] [pyproject.toml](../pyproject.toml) `version` 改成新版本号
- [ ] `docker build -t lockbot:<新版本>` 重新构建
- [ ] `docker save lockbot:<新版本> | gzip > deploy-bundle/lockbot-<新版本>.tar.gz`，删掉旧的 `.tar.gz`
- [ ] [deploy-bundle/docker-compose.yml](../deploy-bundle/docker-compose.yml) 的 `image:` 改成新版本
- [ ] [deploy-bundle/README.md](../deploy-bundle/README.md) 里所有 `lockbot-2.5.5.tar.gz` / `lockbot:2.5.5` 字样替换
- [ ] （如同步内网仓库）[docker/docker-compose.yml](../docker/docker-compose.yml) 也对应更新

---

## 附：常用运维命令

```bash
docker compose restart                          # 重启
docker compose down                             # 停止（数据保留在 ./data）
docker compose logs -f                          # 看日志

# 升级：先 load 新镜像，改 compose 的 image 标签，再
docker compose up -d

# 数据备份（全部数据都在挂载的 data 目录）
tar czf lockbot-data-$(date +%F).tar.gz data/
```
