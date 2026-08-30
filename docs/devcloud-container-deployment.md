# Feedback Hub CPU DevCloud 部署与同步手册

本文档记录 Feedback Hub 部署到 CPU DevCloud 开发容器的完整流程，包括首次部署、日常代码更新、数据同步、远端管理和常见排障。

## 1. 部署结论

当前网站已部署在 CPU DevCloud 容器内，前端页面和后端 API 共用一个公网端口。

访问地址：

```text
http://charvelxia-any2.devcloud.woa.com:8000/
```

远端目录：

```text
/opt/feedback_hub
```

默认端口：

```text
8000
```

## 2. 容器信息

SSH 连接：

```bash
ssh root@charvelxia-any2.devcloud.woa.com -p 36000
```

等价 IP 域名：

```bash
ssh root@21.91.26.201.devcloud.woa.com -p 36000
```

已确认的远端环境：

- Python 3.11.6 可用。
- `python3 -m venv` 可用。
- `python3 -m ensurepip` 可用。
- 远端没有 `node` / `npm`，因此前端在本机构建后上传。
- `systemctl is-system-running` 为 `offline`，因此不用 systemd service。
- 远端服务使用 Python venv + `nohup` 后台运行。

## 3. 部署架构

部署后，容器内独立保存和运行这些内容：

- Vue/Vite 前端构建产物：`dashboard/dist`
- FastAPI 后端代码：`feedback_hub`
- SQLite 数据库：`feedback_hub/data/feedback.db`
- Python 虚拟环境：`.venv`
- 运行日志：`logs/app.log`
- PID 文件：`run/feedback_hub.pid`

FastAPI 会自动检测 `dashboard/dist/index.html`，并在同一个端口提供：

- `/api/...`：后端 API
- `/assets/...`：前端静态资源
- `/`：前端首页
- 其他前端路由：fallback 到 `index.html`

前端生产构建必须使用同源 API，也就是请求当前域名下的 `/api`。因此：

- `dashboard/.env.production` 中的 `VITE_API_BASE_URL` 应保持为空。
- `deploy_devcloud.sh` 会在构建时显式清空 `VITE_API_BASE_URL`。
- 不要在 DevCloud 部署中把 `VITE_API_BASE_URL` 写成 CloudBase 或其他旧后端域名。

## 4. 首次部署

在本机项目根目录执行：

```bash
APP_PORT=8000 ./deploy_devcloud.sh
```

脚本会自动完成：

1. 在本机构建前端：`npm --prefix dashboard run build`
2. 打包当前项目。
3. 上传压缩包到远端容器。
4. 停止远端旧服务。
5. 替换远端目录 `/opt/feedback_hub`。
6. 复用或创建远端 `.venv`。
7. 安装 Python 依赖。
8. 用 `nohup uvicorn` 启动 FastAPI。

部署成功后访问：

```text
http://charvelxia-any2.devcloud.woa.com:8000/
```

如果需要使用其他端口：

```bash
APP_PORT=9000 ./deploy_devcloud.sh
```

如果需要部署到其他远端目录：

```bash
REMOTE_DIR=/data/feedback_hub APP_PORT=8000 ./deploy_devcloud.sh
```

## 5. 后续代码更新

只要项目代码、前端页面、后端接口、依赖或文档发生需要发布到容器的变化，就重新执行：

```bash
APP_PORT=8000 ./deploy_devcloud.sh
```

这条命令是日常代码部署的标准入口。它会重新构建前端、上传项目、停止旧进程并启动新进程。

部署脚本会在替换代码目录时保留远端运行态数据：

- `/opt/feedback_hub/feedback_hub/data/`
- `/opt/feedback_hub/.env`
- `/opt/feedback_hub/.venv`

因此日常代码部署不会覆盖远端 SQLite 数据库。需要同步本地数据库时，仍按「数据更新方案」单独执行数据库替换流程。

部署后建议验证：

