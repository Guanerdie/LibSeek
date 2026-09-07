# LibSeek 部署与维护指南

> 项目GitHub: https://github.com/Guanerdie/LibSeek  
> 生产服务器: 见本地 `~/.ssh/config` 的 `libseek-prod` 别名（本仓库公开，不记录真实地址）  
> 更新日期: 2026-09-02

---

## 服务器信息

**服务器地址**: 不写在仓库里。本仓库是公开的，公开 SSH 目标会被扫描器直接拿去爆破。
真实地址放在本地 `~/.ssh/config`（Windows 为 `C:\Users\<用户名>\.ssh\config`）：

```sshconfig
Host libseek-prod
    HostName <生产服务器 IP 或域名>
    User <用户名>
    IdentityFile ~/.ssh/libseek_deploy
```

**SSH登录**: 私钥存放在本地 `~/.ssh`，不要放进项目目录。
**连接方式**:
```bash
ssh libseek-prod
```

> 下文所有 `<PROD_HOST>` 占位符，按需替换为你自己的地址或直接用上面的 Host 别名。

---

## 当前部署架构

### 推测的部署方式

基于项目结构，生产环境应该采用以下方式之一：

#### 方式1: Docker Compose（推荐）
```bash
# 项目目录结构
/opt/libseek/
├── compose.yaml
├── .env
├── backend/
├── frontend/
└── data/          # SQLite数据库或持久化卷
```

#### 方式2: 直接运行
```bash
# 后端
cd /opt/libseek/backend
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 前端
cd /opt/libseek/frontend
npm run build
# 使用Nginx托管dist/
```

---

## 部署流程

### 初次部署

#### 1. 服务器环境准备

```bash
# 登录服务器
ssh libseek-prod

# 安装Docker和Docker Compose（如果用方式1）
sudo apt update
sudo apt install -y docker.io docker-compose-plugin

# 克隆项目
cd /opt
sudo git clone https://github.com/Guanerdie/LibSeek.git libseek
cd libseek

# 配置环境变量
sudo cp .env.example .env
sudo nano .env
```

#### 2. 配置 .env 文件

关键配置项：
```ini
# NextFind（缺失来源）
NEXTFIND_USERNAME=your_username
NEXTFIND_PASSWORD=your_password

# TMDB（影视元数据）
TMDB_API_KEY=your_tmdb_api_key

# PT站点（至少配置一个）
AVISTAZ_API_KEY=your_avistaz_key
# 或
NEXUSPHP_SITES='[{"name":"站点名","base_url":"https://...","username":"...","passkey":"..."}]'

# qBittorrent（下载器）
QBITTORRENT_BASE_URL=http://your-qb-server:8080
QBITTORRENT_USERNAME=admin
QBITTORRENT_PASSWORD=adminadmin
ENABLE_QB_WRITE=true  # 允许提交下载

# 认证（生产环境必须修改）
AUTH_USERNAME=admin
AUTH_PASSWORD=your_secure_password
AUTH_SESSION_SIGNING_KEY=your_random_secret_key

# 自动化调度
AUTOMATION_SCHEDULER_ENABLED=true
AUTOMATION_SCHEDULER_CRON=0 3 * * *  # 每天凌晨3点运行
```

#### 3. 启动服务

**方式1: Docker Compose**
```bash
sudo docker compose up -d

# 查看日志
sudo docker compose logs -f

# 检查运行状态
sudo docker compose ps
```

**方式2: 直接运行**
```bash
# 后端
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 &

# 前端
cd ../frontend
npm install
npm run build
sudo cp -r dist/* /var/www/libseek/
```

#### 4. 配置反向代理（Nginx）

```nginx
# /etc/nginx/sites-available/libseek
server {
    listen 80;
    server_name <PROD_HOST>;  # 生产服务器 IP 或域名

    # 前端
    location / {
        root /var/www/libseek;
        try_files $uri $uri/ /index.html;
    }

    # 后端API
    location /api {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/libseek /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### 5. 配置HTTPS（可选但推荐）

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

---

## 日常维护

### 更新代码

```bash
# 登录服务器
ssh libseek-prod
cd /opt/libseek

# 拉取最新代码
sudo git fetch origin
sudo git pull origin main

