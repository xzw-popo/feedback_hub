#!/usr/bin/env python3
"""CloudRun 部署脚本 —— 使用腾讯云 Python SDK 绕过 tcb CLI bug。

用法：python3 deploy_cloudrun.py

前提：
  - pip install tencentcloud-sdk-python
  - 已 docker build + docker save + gzip 生成 /tmp/feedback-api.tar.gz
  - 已设置环境变量 TENCENT_SECRET_ID 和 TENCENT_SECRET_KEY
"""
import base64
import json
import os
import sys
import time

from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tcb.v20180608 import tcb_client, models

# ===== 配置 =====
ENV_ID = "feedback7-d3gz69ofw321c4da5"
SERVICE_NAME = "feedback-api"
PACKAGE_PATH = "/tmp/feedback-api.tar.gz"

# 环境变量（与 CloudRun 控制台一致）
ENV_VARS = {
    "DB_MODE": "mysql",
    "MYSQL_HOST": "172.17.0.7",
    "MYSQL_PORT": "3306",
    "MYSQL_USER": "feedback",
    "MYSQL_PASSWORD": os.environ.get("MYSQL_PASSWORD", ""),
    "MYSQL_DATABASE": "feedback7-d3gz69ofw321c4da5",
    "CORS_ORIGINS": "https://feedback7-d3gz69ofw321c4da5-1442771950.tcloudbaseapp.com",
    "PORT": "9000",
    "IMPORT_TOKEN": os.environ.get("IMPORT_TOKEN", ""),
    "LLM_API_URL": "https://api.deepseek.com/v1/chat/completions",
    "LLM_API_KEY": os.environ.get("LLM_API_KEY", ""),
    "LLM_MODEL": "deepseek-v4-flash",
}


def main():
    # 1. 读取镜像包
    print(f"[1/4] 读取镜像包: {PACKAGE_PATH}")
    if not os.path.exists(PACKAGE_PATH):
        print(f"  ❌ 镜像包不存在: {PACKAGE_PATH}")
        print("  请先执行: docker build -t feedback-api:latest . && "
              "docker save feedback-api:latest | gzip > /tmp/feedback-api.tar.gz")
        sys.exit(1)

    file_size_mb = os.path.getsize(PACKAGE_PATH) / 1024 / 1024
    print(f"  镜像包大小: {file_size_mb:.1f} MB")

    with open(PACKAGE_PATH, "rb") as f:
        package_content = base64.b64encode(f.read()).decode("utf-8")
    print(f"  Base64 编码完成，长度: {len(package_content)} 字符")

    # 2. 构造 API 请求
    print("[2/4] 构造 API 请求...")

    secret_id = os.environ.get("TENCENT_SECRET_ID")
    secret_key = os.environ.get("TENCENT_SECRET_KEY")
    if not secret_id or not secret_key:
        print("  ❌ 缺少腾讯云凭证环境变量")
        print("  请设置: export TENCENT_SECRET_ID=xxx && export TENCENT_SECRET_KEY=xxx")
        sys.exit(1)

    cred = credential.Credential(secret_id, secret_key)
    http_profile = HttpProfile()
    http_profile.reqTimeout = 300  # 5 分钟超时（镜像包大）
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    client = tcb_client.TcbClient(cred, "ap-shanghai", clientProfile=client_profile)

    # 3. 使用 from_json_string 绕过 SDK 序列化 bug（Dockerfile → Ockerfile）
    print("[3/4] 调用 UpdateCloudRunServer API...")

    env_var_list = [{"Name": k, "Value": v} for k, v in ENV_VARS.items() if v]

    request_body = {
        "EnvId": ENV_ID,
        "ServiceName": SERVICE_NAME,
        "DeployType": "package",
        "DeployInfo": {
            "Package": {
                "PackageVersion": f"v{int(time.time())}",
                "PackageName": "feedback-api.tar.gz",
                "PackageContent": package_content,
            }
        },
        "ReleaseType": "FULL",
        "RunId": "",
        "Configuration": {
            "EnvVariables": env_var_list,
        },
    }

    # ⚠️ 关键：直接用 JSON 构造请求体，绕过 SDK 的 from_dict() 序列化 bug
    # SDK 会把 Dockerfile 字段序列化为 Ockerfile（首字母 D 被吞掉）
    req = models.UpdateCloudRunServerRequest()
    req.from_json_string(json.dumps(request_body))

    # 4. 发送请求
    try:
        resp = client.UpdateCloudRunServer(req)
        print("  ✅ API 调用成功！")
        print(f"  RequestId: {resp.RequestId}")
        result = json.loads(resp.to_json_string())
        print(f"  响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
    except Exception as e:
        print(f"  ❌ API 调用失败: {e}")
        sys.exit(1)

    print("\n🎉 部署请求已发送！CloudRun 正在发布新版本...")
    print("  通常需要 2-5 分钟生效，可在 CloudBase 控制台查看版本状态")


if __name__ == "__main__":
    main()