```bash
curl 'http://charvelxia-any2.devcloud.woa.com:8000/api/conversations?limit=1'
```

也可以在远端检查服务状态：

```bash
ssh root@charvelxia-any2.devcloud.woa.com -p 36000
cd /opt/feedback_hub
APP_PORT=8000 scripts/devcloud_runtime.sh status
```

### 5.1 部署一次性反馈专题挖掘后端

专题挖掘后端提供 `/api/topic-mining/*`，用于创建只读、可审核的专题 run。它不是日常拉取或通用打标流程的一部分：源数据库只读，快照和 run 状态只写入 `feedback_hub/data/topic_mining/`。

部署代码后，在远端虚拟环境安装专题依赖：

```bash
cd /opt/feedback_hub
./.venv/bin/pip install -r requirements-topic-mining.txt
```

在远端 `.env` 配置以下六个 `TOPIC_*` 变量；token 只能写在 `.env`，不要提交或复制到 Skill 源码：

```dotenv
TOPIC_MINING_DATA_DIR=/opt/feedback_hub/feedback_hub/data/topic_mining
TOPIC_VECTOR_API_URL=https://your-internal-vector-service
TOPIC_VECTOR_API_TOKEN=
TOPIC_VECTOR_INDEX=feedback-items-v1
TOPIC_VECTOR_MAX_LAG_SECONDS=21600
TOPIC_MINING_API_TOKEN=
```

协议 v3 的专题挖掘不会调用后端语义分类模型。`LLM_*` 可以继续为搜索、打标等其他服务保留，但 `LLM_MODEL`（包括 `deepseek-v4-flash`）不会参与 v3 专题的成员判定：

```dotenv
LLM_API_URL=
LLM_API_KEY=
LLM_MODEL=
```

`TOPIC_VECTOR_API_URL` 指向受维护的向量召回服务；向量结果只用于候选召回，调用 Skill 的 AI 负责逐条语义分类，后端负责证据校验、验证和导出。向量服务返回的 `watermark_ts_ms` 必须是从 `feedback_source_coverage.completed_at_ms` 传递的同一数据代际，不能用最后一条反馈的事件时间代替。

新版 schema 会创建 `feedback_source_coverage`，每次 `feedback_hub pull` 成功后写入本次的 channel、拉取起止区间和单调递增的数据代际。专题服务只读这张表：它用覆盖区间区分“该时段没有反馈”和“该时段尚未同步”，并用数据代际区分最大反馈时间不变的历史补录。旧数据库在首次启动新代码时只会建表，不会猜测历史覆盖范围；在运行专题前，必须用正常拉取流程同步请求的完整时间段。如果 run 返回 `data_coverage_error`，应补拉缺失区间并新建数据代际下的 run，不得伪造边界反馈或手工绕过验证。

完成依赖和配置后，先重启新代码并确认新进程正常，再检查专题能力接口：

```bash
APP_PORT=8000 scripts/devcloud_runtime.sh restart
APP_PORT=8000 scripts/devcloud_runtime.sh status
```

能力接口必须包含完整且精确的 v3 所有权契约，新的 Skill 才允许创建 Run：

```json
{
  "classification_protocol": {
    "version": 3,
    "owner": "caller_ai",
    "candidate_page_default": 20,
    "candidate_page_maximum": 20,
    "matched_evidence": "exact_candidate_substring",
    "partial_acceptance": true
  }
}
```

健康的新 Run 在快照、硬筛和混合召回后应停在
`classification_ready`，此时没有后台模型来源或重试信息。后续运维观察
`accepted_decision_count` 和 `pending_decision_count`；覆盖完整后进入
`verification_ready`。冒烟测试应故意把一条只存在于 `context_items` 的证据
提交给候选反馈，确认返回 `evidence_not_candidate_grounded`、其他条目被接受、
仅该 ID 保持 pending；再将其修复为 `not_matched`，并确认真正相关的上下文消息
只有以自己的 Feedback ID 独立召回和判定后才能导出。不要恢复未完成的 v1/v2
Run；旧 v1/v2 仅保留已验证结果的读取和重新导出兼容。