# 如果有数据库迁移
cd backend
sudo docker compose exec backend uv run alembic upgrade head
# 或直接运行方式
uv run alembic upgrade head

# 重启服务
sudo docker compose down
sudo docker compose up -d --build

# 查看启动日志
sudo docker compose logs -f backend
```

### 应用本次搜索冷却优化

```bash
cd /opt/libseek

# 1. 拉取最新代码（包含迁移文件）
sudo git pull origin main

# 2. 应用数据库迁移
sudo docker compose exec backend uv run alembic upgrade head
# 预期输出：
# INFO  [alembic.runtime.migration] Running upgrade ... -> 20260902_0013

# 3. 重启服务（应用新逻辑）
sudo docker compose restart backend

# 4. 验证迁移成功
sudo docker compose exec backend uv run alembic current
# 应该显示：20260902_0013 (head)

# 5. 检查日志（观察首次自动化运行）
sudo docker compose logs -f backend | grep "search_cooldown\|next_search_at"
```

### 回滚到上一个版本

```bash
cd /opt/libseek

# 1. 回滚代码
sudo git log --oneline -5  # 查看最近提交
sudo git reset --hard <commit-hash>

# 2. 回滚数据库
sudo docker compose exec backend uv run alembic downgrade -1

# 3. 重启服务
sudo docker compose restart
```

### 查看日志

```bash
# 实时日志
sudo docker compose logs -f

# 只看后端
sudo docker compose logs -f backend

# 最近100行
sudo docker compose logs --tail=100 backend

# 查看错误日志
sudo docker compose logs backend | grep ERROR
```

### 备份数据库

```bash
# SQLite数据库通常在
sudo docker compose exec backend ls -la /app/data/

# 备份
sudo docker compose exec backend cp /app/data/libseek.db /app/data/backup-$(date +%Y%m%d).db

# 或从宿主机复制
sudo docker cp libseek-backend-1:/app/data/libseek.db ./backup-$(date +%Y%m%d).db
```

### 清理旧数据

```bash
# 进入后端容器
sudo docker compose exec backend bash

# 连接数据库
sqlite3 /app/data/libseek.db

# 清理90天前的活动日志
DELETE FROM activity_log WHERE created_at < datetime('now', '-90 days');

# 清理已完成的下载记录（保留种子数据）
DELETE FROM download WHERE state = 'COMPLETED' AND updated_at < datetime('now', '-30 days');

# 退出
.quit
exit
```

---

## 监控和告警

### 健康检查

```bash
# 检查服务状态
curl http://<PROD_HOST>/api/health

# 预期响应
{"status":"ok","version":"0.9.0"}
```

### 关键指标

**需要监控的指标**：
1. **自动化成功率**
   ```sql
   SELECT 
     COUNT(*) FILTER (WHERE state = 'SUCCEEDED') * 100.0 / COUNT(*) as success_rate
   FROM automation_run
   WHERE created_at > datetime('now', '-7 days');
   ```

2. **搜索冷却分布**
   ```sql
   SELECT 
     search_miss_count,
     COUNT(*) as count,
     AVG((julianday(next_search_at) - julianday('now')) * 24) as avg_hours_until_next
   FROM library_media
   WHERE next_search_at IS NOT NULL
   GROUP BY search_miss_count;
   ```

3. **下载队列积压**
   ```sql
   SELECT state, COUNT(*) 
   FROM download 
   WHERE state IN ('QUEUED', 'DOWNLOADING')
   GROUP BY state;
   ```

### 告警脚本（可选）

```bash
#!/bin/bash
# /opt/libseek/monitor.sh

# 检查后端是否响应
if ! curl -sf http://localhost:8000/api/health > /dev/null; then
    echo "Backend is down!" | mail -s "LibSeek Alert" admin@example.com
fi

# 检查是否有失败的自动化运行
FAILED=$(sudo docker compose exec -T backend sqlite3 /app/data/libseek.db \
  "SELECT COUNT(*) FROM automation_run WHERE state='FAILED' AND created_at > datetime('now','-1 hour');")

if [ "$FAILED" -gt 0 ]; then
    echo "Found $FAILED failed automation runs in the last hour" | \
      mail -s "LibSeek Automation Failed" admin@example.com
