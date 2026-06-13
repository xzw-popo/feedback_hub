#!/usr/bin/env bash
# feedback_hub 每日自动同步脚本（spec §6）
#
# 流程：VPN 检测 → pull → tag → export → POST /api/import → push
#
# 用法：
#   bash feedback_hub/auto_daily.sh                    # 拉取昨天的数据
#   bash feedback_hub/auto_daily.sh --date 2026-06-11  # 指定日期补跑
#   bash feedback_hub/auto_daily.sh --dry-run          # 只导出不同步
#
# 定时调度：macOS launchd 每天 10:00 触发

set -euo pipefail

# ---------- 配置 ----------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="/Users/charvel/.workbuddy/binaries/python/envs/default/bin/python"
DB_PATH="$PROJECT_DIR/feedback_hub/data/feedback.db"
EXPORT_DIR="$PROJECT_DIR/feedback_hub/data/export"
LOG_DIR="$PROJECT_DIR/feedback_hub/data/logs"
LAST_SYNC_FILE="$EXPORT_DIR/last_sync.json"

# CloudRun API 地址
API_BASE="${FEEDBACK_API_URL:-https://feedback-api-269678-9-1442771950.sh.run.tcloudbase.com}"
IMPORT_TOKEN="${IMPORT_TOKEN:-}"

# VPN 检测
VPN_CHECK_URL="https://wrfeedback.weread.woa.com"
VPN_MAX_RETRIES=6
VPN_RETRY_INTERVAL=300  # 5 分钟

# ---------- 工具函数 ----------

log() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $*"
    echo "[$ts] $*" >> "$LOG_DIR/auto_daily.log"
}

notify() {
    local title="$1"
    local body="$2"
    osascript -e "display notification \"$body\" with title \"$title\"" 2>/dev/null || true
}

die() {
    log "ERROR: $*"
    notify "反馈同步失败" "$*"
    exit 1
}

# 计算目标日期（默认昨天，北京时间）
target_date() {
    if [[ -n "${1:-}" ]]; then
        echo "$1"
    else
        # macOS BSD date：减 1 天
        date -v-1d +%Y-%m-%d
    fi
}

# 检测 VPN 连通性
check_vpn() {
    curl -s -o /dev/null -w "%{http_code}" --max-time 10 -k "$VPN_CHECK_URL" 2>/dev/null || echo "000"
}

# 读取上次成功同步的日期
last_sync_date() {
    if [[ -f "$LAST_SYNC_FILE" ]]; then
        $PYTHON -c "
import json
try:
    d = json.load(open('$LAST_SYNC_FILE'))
    print(d.get('date', ''))
except:
    print('')
" 2>/dev/null || echo ""
    else
        echo ""
    fi
}

# 写入同步成功记录
mark_synced() {
    local d="$1"
    mkdir -p "$EXPORT_DIR"
    $PYTHON -c "
import json
json.dump({'date': '$d', 'synced_at': '$(date -u +%Y-%m-%dT%H:%M:%SZ)'}, open('$LAST_SYNC_FILE', 'w'))
"
}

# ---------- 主流程 ----------