`.env` 不会自动导出到当前 shell。保持在 `/opt/feedback_hub`，先用 `env -u` 避免当前 shell 的同名变量遮蔽项目配置，再由当前虚拟环境导入 `feedback_hub.config`，让它加载项目根 `.env`，最后把专题 API token 只保存到临时 shell 变量。以下命令不打印 token；也不要额外 `echo` 该变量：

```bash
cd /opt/feedback_hub
TOPIC_CAPABILITIES_URL='http://127.0.0.1:8000/api/topic-mining/capabilities'
TOPIC_TOKEN="$(
  env -u TOPIC_MINING_API_TOKEN ./.venv/bin/python -c 'import os; from feedback_hub import config as _feedback_config; print(os.environ.get("TOPIC_MINING_API_TOKEN", ""))'
)" || exit 1

if [ -z "$TOPIC_TOKEN" ]; then
  TOPIC_HTTP_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' "$TOPIC_CAPABILITIES_URL")" || \
    { unset TOPIC_TOKEN TOPIC_HTTP_STATUS; exit 1; }
  test "$TOPIC_HTTP_STATUS" = "200" || \
    { unset TOPIC_TOKEN TOPIC_HTTP_STATUS; exit 1; }
else
  TOPIC_HTTP_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' "$TOPIC_CAPABILITIES_URL")" || \
    { unset TOPIC_TOKEN TOPIC_HTTP_STATUS; exit 1; }
  test "$TOPIC_HTTP_STATUS" = "401" || \
    { unset TOPIC_TOKEN TOPIC_HTTP_STATUS; exit 1; }
  TOPIC_HTTP_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $TOPIC_TOKEN" \
    "$TOPIC_CAPABILITIES_URL")" || \
    { unset TOPIC_TOKEN TOPIC_HTTP_STATUS; exit 1; }
  test "$TOPIC_HTTP_STATUS" = "200" || \
    { unset TOPIC_TOKEN TOPIC_HTTP_STATUS; exit 1; }
fi

unset TOPIC_HTTP_STATUS
unset TOPIC_TOKEN
```

空 token 分支确认未开启鉴权时返回 `200`；非空分支先确认无 token 请求返回 `401`，再确认携带正确 token 返回 `200`。每次 `curl` 的传输状态和 HTTP 状态都独立校验；任何一步不符合预期都会立即退出并传播非零状态，不会被后续成功命令掩盖。

专题后端部署不上传或安装 `codex-skills/mining-feedback-topics/`：该目录是单独分发给 Codex 的客户端 Skill，不属于服务运行时。打包部署时应将其排除出服务包。部署也绝不替换生产 `feedback_hub/data/feedback.db`；仅保留既有数据库，并让专题 run 在独立 `topic_mining/` 数据目录内创建快照和产物。

### 5.2 部署本地 Qwen 向量后端（需单独批准远端写入）

向量模型、索引和日志都是远端运行态，固定保存在代码包之外；常规 `deploy_devcloud.sh` 会保留它们，归档和 Git 均不包含这些路径：

- 模型：`/opt/feedback_hub/feedback_hub/data/models/Qwen3-Embedding-0.6B`
- 索引：`/opt/feedback_hub/feedback_hub/data/vector_index`
- 向量 API：只监听 `127.0.0.1:8011`（可用 `VECTOR_PORT` 覆盖）
- 向量日志：`/opt/feedback_hub/logs/vector_index.log`
- 增量同步日志：`/opt/feedback_hub/feedback_hub/data/logs/feedback_incremental_sync.log`

模型只在获得远端写入批准后从本机已验证的实验目录上传。脚本要求本地模型同时有 `config.json`、`tokenizer.json`、`model.safetensors`，生成确定性 SHA-256 清单，仅上传与远端清单不同的文件，并通过临时目录校验后原子提升。它会先安装 `requirements-vector.txt`，再从官方 CPU 索引安装 PyTorch，并拒绝 CUDA 版本。它不会拉取原始反馈、不会打标、不会重建索引、不会修改 cron。