fi
```

添加到crontab:
```bash
crontab -e
# 每10分钟检查一次
*/10 * * * * /opt/libseek/monitor.sh
```

---

## 性能优化（生产环境）

### 1. 数据库优化

**迁移到PostgreSQL**（处理大数据量时）
```yaml
# compose.yaml 添加
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: libseek
      POSTGRES_USER: libseek
      POSTGRES_PASSWORD: secure_password
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

修改 `.env`:
```ini
DATABASE_URL=postgresql+asyncpg://libseek:secure_password@postgres:5432/libseek
```

### 2. 增加工作进程

```yaml
# compose.yaml
services:
  backend:
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 3. 调整自动化并发

```ini
# .env
AUTOMATION_MAX_PARALLEL_JOBS=10  # 根据服务器性能调整
```

---

## 故障排查

### 常见问题

#### 1. 自动化不运行

**检查**：
```bash
# 查看配置
sudo docker compose exec backend env | grep AUTOMATION

# 查看日志
sudo docker compose logs backend | grep "scheduler"
```

**解决**：
```ini
# .env 中确保
AUTOMATION_SCHEDULER_ENABLED=true
```

#### 2. PT站搜索失败

**检查**：
```bash
# 查看最近的搜索错误
sudo docker compose exec backend sqlite3 /app/data/libseek.db \
  "SELECT * FROM activity_log WHERE action LIKE '%search%' AND level='ERROR' ORDER BY created_at DESC LIMIT 10;"
```

**可能原因**：
- API Key过期
- 站点Cookie失效（NexusPHP站点）
- 触发反爬限制

#### 3. 下载提交失败

**检查**：
```bash
# 测试qBittorrent连接
curl -u admin:password http://your-qb-server:8080/api/v2/app/version
```

**解决**：
```ini
# .env 中确认
ENABLE_QB_WRITE=true
QBITTORRENT_BASE_URL=http://correct-address:8080
```

#### 4. 内存占用过高

**检查**：
```bash
# 查看容器资源使用
sudo docker stats

# 限制内存
sudo docker compose down
```

修改 `compose.yaml`:
```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          memory: 512M
```

---

## 安全建议

### 1. 防火墙配置

```bash
# 只开放必要端口
sudo ufw allow 22/tcp   # SSH
sudo ufw allow 80/tcp   # HTTP
sudo ufw allow 443/tcp  # HTTPS
sudo ufw enable

# 限制SSH登录IP（如果有固定IP）
sudo ufw allow from YOUR_IP to any port 22
```

### 2. 定期更新密码

```bash
# 修改认证密码
sudo nano /opt/libseek/.env
# 修改 AUTH_PASSWORD 和 AUTH_SESSION_SIGNING_KEY

# 重启服务
sudo docker compose restart backend
```

### 3. 日志审计

```bash
# 查看登录日志
sudo docker compose logs backend | grep "auth_login"

# 查看异常API调用
sudo docker compose logs backend | grep "401\|403"
```

---

## 版本发布检查清单

每次更新生产环境前：

- [ ] 在本地/测试环境验证功能
- [ ] 备份生产数据库
- [ ] 查看Git提交日志，确认改动范围
- [ ] 检查是否有数据库迁移（`backend/alembic/versions/`）
- [ ] 预估停机时间（通常<1分钟）
- [ ] 准备回滚方案（记录当前commit hash）
- [ ] 更新后验证核心功能：
  - [ ] 用户登录
  - [ ] 影视列表加载
  - [ ] 搜索资源
  - [ ] 自动化运行（如果在运行时间）
- [ ] 观察日志5分钟，确保无错误

---

## 相关链接

- **项目主页**: https://github.com/Guanerdie/LibSeek
- **问题反馈**: https://github.com/Guanerdie/LibSeek/issues
- **FastAPI文档**: https://fastapi.tiangolo.com/
- **Vue 3文档**: https://vuejs.org/
- **TMDB API**: https://developers.themoviedb.org/
- **qBittorrent WebAPI**: https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API

---

## 联系信息

**维护者**: [从项目README中获取]  
**技术支持**: [设置Issue或讨论区]

---

**最后更新**: 2026-09-02  
**文档版本**: 1.0
