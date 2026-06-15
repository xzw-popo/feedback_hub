# ==============================================================================
# feedback_hub CloudRun Dockerfile
# 用途：将 FastAPI 只读 API 部署到 CloudBase CloudRun（容器模式）
# ==============================================================================

FROM python:3.13-slim

WORKDIR /app

# 安装 Python 依赖（pymysql 等已在 requirements.txt 中）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码 + entrypoint
COPY feedback_hub/ feedback_hub/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# CloudRun 注入 PORT 环境变量，默认 9000
ENV PORT=9000
# 数据库模式：mysql | sqlite（默认 mysql）
ENV DB_MODE=mysql
# MySQL 连接串（部署时通过 CloudBase 环境变量注入）
ENV MYSQL_HOST=""
ENV MYSQL_PORT=3306
ENV MYSQL_USER=""
ENV MYSQL_PASSWORD=""
ENV MYSQL_DATABASE="feedback_hub"
# CORS 允许的 Origin 列表（逗号分隔）
ENV CORS_ORIGINS=""

EXPOSE ${PORT}

# 启动 uvicorn 服务
CMD ["./entrypoint.sh"]
