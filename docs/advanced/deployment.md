# 生产部署指南

本文档介绍如何在生产环境中部署 Yuxi。

## 前置要求

- Docker Engine (v24.0+)
- Docker Compose (v2.20+)
- NVIDIA Container Toolkit（如需使用 GPU 服务）

::: warning 注意事项
1. 生产环境和开发环境建议使用不同的机器，避免端口和资源冲突
2. 虽然名为「生产环境」，但这只是基本配置，真正上线需要根据实际情况调整
3. 前端有调试面板（长按侧边栏触发），生产环境建议关闭
:::

## 部署步骤

### 1. 准备配置文件

为避免与开发环境冲突，生产环境建议使用 `.env.prod` 文件：

```bash
./scripts/init_prod_env.sh
```

编辑 `.env.prod`，设置生产环境启动前必须存在的部署级配置：

- `JWT_SECRET_KEY` / `YUXI_INSTANCE_ID`：由初始化脚本生成持久化随机值，不要每次部署变化
- `YUXI_DATA_DIR`：生产数据目录，建议放在代码目录外，例如 `/root/yuxi-data`
- `POSTGRES_PASSWORD` / `POSTGRES_URL`：修改默认数据库密码，并保持连接串一致
- `NEO4J_PASSWORD`：修改默认密码
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`：修改默认密钥
- `YUXI_CORS_ORIGINS`：仅跨域部署时设置

模型供应商 Key、OCR Key、Tavily 搜索 Key、URL 解析白名单、默认模型与默认 OCR 引擎不再建议写入 `.env.prod`。服务启动后，使用系统管理员登录后台，在「基本设置」和「模型供应商」中维护这些运行时业务配置。

`docker-compose.prod.yml` 默认把数据库、MinIO、Milvus、Neo4j、Redis、系统上传资源等数据挂载到 `${YUXI_DATA_DIR}`。更新或重新拉取代码时不要删除该目录，否则会丢失知识库文件、索引、用户数据和后台上传的品牌图片。

### 2. 启动服务

使用生产环境配置文件启动：

```bash
# 仅启动核心服务（CPU 模式）
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

# 启动所有服务（包含 GPU OCR）
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile all up -d --build
```

### 3. 验证部署

- Web 访问：http://localhost（直接通过 80 端口）
- API 健康检查：`curl http://localhost/api/system/health`

## 跨域（CORS）配置

`docker-compose.prod.yml` 默认把 `YUXI_ENV` 设为 `production`，后端在该环境下会按 `YUXI_CORS_ORIGINS` 显式声明允许的来源。**未配置时返回空列表，浏览器跨域请求会被拒绝**。生产部署前请根据前端与 API 的相对位置选择策略：

| 部署形态 | 推荐配置 |
|----------|----------|
| 前端与 API 同源（Nginx 同端口反代） | 不需要设置，留空即可 |
| 前端与 API 跨域部署 | `YUXI_CORS_ORIGINS=https://your-frontend.example.com` |
| 多个前端域名 | 逗号分隔，如 `https://a.example.com,https://b.example.com` |
| 完全放开（不推荐） | `YUXI_CORS_ORIGINS=*`，会自动关闭 credentials，登录态/JWT 无法跨域携带 |

开发环境（`YUXI_ENV=development` 且未设置该变量）默认允许 `http://localhost:5173` 与 `http://127.0.0.1:5173`，方便本地前后端独立启动调试。从 0.7.0 升级到 0.7.1 时，如果此前是跨域部署但未显式声明来源，必须补上 `YUXI_CORS_ORIGINS`，否则前端跨域请求会被拒绝。

## 维护与更新

### 更新代码

```bash
# 拉取最新代码
git pull

# 重新构建并启动
./scripts/deploy_prod.sh
```

### 查看日志

```bash
# API 日志
docker logs -f api-prod

# Nginx 访问日志
docker logs -f web-prod
```
