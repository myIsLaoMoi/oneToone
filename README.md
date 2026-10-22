# 文件快传

两台设备之间直接传文件。优先点对点直连（文件不经服务器），直连不可用时自动改用服务器中转，两条路径都支持断线续传，并用 SHA-256 校验完整性。

- 网页版：浏览器打开即用，手机、电脑通用
- 安卓应用：Capacitor 套壳，装好后填服务器地址即可
- 无需注册：靠 6 位验证码或分享链接建立一次传输

## 它怎么工作

```
发送方                          服务器                        接收方
  │ POST /api/transfers           │                              │
  │  {files:[{name,size,mime}]}   │                              │
  │← {code, token, sender_key}    │                              │
  │                               │  POST /api/transfers/join    │
  │                               │  {code} → {receiver_key}     │←
  │ WS /ws/{token}?role=sender    │  WS /ws/{token}?role=receiver│
  │─────────── 转发 SDP / ICE（几百字节）──────────────────────→│
  │                                                              │
  │══════════ DataChannel 直传文件字节（不经服务器）═════════════→│
  │                                                              │
  │  ✗ 直连失败或中途断开 → 重连续传 → 重试耗尽后转中继           │
  │ PUT /api/transfers/{t}/chunk?idx=&offset=   (4MB 分块追加)   │
  │                               │  GET /d/{t}/{idx}  (Range)   │←
```

关键点：

- **直连优先**。WebRTC DataChannel 在纯 HTTP 页面里也能用，所以不强制 HTTPS。
- **断线续传**。接收方记录已完整落盘的字节偏移，重连后上报，发送方从该偏移继续，半块丢弃。中继模式同理（按 4MB 对齐）。
- **完整性校验**。发送前在 Web Worker 里流式计算 SHA-256 提交给服务端，接收方算完比对，不一致标记失败。
- **对方离线也能取**。选「先传到服务器」时文件立即上传，接收方凭验证码随时下载。

## 目录结构

```
server/            FastAPI 后端
  app/config.py      全部可调参数（环境变量）
  app/db.py          SQLite（WAL），表 transfer / file
  app/codes.py       验证码、token、密钥生成
  app/storage.py     分块落盘、配额统计、过期清理
  app/ratelimit.py   验证码尝试限流
  app/api/transfers.py  创建 / 加入 / 分块上传 / 进度 / 转中继 / 删除
  app/api/download.py   Range 下载
  app/api/ws.py         WebSocket 信令转发
  tests/             pytest（18 项）

web/               Vue 3 + Vite 前端
  src/lib/p2p.js        DataChannel 帧协议、流控、续传
  src/lib/relay.js      分块上传 / 下载
  src/lib/session.js    模式选择、降级、重连编排
  src/lib/hash.js       哈希 Worker 封装
  src/lib/hashWorker.js 流式 SHA-256
  src/components/       发送 / 接收 / 设置界面

mobile/            Capacitor 安卓壳
deploy/            systemd 单元
```

## 本地运行

```bash
# 后端
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --port 8791 --reload

# 前端（另开终端）
cd web
npm install
npm run dev          # 5173，/api、/d、/ws 自动代理到 8791
```

生产环境用 `npm run build` 生成 `web/dist`，由后端直接托管（见下方 `WEB_DIR`）。

## 测试

```bash
cd server && .venv/bin/python -m pytest tests -q
```

覆盖：创建/加入、验证码复用与限流、密钥作用域、偏移连续性、续传截断旧字节、文件大小与数量上限、配额、Range 请求（206 与 Content-Range）、转中继、进度上报、删除权限、文件名路径穿越、WebSocket 信令转发。

## 配置

全部通过环境变量，定义在 `server/app/config.py`：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATA_DIR` | `server/data` | 数据库与文件存放目录 |
| `WEB_DIR` | `server/../web/dist` | 前端构建产物目录 |
| `MAX_FILE_SIZE` | 2GB | 单文件上限 |
| `MAX_FILES` | 20 | 单次传输文件数上限 |
| `QUOTA_BYTES` | 10GB | 服务器总存储配额 |
| `CODE_TTL_MIN` | 30 | 验证码生成时的冲突检测窗口（验证码本身在保留期内均可加入） |
| `RETENTION_HOURS` | 24 | 文件保留时长，到期自动清理 |
| `CLEANUP_INTERVAL_SEC` | 300 | 清理任务间隔 |
| `JOIN_LIMIT_PER_MIN` | 10 | 每 IP 每分钟验证码尝试上限 |
| `JOIN_LIMIT_PER_HOUR` | 60 | 每 IP 每小时上限 |
| `STUN_URLS` | Google / Cloudflare / QQ | 逗号分隔的 STUN 列表 |
| `P2P_TIMEOUT_MS` | 8000 | 直连建立超时 |
| `P2P_CHUNK_SIZE` | 16KB | 直连分片大小 |
| `RELAY_CHUNK_SIZE` | 4MB | 中继分块大小 |
| `CORS_ORIGINS` | `http://localhost` 等 | 安卓壳跨域所需 |

## 部署

```bash
# 服务器上
mkdir -p /root/fwq/transfer
# 上传 server/、web/dist/、deploy/
cd /root/fwq/transfer/server
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt

cp /root/fwq/transfer/deploy/transfer.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now transfer
```

服务默认监听 `0.0.0.0:8791`，内存上限 256MB（实测常驻 33–50MB）。

## 安卓

```bash
cd web && npm run build
cd ../mobile && npx cap sync android
cd android && JAVA_HOME=/usr/lib/jvm/java-17-openjdk \
  ANDROID_HOME=/root/android/sdk \
  /root/tools/gradle-8.11.1/bin/gradle assembleDebug
```

产物：`mobile/android/app/build/outputs/apk/debug/app-debug.apk`

首次打开需在「设置」里填写服务器地址（如 `http://192.168.1.10:8791`），默认连 `localhost` 对手机无效。

### 构建环境注意事项

若在本项目所用的 Alpine + PRoot 容器内构建：

- **必须指定 `JAVA_HOME` 为 JDK 17**。JDK 21 在 PRoot 下起不来（`Failed to mark memory page as executable`），而 Capacitor 7 要求 Java 21，因此这里固定用 Capacitor 6。
- **不要用 `./gradlew`**，它会去官方源下载 Gradle 发行版；直接用本机 `/root/tools/gradle-8.11.1/bin/gradle`。
- 可能遇到 `Failed to delete '/tmp/tempdir_*'`。根因是 apkzlib 的 `TemporaryFile` 用 `File.delete()` 且失败即抛异常，而 PRoot 下刚清空的目录会短暂仍报存在。需替换该类为带重试版本，并打进缓存中**所有版本**的 apkzlib jar。

## 安全说明

- 文件原名只存数据库，磁盘上一律用 `<idx>.part`，从根上避免路径穿越。
- 发送方与接收方各有独立密钥，知道 token 也不能越权操作。
- 验证码按 IP 限流，防止穷举 6 位空间。
- 下载响应带 `X-Content-Type-Options: nosniff`，并按 RFC 5987 编码文件名。

**纯 HTTP 下中继路径的流量不加密**，可被中间人看到。P2P 路径自带 DTLS 加密，是安全的。若在意中继路径，可加一层 HTTPS 反向代理——前端无需改动。

## 已知限制

- 移动网络对称 NAT 下 P2P 成功率低（未配 TURN），此时会降级到中继；中继速度受服务器上行带宽限制。
- 中继下载交给浏览器下载器完成，因此页面上不显示下载进度条。
- 只支持单个接收方，验证码在保留期内有效且只对应一条传输。
