# UNIN 部署与更新手册

> 仓库：https://github.com/Guanerdie/LibSeek（**公开仓库**，本文不记录服务器地址、用户名和任何凭据）
> 主线分支：`codex/simplified-mvp`
> 更新日期：2026-10-05（按当天一次真实上线的过程核对过）

---

## 1. 生产环境现状

| 项目 | 值 |
|---|---|
| 代码目录 | `/opt/unin`（git 检出，分支 `codex/simplified-mvp`） |
| 部署方式 | Docker Compose，项目名 `unin`，配置文件 `/opt/unin/compose.yaml` |
| 服务 | `api`（FastAPI，`127.0.0.1:8000`）、`frontend`（Nginx，`127.0.0.1:9527`） |
| 数据库 | SQLite，容器内 `/var/lib/unin/unin.db` |
| 数据卷 | `unin_unin_data`，宿主机路径 `/var/lib/docker/volumes/unin_unin_data/_data` |
| 数据卷内容 | `unin.db`、`auth/`（管理员登录信息）、`integrations/`（页面上保存的连接配置） |
| 环境变量 | `/opt/unin/.env`（不进版本库）；`DATABASE_URL=` 留空表示使用默认的 SQLite |
| 对外入口 | 宿主机 Nginx 反向代理到 `127.0.0.1:9527`，配置见 `deploy/nginx.unin.tlovex.de.conf` |
| 备份目录 | `/opt/unin/backups/` |

要点：

- `api` 容器启动时会先执行 `alembic upgrade head`，**数据库迁移是自动的**，不需要手动跑。
- 服务名是 `api`，不是 `backend`。
- 调度器只支持单个 Uvicorn 进程，不要给 `api` 加 `--workers`。

## 2. 连接服务器

服务器地址和登录信息放在本机的 `~/.ssh/config`（Windows 为 `C:\Users\<用户名>\.ssh\config`），不要写进仓库：

```sshconfig
Host libseek-prod
    HostName <服务器 IP 或域名>
    User <用户名>
    Port <端口>
    IdentityFile <私钥路径>
    IdentitiesOnly yes
```

私钥不要放在项目目录里。下文的命令都在服务器上的 `/opt/unin` 目录执行。

## 3. 更新到新版本

上线前先在本地跑通检查（见 `README.md` 的「本地开发」一节），并把代码推到 `codex/simplified-mvp`。

### 3.1 上线前检查（只读）

```bash
cd /opt/unin
git status -sb                 # 被跟踪的文件应当没有改动
git log --oneline -1           # 记下当前提交，回滚要用
docker compose ps              # 两个服务都应是 healthy
grep '^DATABASE_URL=' .env     # 留空或没有这一行 = SQLite
df -h /var/lib/docker          # 确认磁盘空间
```

### 3.2 保留旧镜像，拉代码，构建

这一步旧服务照常运行。把 `<标签>` 换成本次上线的名字，例如 `pre-cleanup`。

```bash
docker tag unin-api unin-api:<标签>
docker tag unin-frontend unin-frontend:<标签>
git pull --ff-only
docker compose build api frontend
```

必须先打标签再构建：构建会覆盖 `unin-api` 和 `unin-frontend` 这两个镜像名。

### 3.3 停 api，备份数据

从这里开始网站暂时不可用。先停 `api` 再备份，保证 SQLite 文件是一致的。

```bash
docker compose stop api
tar czf backups/unin-data-$(date +%Y%m%d)-<标签>.tgz \
  --exclude='unin.db.backup-*' \
  -C /var/lib/docker/volumes/unin_unin_data/_data .
ls -lh backups/
```

**确认备份文件存在且大小不为 0 再继续。**

### 3.4 启动新版本

```bash
docker compose up -d
docker compose ps              # 等两个服务都变成 healthy
```

### 3.5 上线后核对

```bash
docker compose exec api alembic current        # 应为本次的最新迁移版本，带 (head)
docker compose logs api --tail 50              # 没有 Traceback / ERROR
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/health   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9527/             # 200
```

然后在浏览器里登录，确认列表能加载，本次改动涉及的页面能正常使用。

## 4. 回滚

顺序是**先恢复数据库，再换回旧代码**。新代码的迁移已经改过表结构，旧代码不能直接用新数据库。

```bash
cd /opt/unin
docker compose stop
rm -f /var/lib/docker/volumes/unin_unin_data/_data/unin.db*
tar xzf backups/<备份文件>.tgz -C /var/lib/docker/volumes/unin_unin_data/_data
git reset --hard <上线前的提交>
docker tag unin-api:<标签> unin-api
docker tag unin-frontend:<标签> unin-frontend
docker compose up -d
```

注意：

- 这会用备份覆盖当前数据库，上线之后产生的数据会丢失。
- `rm -f .../unin.db*` 会连同数据卷里名为 `unin.db.backup-*` 的旧备份一起删掉。需要保留的话先把它们移到 `backups/`。
- 用旧镜像启动时不要加 `--build`。

## 5. 空间清理功能的启用节奏