```bash
cd /Users/charvel/Desktop/用户反馈_2026_0612
MODEL_SOURCE_DIR=/Users/charvel/Desktop/用户反馈_2026_0612/feedback_hub/data/embedding_lab/models/Qwen3-Embedding-0.6B \
  scripts/deploy_vector_backend_devcloud.sh
```

首次 bootstrap 默认不启动向量服务：没有已验证的活动根 `manifest.json` 或安全的 `active-generation.json` 指针时，启动会被拒绝。完成获批的两周数据准备和 rebuild 后，才可明确请求启动：

```bash
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com '
  set -e
  cd /opt/feedback_hub &&
  ACTIVE_POINTER=feedback_hub/data/vector_index/active-generation.json &&
  ACTIVE_POINTER_BACKUP=feedback_hub/data/vector_index/backups/active-generation.json.pre-rebuild &&
  NO_PRIOR_POINTER_MARKER=feedback_hub/data/vector_index/backups/no-prior-active-generation.pre-rebuild &&
  mkdir -p feedback_hub/data/vector_index/backups &&
  if [ -e "$ACTIVE_POINTER" ]; then
    ./.venv/bin/python -m json.tool "$ACTIVE_POINTER" >/dev/null
    rm -f -- "$NO_PRIOR_POINTER_MARKER"
    cp -- "$ACTIVE_POINTER" "$ACTIVE_POINTER_BACKUP"
    test -s "$ACTIVE_POINTER_BACKUP"
  else
    rm -f -- "$ACTIVE_POINTER_BACKUP"
    : > "$NO_PRIOR_POINTER_MARKER"
  fi
  ./.venv/bin/python -m feedback_hub.cli ingest backfill --last 14d --chunk 6h &&
  ./.venv/bin/python -m feedback_hub.cli vectors rebuild \
    --target-model-version qwen3-embedding-0.6b-document-v2 \
    --generation-id qwen3-embedding-0.6b-document-v2-20260721 &&
  scripts/vector_runtime.sh start
'
```

`--last 14d` 是本轮批准的最大历史回填窗口；不要因未来可能需要更长专题范围而扩大到 180 天。回填只写原始覆盖和向量数据，不能调用通用 `tag`。如果模型部署时索引已经存在，可用 `--start-after-bootstrap` 只在上述活动清单/指针有效时启动：

```bash
MODEL_SOURCE_DIR=/path/to/Qwen3-Embedding-0.6B \
  scripts/deploy_vector_backend_devcloud.sh --start-after-bootstrap
```

常规代码部署在代码目录原子切换后，如果检测到活动索引，会先重启并检查向量服务，再启动 FastAPI。向量服务无法达到 `/health` 时应用保持停止，部署返回非零；脚本会把代码目录回滚，同时保留 `.venv`、`.env`、模型、SQLite 与 `feedback_hub/data/`。

```bash
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com '
  cd /opt/feedback_hub &&
  scripts/vector_runtime.sh status &&
  curl -fsS http://127.0.0.1:8011/health &&
  APP_PORT=8000 scripts/devcloud_runtime.sh status
'
```

当前节点只通过内网/VPN 访问专题接口，因此 `TOPIC_MINING_API_TOKEN` 保持 unset；不要为了此部署设置、打印或提交该 token。仍需保留 `TOPIC_VECTOR_API_URL=http://127.0.0.1:8011` 和相应 `TOPIC_*` 运行时配置在远端 `.env`，而不是本机 shell 或仓库。

在切换每 20 分钟增量 cron 前，先备份当前 crontab；回滚时恢复此备份并删除新条目。以下命令是一次性远端操作，不由部署脚本执行：

