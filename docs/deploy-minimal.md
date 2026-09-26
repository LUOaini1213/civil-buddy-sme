# 云部署最小步骤（civil-buddy）

目标：固定 HTTPS 公网地址，不依赖本机 localtunnel。

默认 **不需要** skjolber Java；3D 用 Python bin3d。LLM Key 可选（steps 主路径可无 Key）。

---

## 方案 A · Render（推荐，免费档够演示）

### 0. 准备
- GitHub 已推代码：`https://github.com/LUOaini1213/civil-buddy-sme`
- 仓库根目录有可用 `Dockerfile`（已支持 `$PORT`）

### 1. 创建服务
1. 打开 [https://render.com](https://render.com) 登录（可用 GitHub）
2. **New → Web Service**
3. 连接仓库 `LUOaini1213/civil-buddy-sme`，分支 `main`
4. 设置：
   - **Runtime**: Docker
   - **Region**: 选近的（如 Singapore）
   - **Instance**: Free / Starter
5. **Environment**（可选）：
   | Key | 示例 | 说明 |
   |-----|------|------|
   | `CIVIL_TOKEN` | 长随机串 | **必填**：访问口令；不设则容器拒绝启动 |
   | `PACKING_SKIP_SKJOLBER` | `1` | 默认即可 |
   | `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` | 你的 Key | 不要也可跑 steps |
   | `OPENAI_BASE_URL` | DeepSeek 时填对应 base | 可选 |
6. **Create Web Service** → 等 Build / Deploy 变绿

### 2. 访问
- 公网：`https://<你的服务名>.onrender.com/?token=<口令>`（打开一次即换成 HttpOnly cookie）
- 健康：`https://<你的服务名>.onrender.com/api/health`  
  期望：`gateway: UP`、`agent_count: 13`

### 3. 注意（Free）
- 一段时间无人访问会**休眠**，下次打开要等 30～60 秒冷启动
- 要常亮：升付费档，或用方案 B 小机

---

## 方案 B · 任意 Linux 云主机（2 核 2G 够演示）

```bash
# 1. 装 Docker
curl -fsSL https://get.docker.com | sh

# 2. 拉代码
git clone https://github.com/LUOaini1213/civil-buddy-sme.git
cd civil-buddy-sme

# 3. 访问口令（必填；docker compose 没有它会直接报错）
echo "CIVIL_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" >> .env

# 4. 构建并后台跑
docker compose up -d --build

# 5. 访问 http://<公网IP>:8000/?token=<口令>
# 正式对外请按下面「AWS Lightsail」一节加 HTTPS，不要直接放行 8000

# 6. 自检（脚本用 Bearer 头；/api/health 是公开的存活探针，其余接口没口令一律 401）
curl -s http://127.0.0.1:8000/api/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/tools                                  # 401
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $(sed -n 's/^CIVIL_TOKEN=//p' .env)" \
  http://127.0.0.1:8000/api/tools                                                                       # 200
```

数据：SQLite 库（会话、运行记录、审计、LangGraph 检查点）在卷 `packing_output` 里的 `/app/output/db/civilbuddy.db`（`CB_DB_PATH`），和 `output/` 的运行产物一起保留；`docker compose down` 或重新 `--build` 都不会丢，只有 `docker compose down -v` 会删掉。整套镜像自检是 `docker build -t civil-buddy-gateway:smoke . && bash scripts/docker_smoke.sh`（CI 的 `docker-smoke` 任务每次都跑）。

可选环境变量（写 `.env` 或 `docker compose` 的 environment）：

```env
PACKING_SKIP_SKJOLBER=1
DEEPSEEK_API_KEY=sk-xxx
```

### AWS Lightsail（一台公司服务器 + 员工浏览器）

1. **口令必填**：`CIVIL_TOKEN` 用上面的随机串，只放服务器 `.env`，不进仓库。换口令 = 改值后重启，旧 cookie 会收到 401 并被清掉。**不要**设 `CIVIL_ALLOW_OPEN_LAN`。
2. **只经 HTTPS 进来**：`docker-compose.yml` 的端口改成 `"127.0.0.1:8000:8000"`；Lightsail 防火墙只开 80/443，不开 8000。
3. **前面放 Caddy 或 nginx**。nginx 需要：
   ```nginx
   location / {
       proxy_pass http://127.0.0.1:8000;
       proxy_http_version 1.1;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_set_header Upgrade $http_upgrade;      # /ws/
       proxy_set_header Connection "upgrade";
   }
   ```
   带转发头或走 HTTP/1.0 的请求按"非本机"处理；口令对本机回环也同样必需，所以代理怎么配都不会绕过口令；`X-Forwarded-Proto: https` 会让 cookie 带 `Secure`。不要把 uvicorn 的 `FORWARDED_ALLOW_IPS` 设成 `*`。
4. **员工第一次**打开 `https://<域名>/?token=<口令>`，服务器 303 跳回不带口令的地址并种 HttpOnly、SameSite=Strict 的 cookie；这条链接会在代理日志和浏览器历史里出现一次，别转发。脚本用 `Authorization: Bearer <口令>`。
5. 密钥只放环境变量；服务器仓库根和 `output/` 下不要放 `deepseek api.txt` 之类的文件（`/api/artifact` 只读 `output/`、`PACKING_OUTPUT_DIR` 和 runs 目录）。
6. `PACKING_TMS_MODE` 不设（stub）；请求体里的 `mode` 已不能切到真实 TMS。
7. 容器里只启动网关（镜像带着 `demo/` 的文件，因为网关的专家能力要读 `demo/kb`，但不启动工作台）。要把员工工作台也放上去，用 `CIVIL_HOST=127.0.0.1 CIVIL_TOKEN=<同一口令> python demo/serve.py`，只让同机的代理转发进来（代理和应用在同一台机器上时不要绑 0.0.0.0）；经代理进来的请求一律要口令，没设口令时代理转发的请求全部被拒。直接对外绑 0.0.0.0 而没口令，`serve.py` 拒绝启动。两个应用必须用同一个口令：cookie 同名且不分端口，口令不同会互相清掉对方的 cookie。
8. 应用挂在域名根路径（`location /`）。挂在子路径下时，`?token=` 的 303 会跳回域名根。
9. 没设口令时，不加转发头的 HTTP/1.1 代理（Host 改成 127.0.0.1）和任何 TCP 端口转发（socat、netsh portproxy、`ssh -R`）都会让远程请求看起来像本机：没口令的实例绝不要这样转出去。

---

## 方案 C · Railway（同类，界面更简单）

1. [railway.app](https://railway.app) → New Project → Deploy from GitHub  
2. 选本仓库 → 自动识别 Dockerfile  
3. Variables 同上（Key 可选）  
4. Generate Domain → 得到 `https://xxx.up.railway.app`

---

## 验收清单

- [ ] `GET /api/health` → UP  
- [ ] 打开首页 → 能看到「满载演示」  
- [ ] 点满载 → HITL 确认 → 拼柜有结果  
- [ ] 手机 4G 也能打开（不是局域网）

---

## 不需要做的

- 不必部署 skjolber（可选 3D 服务）  
- 不必再开 localtunnel（云地址即长期入口）  
- 不必把 `.env` / `deepseek api.txt` 提交进 Git  

---

## 本机对照

```bash
# 仍可本机跑
pip install -r requirements.txt
uvicorn gateway.app:app --host 127.0.0.1 --port 8000
```

云上是同一套 `gateway.app:app`，只是 `--host 0.0.0.0` + 公网域名 + `CIVIL_TOKEN`（没口令监听 0.0.0.0 会拒绝启动）。
