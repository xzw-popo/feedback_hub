# CloudBase 部署实操手册

> 最后更新：2026-06-15
>
> **目的**：明确部署路径，减少"卡住不知道原因"的情况。

---

## 一、部署路径选择

本项目有 **两条部署路径**，请根据本机环境选择：

| | 路径 A：源码直推（推荐） | 路径 B：本地 Docker + Python SDK |
|---|---|---|
| **前提** | 只需 Node.js + tcb CLI | 还需 Docker Desktop + 腾讯云密钥 + Python SDK |
| **步骤数** | 3 步 | 5 步 |
| **总耗时** | ~5 分钟 | ~10 分钟 |
| **后端构建** | 云端 CloudBuild | 本地 `docker build` |
| **环境变量** | 在 CloudBase 控制台修改 | 在 `deploy_cloudrun.py` 中修改 |
| **适用场景** | 日常部署 | 需要精确控制镜像内容 / 离线构建 |

> **如果不确定选哪条，选路径 A。**

---

## 二、路径 A：源码直推（推荐）

### 2.1 流程图

```
┌──────────────────────────────────────────────────┐
│  路径 A：3 步部署                                 │
│                                                    │
│  1. npm run build              ≈ 30s              │
│  2. tcb hosting deploy         ≈ 1-2min          │
│  3. tcb cloudrun deploy        ≈ 2-5min (云端构建)│
│                                                    │
│  总计：约 5 分钟                                   │
└──────────────────────────────────────────────────┘
```

### 2.2 前提条件

```bash
# 1. tcb CLI 安装（只需一次）
npm install -g @cloudbase/cli

# 2. tcb 登录（首次或过期时）
tcb login
# 浏览器会打开授权页面，点击同意即可
```

### 2.3 步骤 1：构建前端

```bash
cd ~/Desktop/用户反馈_2026_0612/dashboard
npm run build
```

**✅ 检查点**：`ls dist/index.html` 存在

**⏱ 耗时**：~30 秒

### 2.4 步骤 2：部署前端到静态托管

```bash
cd ~/Desktop/用户反馈_2026_0612/dashboard
tcb hosting deploy dist -e feedback7-d3gz69ofw321c4da5
```

**✅ 检查点**：输出 `Deployment complete` 和线上 URL

**⏱ 耗时**：1-2 分钟

**⚠️ 常见问题**：

| 现象 | 原因 | 解决 |
|---|---|---|
| `EPERM: operation not permitted, .~global.json` | tcb 配置目录权限问题 | 设置 `XDG_CONFIG_HOME=/tmp/tcb_config` 后重试 |
| `No valid identity information` | 登录过期 | `tcb login` 重新登录 |
| `npm error could not determine executable` | tcb 未全局安装 | `npm install -g @cloudbase/cli` |

### 2.5 步骤 3：部署后端到 CloudRun（云端构建）

```bash
cd ~/Desktop/用户反馈_2026_0612
echo "" | tcb cloudrun deploy \
  -s feedback-api \
  --source . \
  --port 9000 \
  --force
```

**说明**：
- `--source .`：将当前目录源码上传到 CloudBase，**云端自动执行 Docker 构建**
- `--force`：跳过二次确认
- `echo ""`：自动选择"不启用灰度发布"
- `.dockerignore` 会被云端 Docker 构建使用，`feedback_hub/data/`（66MB SQLite）不会进入镜像

**✅ 检查点**：输出 `提交完成` 和控制台链接

**⏱ 耗时**：2-5 分钟（主要是云端构建时间，取决于排队情况）