```bash
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com '
  cd /opt/feedback_hub &&
  mkdir -p feedback_hub/data/cron-backups &&
  crontab -l > feedback_hub/data/cron-backups/crontab-pre-vector.txt &&
  scripts/install_feedback_incremental_cron.sh &&
  crontab -l
'

# rollback the cron only after inspecting the backup:
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com '
  crontab /opt/feedback_hub/feedback_hub/data/cron-backups/crontab-pre-vector.txt
'
```

若 rebuild 生成了错误活动代际，先停止向量服务。存在旧指针备份时恢复它；首次 rebuild 没有旧指针时，保留 `no-prior-active-generation.pre-rebuild` 标记、删除新建指针并确认健康端点不可用。不要删除模型或覆盖 `feedback_hub/data/`。记录操作后再决定是否恢复 crontab 备份。

```bash
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com '
  set -e
  cd /opt/feedback_hub &&
  ACTIVE_POINTER=feedback_hub/data/vector_index/active-generation.json &&
  ACTIVE_POINTER_BACKUP=feedback_hub/data/vector_index/backups/active-generation.json.pre-rebuild &&
  NO_PRIOR_POINTER_MARKER=feedback_hub/data/vector_index/backups/no-prior-active-generation.pre-rebuild &&
  scripts/vector_runtime.sh stop &&
  if test -s "$ACTIVE_POINTER_BACKUP"; then
    cp -- "$ACTIVE_POINTER_BACKUP" "$ACTIVE_POINTER"
    scripts/vector_runtime.sh start
    curl -fsS http://127.0.0.1:8011/health
  elif test -f "$NO_PRIOR_POINTER_MARKER"; then
    rm -f -- "$ACTIVE_POINTER"
    test ! -e "$ACTIVE_POINTER"
    if curl -fsS http://127.0.0.1:8011/health; then exit 1; fi
  else
    echo "missing active-generation rollback evidence" >&2
    exit 1
  fi
'
```

## 6. 数据更新方案

数据更新有两种方式：容器自动更新，或本机更新后同步数据库到容器。

### 6.1 方案 A：容器自动更新

适用条件：

- 容器能访问上游反馈接口。
- 容器内 `.env` 配好了必要变量，例如 `LLM_API_KEY`、`WINK_AGENT_KEY` 等。
- 上游接口不强绑定本机身份、浏览器态或其他只在本机存在的凭据。

已完成基础连通性探测：

- `wrfeedback.weread.woa.com:80/443` 可连通。
- `api.deepseek.com:80/443` 可连通。
- `winkagentsvr.dante.weread2.woa.com:80/443` 可连通。

注意：网络连通不等于业务鉴权一定通过。真正切换到容器自动更新前，需要在远端执行一次真实拉取和打标流程验证。

### 6.2 方案 B：本机自动更新后同步到容器

如果容器无法通过业务鉴权，继续让本机执行自动更新，然后同步 SQLite 到远端。

本机完成自动更新后，执行：

```bash
scp -P 36000 feedback_hub/data/feedback.db \
  root@charvelxia-any2.devcloud.woa.com:/opt/feedback_hub/feedback_hub/data/feedback.db.new

ssh root@charvelxia-any2.devcloud.woa.com -p 36000 '
  cd /opt/feedback_hub &&
  scripts/devcloud_runtime.sh stop &&
  mv feedback_hub/data/feedback.db.new feedback_hub/data/feedback.db &&
  APP_PORT=8000 scripts/devcloud_runtime.sh start
'
```

这个流程会先上传到 `.db.new`，再停服务并替换数据库，避免服务运行中直接覆盖 SQLite 文件。

## 7. 远端管理命令

登录远端：

```bash
ssh root@charvelxia-any2.devcloud.woa.com -p 36000
cd /opt/feedback_hub
```

查看状态：

```bash
APP_PORT=8000 scripts/devcloud_runtime.sh status
```

查看日志：

```bash
scripts/devcloud_runtime.sh logs
```

查看更多日志行：