main() {
    local DATE=""
    local DRY_RUN=false

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --date)
                DATE="$2"
                shift 2
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            *)
                shift
                ;;
        esac
    done

    # 初始化日志目录
    mkdir -p "$LOG_DIR" "$EXPORT_DIR"

    # 计算目标日期
    if [[ -z "$DATE" ]]; then
        DATE="$(target_date)"
    fi

    log "========== 每日同步开始，目标日期: $DATE =========="

    # 幂等检查：如果当天已同步，跳过
    local LAST_DATE
    LAST_DATE="$(last_sync_date)"
    if [[ "$LAST_DATE" == "$DATE" ]]; then
        log "日期 $DATE 已同步过，跳过"
        notify "反馈同步" "$DATE 已同步，跳过"
        exit 0
    fi

    # 步骤 1：检测 VPN 连通性
    log "步骤 1/6: 检测 VPN 连通性..."
    local vpn_ok=false
    for i in $(seq 1 $VPN_MAX_RETRIES); do
        local code
        code="$(check_vpn)"
        if [[ "$code" != "000" && "$code" -lt 500 ]]; then
            vpn_ok=true
            log "VPN 已连通 (HTTP $code)"
            break
        fi
        log "VPN 未连通 (HTTP $code)，第 $i/$VPN_MAX_RETRIES 次重试，${VPN_RETRY_INTERVAL}s 后重试..."
        if [[ $i -lt $VPN_MAX_RETRIES ]]; then
            sleep "$VPN_RETRY_INTERVAL"
        fi
    done

    if [[ "$vpn_ok" == "false" ]]; then
        die "VPN 连接超时，无法拉取数据"
    fi

    # 步骤 2：pull
    log "步骤 2/6: 拉取 $DATE 的反馈数据..."
    cd "$PROJECT_DIR"
    $PYTHON -m feedback_hub.cli pull --date "$DATE" || die "pull 失败"

    # 步骤 3：tag（离线模式）
    log "步骤 3/6: 打标（离线模式）..."
    $PYTHON -m feedback_hub.cli tag || die "tag 失败"

    # 步骤 4：导出 JSON
    log "步骤 4/6: 导出增量数据..."
    local EXPORT_FILE="$EXPORT_DIR/$DATE.json"
    $PYTHON -m feedback_hub.exporter --date "$DATE" --output "$EXPORT_FILE" --token "$IMPORT_TOKEN" \
        || die "导出失败"

    # 统计导出行数
    local ROW_COUNT
    ROW_COUNT=$($PYTHON -c "
import json
d = json.load(open('$EXPORT_FILE'))
total = sum(len(v) for v in d.get('tables', {}).values())
print(total)
" 2>/dev/null || echo "0")
    log "导出 $ROW_COUNT 行数据到 $EXPORT_FILE"

    # dry-run 模式到此为止
    if [[ "$DRY_RUN" == "true" ]]; then
        log "dry-run 模式，跳过同步和推送"
        notify "反馈同步 (dry-run)" "$DATE 导出 $ROW_COUNT 行"
        exit 0
    fi

    # 步骤 5：POST /api/import
    log "步骤 5/6: 同步到云端..."
    if [[ -z "$IMPORT_TOKEN" ]]; then
        die "IMPORT_TOKEN 环境变量未设置"
    fi

    local IMPORT_RESPONSE
    IMPORT_RESPONSE=$(curl -s -X POST \
        -H "Content-Type: application/json" \
        -d @"$EXPORT_FILE" \
        "$API_BASE/api/import" \
        --max-time 120 \
        -w "\n%{http_code}" 2>&1) || die "HTTP 请求失败"

    local HTTP_CODE
    HTTP_CODE=$(echo "$IMPORT_RESPONSE" | tail -1)
    local BODY
    BODY=$(echo "$IMPORT_RESPONSE" | sed '$d')

    log "API 响应: HTTP $HTTP_CODE"
    log "响应体: $BODY"

    if [[ "$HTTP_CODE" != "200" ]]; then
        die "导入失败: HTTP $HTTP_CODE - $BODY"
    fi

    # 检查部分失败
    local HAS_ERRORS
    HAS_ERRORS=$($PYTHON -c "
import json
d = json.loads('''$BODY''')
print('false' if d.get('ok', False) else 'true')
" 2>/dev/null || echo "true")

    if [[ "$HAS_ERRORS" == "true" ]]; then
        log "警告: 部分数据导入失败，请检查响应体"
    fi

    # 标记同步成功
    mark_synced "$DATE"

    # 步骤 6：push（可选）
    log "步骤 6/6: 企微推送..."
    if $PYTHON -m feedback_hub.cli push --date "$DATE" 2>/dev/null; then
        log "推送完成"
    else
        log "推送失败或未配置，跳过（不影响同步结果）"
    fi

    log "========== 每日同步完成: $DATE =========="
    notify "反馈同步完成" "$DATE 成功导入 $ROW_COUNT 行"
}

main "$@"
