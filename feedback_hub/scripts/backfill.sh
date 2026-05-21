#!/usr/bin/env bash
# 30 天历史回灌（spec 阶段 2 §6.1 / §6.2）
# 用法：bash feedback_hub/scripts/backfill.sh
#
# 依赖：macOS 的 BSD `date -v` 语法。Linux 需另行适配。
# 幂等：feedback 主键冲突时跳过；message_label upsert；可重跑无害。

set -euo pipefail

# 切到仓库根目录（脚本位于 feedback_hub/scripts/，向上两级）
cd "$(dirname "$0")/../.."

echo "=== 30 天历史回灌开始 $(date '+%Y-%m-%d %H:%M:%S') ==="

FAILED_DAYS=()

for i in $(seq 30 -1 1); do
  DATE_START=$(date -v-${i}d +%Y-%m-%d)
  DATE_END=$(date -v-$((i-1))d +%Y-%m-%d)
  echo ">>> [$(date +%H:%M:%S)] 回灌 ${DATE_START} 00:00 ~ ${DATE_END} 00:00"
  if ! python3 -m feedback_hub.cli pull \
        --start "${DATE_START} 00:00:00" \
        --end "${DATE_END} 00:00:00"; then
    echo "!!! pull 失败 ${DATE_START}，记录后继续"
    FAILED_DAYS+=("${DATE_START}")
  fi
done

echo ""
echo "=== 拉取阶段完成；开始全量打标 ==="
python3 -m feedback_hub.cli tag --online

echo ""
echo "=== 回灌完成 $(date '+%Y-%m-%d %H:%M:%S') ==="
if [ ${#FAILED_DAYS[@]} -gt 0 ]; then
  echo "!!! 以下日期 pull 失败，需要人工补："
  printf '    %s\n' "${FAILED_DAYS[@]}"
  exit 1
fi
echo "全部 30 天均成功。"