```bash
LINES=300 scripts/devcloud_runtime.sh logs
```

重启：

```bash
APP_PORT=8000 scripts/devcloud_runtime.sh restart
```

停止：

```bash
scripts/devcloud_runtime.sh stop
```

启动：

```bash
APP_PORT=8000 scripts/devcloud_runtime.sh start
```

查看端口占用：

```bash
ss -ltnp | grep :8000
```

## 8. 验证清单

部署后推荐按顺序检查：

```bash
curl -s -o /tmp/feedback_hub_index.html \
  -w '%{http_code} %{content_type} %{size_download}\n' \
  'http://charvelxia-any2.devcloud.woa.com:8000/'
```

期望首页返回：

```text
200 text/html
```

检查 API：

```bash
curl -s -o /tmp/feedback_hub_api.json \
  -w '%{http_code} %{content_type} %{size_download}\n' \
  'http://charvelxia-any2.devcloud.woa.com:8000/api/conversations?limit=1'
```

期望 API 返回：

```text
200 application/json
```

检查远端进程：

```bash
ssh root@charvelxia-any2.devcloud.woa.com -p 36000 '
  cd /opt/feedback_hub &&
  APP_PORT=8000 scripts/devcloud_runtime.sh status
'
```

期望看到：

```text
[runtime] running pid=<pid> port=8000
```

## 9. 常见问题

### 页面提示“网络错误，请检查后端服务”

优先检查生产构建是否误用了旧 API 域名。

本地检查：

```bash
rg -n 'feedback-api|tcloudbase|sh.run.tcloudbase|VITE_API_BASE_URL' dashboard/dist dashboard/.env.production
```

远端检查：

```bash
ssh root@charvelxia-any2.devcloud.woa.com -p 36000 \
  "rg -n 'feedback-api|tcloudbase|sh.run.tcloudbase' /opt/feedback_hub/dashboard/dist || true"
```

如果产物里出现旧后端域名，确认 `dashboard/.env.production` 中 `VITE_API_BASE_URL=` 为空，然后重新部署：

```bash
APP_PORT=8000 ./deploy_devcloud.sh
```

### 重部署后仍然访问到旧页面

可能是浏览器缓存。先强制刷新：

```text
Cmd + Shift + R
```

再检查首页引用的 JS 文件是否已变化：

```bash
curl -s 'http://charvelxia-any2.devcloud.woa.com:8000/'
```

### 新进程启动失败，提示端口占用

检查远端端口：

```bash
ssh root@charvelxia-any2.devcloud.woa.com -p 36000 '
  ss -ltnp | grep :8000 || true
'
```

当前 `scripts/devcloud_runtime.sh stop` 会优先按 PID 文件停止服务，并兜底停止占用 `APP_PORT` 的进程。修复后重新部署即可：

```bash
APP_PORT=8000 ./deploy_devcloud.sh
```

### API 能访问，但页面列表为空

先直接请求列表 API：

```bash
curl 'http://charvelxia-any2.devcloud.woa.com:8000/api/conversations?limit=1'
```

如果 API 有数据而页面没有，检查浏览器控制台和前端构建产物。  
如果 API 也没有数据，检查远端 SQLite：

```bash
ssh root@charvelxia-any2.devcloud.woa.com -p 36000 '
  ls -lh /opt/feedback_hub/feedback_hub/data/feedback.db
'
```

## 10. 注意事项

- `deploy_devcloud.sh` 会上传当前工作区的 `.env`，请确认里面是远端运行允许使用的配置。
- SQLite 是文件数据库。替换数据库前应先停止服务。
- 如果开发容器被平台重置，`/opt/feedback_hub` 可能需要重新部署。
- 当前长期运行依赖 `nohup` 后台进程。如果平台后续提供更正式的进程保活能力，可以再切换。
- 日常记忆方式：改代码跑 `./deploy_devcloud.sh`，只更新数据就同步 `feedback.db` 并重启服务。