**⚠️ 环境变量注意事项**：
- `tcb cloudrun deploy` **不会**修改已配置的环境变量
- 如果需要新增或修改环境变量，去 [CloudBase 控制台](https://tcb.cloud.tencent.com/dev?envId=feedback7-d3gz69ofw321c4da5#/platform-run/service/detail?serverName=feedback-api&tabId=config&envId=feedback7-d3gz69ofw321c4da5) 修改
- 新版本部署后会自动继承上一版本的环境变量

---

## 三、路径 B：本地 Docker + Python SDK

> 适用于需要精确控制镜像、离线构建、或路径 A 失败的场景。

### 3.1 流程图

```
┌────────────────────────────────────────────────────────────────┐
│  路径 B：5 步部署                                               │
│                                                                  │
│  1. npm run build                         ≈ 30s               │
│  2. tcb hosting deploy                    ≈ 1-2min             │
│  3. docker build -t feedback-api:latest . ≈ 1-3min             │
│  4. docker save ... | gzip > /tmp/...     ≈ 30s-1min           │
│  5. python3 deploy_cloudrun.py            ≈ 2-5min             │
│                                                                  │
│  总计：约 10 分钟                                                │
└────────────────────────────────────────────────────────────────┘
```

### 3.2 额外前提条件

```bash
# Docker Desktop 必须运行中
docker ps

# Python SDK（只需安装一次）
pip install tencentcloud-sdk-python

# 腾讯云密钥（从 https://console.cloud.tencent.com/cam/capi 获取）
export TENCENT_SECRET_ID="你的 SecretId"
export TENCENT_SECRET_KEY="你的 SecretKey"
```

### 3.3 步骤 1-2：同路径 A

前端构建和部署完全相同，见上方 2.3 和 2.4。

### 3.4 步骤 3：本地 Docker 构建

```bash
cd ~/Desktop/用户反馈_2026_0612
docker build -t feedback-api:latest .
```

**✅ 检查点**：`docker images feedback-api` 显示新镜像

### 3.5 步骤 4：导出并压缩镜像

```bash
docker save feedback-api:latest | gzip > /tmp/feedback-api.tar.gz
```

**✅ 检查点**：`ls -lh /tmp/feedback-api.tar.gz` 显示 100-200MB

### 3.6 步骤 5：通过 Python SDK 部署到 CloudRun

```bash
# 如果需要传环境变量，先 export
export IMPORT_TOKEN="你的导入令牌"
export LLM_API_KEY="你的 DeepSeek API Key"

python3 deploy_cloudrun.py
```

`deploy_cloudrun.py` 脚本说明：
- 读取 `/tmp/feedback-api.tar.gz`，Base64 编码后通过 API 推送到 CloudRun
- 使用 `from_json_string()` 绕过 SDK 的 `Dockerfile → Ockerfile` 序列化 bug
- 环境变量在脚本内硬编码 + 读取 `os.environ` 混合模式

**⚠️ 已知坑**：

| 问题 | 原因 | 解决 |
|---|---|---|
| `Dockerfile → Ockerfile` | SDK `from_dict()` 序列化 bug | `from_json_string()` 绕过（脚本已处理） |
| `ReleaseType is required` | API 必需参数 | 脚本已包含 `"ReleaseType": "FULL"` |
| 镜像包 > 500MB | `.dockerignore` 遗漏 `data/` | 检查排除规则 |
| `AuthFailure` | 环境变量未设置 | `export TENCENT_SECRET_ID/KEY` |

---

## 四、部署后验证

### 4.1 后端 API 检查

```bash
# 基础连通性
curl -s https://feedback-api-269678-9-1442771950.sh.run.tcloudbase.com/api/conversations?limit=1 | python3 -c "
import sys,json; d=json.load(sys.stdin)
print(f'total: {d[\"total\"]}')
if d['items']:
    item = d['items'][0]
    print(f'platform 字段: {item.get(\"platform\", \"缺失!\")}')
"
```

**预期**：输出 total 数字和 platform 值（如 "Android"、"小程序"）

### 4.2 前端页面

浏览器打开：https://feedback7-d3gz69ofw321c4da5-1442771950.tcloudbaseapp.com/

- 反馈列表页面加载正常
- 设备筛选（多选）可用
- 搜索功能正常

### 4.3 CDN 缓存

如果前端页面没更新，用无痕模式或：
```bash
curl -H "Cache-Control: no-cache" https://feedback7-d3gz69ofw321c4da5-1442771950.tcloudbaseapp.com/
```

---

## 五、环境变量清单

> CloudRun 环境变量在控制台配置，每次部署新版本自动继承。
> 只有首次创建服务或需要修改时才需要手动操作。

| 变量名 | 值 | 用途 |
|---|---|---|
| `DB_MODE` | `mysql` | 数据库模式 |
| `MYSQL_HOST` | `172.17.0.7` | MySQL 内网地址 |
| `MYSQL_PORT` | `3306` | MySQL 端口 |
| `MYSQL_USER` | `feedback` | MySQL 用户名 |
| `MYSQL_PASSWORD` | `<MySQL 密码>` | MySQL 密码 |
| `MYSQL_DATABASE` | `feedback7-d3gz69ofw321c4da5` | 数据库名 |
| `CORS_ORIGINS` | `https://feedback7-d3gz69ofw321c4da5-1442771950.tcloudbaseapp.com` | CORS 白名单 |
| `PORT` | `9000` | 服务监听端口 |
| `IMPORT_TOKEN` | （与本地脚本共享） | 导入 API 鉴权 |
| `LLM_API_URL` | `https://api.deepseek.com/v1/chat/completions` | 智能搜索 LLM 地址 |
| `LLM_API_KEY` | （DeepSeek API Key） | 智能搜索 LLM 密钥 |
| `LLM_MODEL` | `deepseek-v4-flash` | 智能搜索 LLM 模型 |

> ⚠️ **关键提醒**：环境变量优先级**高于**代码 `config.py` 中的默认值。
> 如果 CloudRun 控制台设了 `LLM_MODEL=deepseek-chat`，即使代码默认是 `deepseek-v4-flash`，
> 线上跑的也是 `deepseek-chat`。修改模型时务必同步检查控制台配置。

---

## 六、一键部署脚本

### 路径 A 版本（推荐）

```bash
#!/usr/bin/env bash
# deploy_all.sh — 源码直推部署
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

echo "=========================================="
echo "  Feedback Hub 一键部署（路径 A：源码直推）"
echo "=========================================="

# ---------- 前端 ----------
echo ""
echo "📦 [1/3] 构建前端..."
cd dashboard
npm run build
echo "✅ 前端构建完成"

echo ""
echo "🚀 [2/3] 部署前端到 CloudBase 静态托管..."
tcb hosting deploy dist -e feedback7-d3gz69ofw321c4da5
echo "✅ 前端部署完成"

# ---------- 后端 ----------
cd "$PROJECT_ROOT"

echo ""
echo "☁️  [3/3] 部署后端到 CloudRun（云端构建）..."
echo "" | tcb cloudrun deploy -s feedback-api --source . --port 9000 --force
echo "✅ 后端部署已提交"

echo ""
echo "=========================================="
echo "  🎉 部署完成！"
echo "  前端: https://feedback7-d3gz69ofw321c4da5-1442771950.tcloudbaseapp.com/"
echo "  后端: https://feedback-api-269678-9-1442771950.sh.run.tcloudbase.com/"
echo "  ⏳ CloudRun 新版本通常 2-5 分钟生效"
echo "=========================================="
```

---

## 七、数据更新（不需要重新部署）

如果**只是更新数据**（新增反馈），不需要部署。使用每日自动同步：

```bash
bash feedback_hub/auto_daily.sh
```

**何时需要重新部署**：只有**代码变更**时才需要走部署流程。

---

## 八、故障排查速查表

| 现象 | 第一步检查 |
|---|---|
| `tcb: command not found` | `npm install -g @cloudbase/cli` |
| `EPERM .~global.json` | 设置 `XDG_CONFIG_HOME=/tmp/tcb_config` |
| `No valid identity information` | `tcb login` 重新登录 |
| Docker build 失败 | Docker Desktop 是否启动 |
| 镜像包 > 500MB | `.dockerignore` 是否排除了 `feedback_hub/data/` |
| API 调用 `AuthFailure` | `export TENCENT_SECRET_ID/KEY` |
| 部署后 API 500 | CloudRun 日志；检查环境变量 |
| 部署后页面白屏 | F12 Console；CDN 缓存（无痕模式） |
| AI 搜索报"服务异常" | 检查 `LLM_API_KEY`/`LLM_MODEL` 环境变量 |
| 部署后数据为空 | MySQL 表是否有数据；VPC 是否配置 |
| `Dockerfile → Ockerfile` | SDK 序列化 bug，用 `from_json_string()` |

---

## 九、2026-06-15 部署实录

本次部署采用路径 A（源码直推），实际操作过程和卡点：

### 实际操作步骤

| # | 操作 | 实际耗时 | 卡点 |
|---|---|---|---|
| 1 | `npm run build` | 2.7s | 无 |
| 2 | `tcb hosting deploy` | ~1min | 首次 `npx tcb` 不可用→改全局安装；EPERM 错误→改 XDG_CONFIG_HOME；需要浏览器登录授权 |
| 3 | `tcb cloudrun deploy --source` | ~3min | 交互式灰度发布选择→用 `echo ""` 自动选择 |
| 4 | 等待云端构建 | ~2min | 无进度显示，只能等 |
| 5 | 验证 | 1min | 无 |

**总耗时约 8 分钟**，其中 4 分钟是实际等待（前端上传 + 云端构建 + 版本发布）。

### 踩坑总结

1. **tcb CLI 安装方式**：`npx tcb` 可能不可用，建议 `npm install -g @cloudbase/cli` 全局安装
2. **EPERM 权限错误**：tcb 写 `~/.config/.cloudbase/.~global.json` 被沙箱拦截。解决方案：
   ```bash
   export XDG_CONFIG_HOME=/tmp/tcb_config
   mkdir -p /tmp/tcb_config/.cloudbase
   ```
3. **浏览器授权**：tcb login 需要在浏览器中点击授权，如果是远程环境无法打开浏览器，需要手动复制链接
4. **cloudrun deploy 交互式选择**：`--force` 不够，还需 `echo ""` 管道输入来跳过灰度发布选项
5. **无构建进度**：云端构建期间没有任何进度反馈，只能定时 curl 检查 API 是否更新
6. **环境变量不会丢失**：新版本自动继承上一版本的环境变量，无需每次重新配置

---

## 十、2026-06-15 第二次部署运行流程（完整记录）

> 本次为路径 A 的**纯净复现**：tcb 已全局安装、登录态已存在，无新增环境阻塞。
> 全程命令 + 实际输出 + 耗时如下，可直接照搬。

### 10.1 实际执行的命令（按顺序）

```bash
# ── 前置：复用已有 tcb 登录态（解决 EPERM，固定写法）──
export XDG_CONFIG_HOME=/tmp/tcb_config        # tcb 配置目录指向可写路径

# ── 步骤 1：构建前端（13:31）──
cd ~/Desktop/用户反馈_2026_0612/dashboard
npm run build                                  # ✅ built in 2.90s

# ── 步骤 2：部署前端到静态托管 ──
tcb hosting deploy dist -e feedback7-d3gz69ofw321c4da5
# ✅ Failed to upload 0 file(s)（0 失败 = 全部成功），22 个文件上传完成

# ── 步骤 3：部署后端到 CloudRun（13:32:09）──
cd ~/Desktop/用户反馈_2026_0612
echo "" | tcb cloudrun deploy -s feedback-api --source . --port 9000 --force
# ✅ Container-based cloud hosting feedback-api submission completed!

# ── 步骤 4：等待云端构建（约 2-3 分钟）+ 验证（13:35）──
curl -s "https://feedback-api-269678-9-1442771950.sh.run.tcloudbase.com/api/conversations?limit=1"
```

### 10.2 时间线

| 时刻 | 阶段 | 结果 |
|---|---|---|
| 13:31:29 | 部署开始，检查 tcb 登录态 | 登录态存在 ✅ |
| 13:31:33 | `npm run build` 完成 | 2.90s，dist 产物生成 ✅ |
| ~13:32:00 | `tcb hosting deploy` 完成 | 22 文件全部上传，0 失败 ✅ |
| 13:32:09 | `tcb cloudrun deploy` 提交 | 云端构建任务已提交 ✅ |
| 13:35:01 | 后端 API 验证 | total=50435，正常响应 ✅ |

**总耗时约 3.5 分钟**（登录态已就绪的情况下，比首次的 8 分钟快很多）。

### 10.3 验证结果

| 验证项 | 命令 | 结果 |
|---|---|---|
| 前端版本 | `curl .../ \| grep index-*.js` | `index-CEKBxnsb.js`（与本次构建一致）✅ |
| 后端连通 | `GET /api/conversations?limit=1` | total=50435 ✅ |
| 设备字段 | 检查响应 `platform` | `Android` / `小程序` 正常返回 ✅ |
| 关键词搜索 | `POST /api/keyword-search {"groups":[{"keywords":["闪退"]}]}` | 匹配 96 条 ✅ |

### 10.4 关键经验（本次新增）

1. **登录态可复用**：只要 `/tmp/tcb_config/.cloudbase/auth.json` 还在，就不必重新 `tcb login`。
   每次部署前先 `export XDG_CONFIG_HOME=/tmp/tcb_config` 即可直接用。
2. **真正的卡点只有一个——云端构建等待**：前端秒级完成，后端提交也是秒级，
   唯一需要"等"的是 CloudRun 云端 Docker 构建（2-3 分钟），且无进度条。
   **不要反复重试，提交成功后耐心等 2-3 分钟再 curl 验证即可。**
3. **判断后端是否生效的最快方法**：curl `/api/conversations?limit=1`，
   看 `total` 数字或 `platform` 字段是否符合预期，比看控制台快。
4. **前端 CDN 缓存**：验证前端用 `-H "Cache-Control: no-cache"`，避免被 CDN 旧缓存误导。