2026-10-05 上线（提交 `f93b949`，迁移 `20260923_0025`）。真正删除需要三项同时满足：

1. `.env` 中 `ENABLE_QB_WRITE=true`
2. `.env` 中 `ENABLE_QB_DELETE=true`
3. 自动化页「空间清理」里打开「启用空间清理」，并关闭「演练模式」

启用步骤：

1. **演练一周**：不设置 `ENABLE_QB_DELETE`，在页面上启用清理并保持演练模式，用「查看预览」确认它打算删的内容合理。
2. **小量开启**：在 `.env` 中加 `ENABLE_QB_DELETE=true`，执行 `docker compose up -d api` 使其生效；页面上把「每日最多清理」设为 3，关闭演练模式。
3. **恢复正常**：稳定后把每日上限改回 20。

已知行为：

- 升级时已经存在的下载全部是 `HELD`，不会被清理，也不会出现在预览里。只有升级后新提交的下载才会进入清理流程。
- 清理分两阶段：先打 `unin-cleanup` 标签，观察期结束、且标签还在，才连同文件一起删除。在 qBittorrent 里摘掉这个标签，该种子就永久保留。
- 每轮清理开始前会先同步一次 NextFind，重新确认入库状态。同步失败或列表不完整时，这一轮什么都不删，并在操作记录里留下「本轮清理已跳过」。没有待处理的种子时不会同步。
- 「下载」页可以把已完成的下载在「保留不清理」和「允许自动清理」之间切换。已标记待清理的种子要在 qBittorrent 里摘标签来保留。`DELETED` 无法改回。
- 清理任务默认每 6 小时检查一次，由 `.env` 的 `CLEANUP_INTERVAL_MINUTES` 控制（单位分钟）。只在测试时调小，测完删掉这一行并执行 `docker compose up -d api` 恢复默认。

### 操作记录

「记录」页列出每一次下载、删种、入库确认和策略修改：时间、操作人、手动还是自动、原因，以及体积、做种天数等数据。

- 操作人是登录用户名；系统自己执行的显示为「定时调度」「空间清理」或「系统」。
- 2026-10-05 之前写入的记录没有操作人和方式，显示为「未记录」。
- 记录按 `HISTORY_RETENTION_DAYS` 定期清理，但「删除种子和文件」的记录永久保留。

查看各状态的下载数量：

```bash
docker compose exec api python -c "import sqlite3;c=sqlite3.connect('file:/var/lib/unin/unin.db?mode=ro',uri=True);print(c.execute('select cleanup_state,count(*) from downloads group by 1').fetchall())"
```

## 6. 日常维护

```bash
docker compose ps                         # 服务状态
docker compose logs -f api                # 实时日志
docker compose logs api --tail 100        # 最近 100 行
docker compose exec api printenv ENABLE_QB_WRITE ENABLE_QB_DELETE   # 生效中的开关
```

修改 `.env` 之后要重新创建容器才生效：

```bash
docker compose up -d api
```

历史记录（自动化运行、搜索结果、活动日志）由应用按 `HISTORY_RETENTION_DAYS` 自动清理，不需要手动删数据库里的行。

`backups/` 和数据卷里的旧备份不会自动清理，确认不再需要后手动删除。

## 7. 首次部署

```bash
cd /opt
git clone -b codex/simplified-mvp https://github.com/Guanerdie/LibSeek.git unin
cd unin
cp .env.example .env        # 按注释填写；各类连接也可以启动后在「设置」页保存
docker compose up --build -d
```

- 首次打开页面时创建本地管理员。
- 宿主机 Nginx 参考 `deploy/nginx.unin.tlovex.de.conf`，证书用 certbot 申请。
- 部署并检查完成后，再设置 `AUTOMATION_SCHEDULER_ENABLED=true` 打开调度器；自动化策略还需要在页面上单独启用。
- 需要 PostgreSQL 时叠加 `deploy/compose.postgres.yaml`，见 `README.md`。上文的备份和回滚步骤只适用于 SQLite。

## 8. 安全注意事项

- 仓库是公开的。服务器地址、用户名、密码、token、私钥都不要写进任何会被提交的文件。仓库已配置 gitleaks（`.gitleaks.toml`、`.pre-commit-config.yaml`）。
- 生产服务器的 IP 曾被提交进 git 历史，应视为已公开：禁用密码登录，只允许密钥；建议安装 fail2ban，并改用非 root 用户登录。
- `api` 和 `frontend` 只监听 `127.0.0.1`，对外只经过宿主机 Nginx 的 443 端口。

## 9. 上线检查清单

- [ ] 本地测试全部通过，代码已推到 `codex/simplified-mvp`
- [ ] 记下服务器当前的提交
- [ ] 旧镜像已打标签
- [ ] `api` 已停止，数据卷已备份，备份文件大小正常
- [ ] 新版本启动后两个服务都是 healthy
- [ ] `alembic current` 是预期版本
- [ ] 日志没有报错，健康检查返回 200
- [ ] 浏览器里登录、列表、本次改动的页面都正常
