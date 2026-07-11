# Yuxi 知识问答平台

本项目是面向前台多用户、后台单用户运营管理场景的知识问答平台。系统基于 Yuxi 的知识库、智能体、数据看板、用户角色与部门管理能力，整合了原业务中的前台问答入口、后台运营配置和权限控制逻辑。

## 业务定位

- 前台用户通过问答入口使用知识库与智能体能力。
- 后台管理员维护知识库、模型、用户、部门、权限和前台展示配置。
- 知识库文件需要在新系统中重新上传，系统按新的 Yuxi 数据结构重新入库。
- FAQ、问答对、智能体执行等基础能力沿用 Yuxi 现有实现。

## 快速开始

```bash
cd Yuxi
./scripts/init.sh
docker compose up --build
```

启动后访问本地 Web 服务，按初始化流程创建管理员账号并登录后台。

## 生产部署指南

本文档介绍如何在生产环境中部署 Yuxi。

### 前置要求

- Docker Engine (v24.0+)
- Docker Compose (v2.20+)
- NVIDIA Container Toolkit（如需使用 GPU 服务）

### 注意事项

1. 生产环境和开发环境建议使用不同的机器，避免端口和资源冲突。
2. 虽然名为「生产环境」，但这只是基本配置，真正上线需要根据实际情况调整。
3. 前端有调试面板（长按侧边栏触发），生产环境建议关闭。

### 部署步骤

#### 1. 准备配置文件

为避免与开发环境冲突，生产环境建议使用 `.env.prod` 文件：

```bash
cp .env.template .env.prod
```

编辑 `.env.prod`，设置强密码和必要的 API 密钥：

- `NEO4J_PASSWORD`：修改默认密码
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`：修改默认密钥
- `SILICONFLOW_API_KEY` 等模型密钥

#### 2. 启动服务

使用生产环境配置文件启动：

```bash
# 仅启动核心服务（CPU 模式）
docker compose -f docker-compose.prod.yml up -d --build

# 启动所有服务（包含 GPU OCR）
docker compose -f docker-compose.prod.yml --profile all up -d --build
```

#### 3. 验证部署

- Web 访问：`http://localhost`（直接通过 80 端口）
- API 健康检查：

```bash
curl http://localhost/api/system/health
```

### 跨域（CORS）配置

`docker-compose.prod.yml` 默认把 `YUXI_ENV` 设为 `production`，后端在该环境下会按 `YUXI_CORS_ORIGINS` 显式声明允许的来源。未配置时返回空列表，浏览器跨域请求会被拒绝。生产部署前请根据前端与 API 的相对位置选择策略：

| 部署形态 | 推荐配置 |
| --- | --- |
| 前端与 API 同源（Nginx 同端口反代） | 不需要设置，留空即可 |
| 前端与 API 跨域部署 | `YUXI_CORS_ORIGINS=https://your-frontend.example.com` |
| 多个前端域名 | 逗号分隔，如 `https://a.example.com,https://b.example.com` |
| 完全放开（不推荐） | `YUXI_CORS_ORIGINS=*`，会自动关闭 credentials，登录态/JWT 无法跨域携带 |

开发环境（`YUXI_ENV=development` 且未设置该变量）默认允许 `http://localhost:5173` 与 `http://127.0.0.1:5173`，方便本地前后端独立启动调试。从 0.7.0 升级到 0.7.1 时，如果此前是跨域部署但未显式声明来源，必须补上 `YUXI_CORS_ORIGINS`，否则前端跨域请求会被拒绝。

### 维护与更新

更新代码：

```bash
# 拉取最新代码
git pull

# 重新构建并启动
docker compose -f docker-compose.prod.yml up -d --build
```

查看日志：

```bash
# API 日志
docker logs -f api-prod

# Nginx 访问日志
docker logs -f web-prod
```

## 目录说明

| 目录 | 说明 |
| --- | --- |
| `web` | 前端应用 |
| `backend` | 后端服务 |
| `docs` | 本地项目文档 |
| `scripts` | 初始化、部署和维护脚本 |
| `packages` | CLI 等附属包 |

## 使用说明

1. 初始化系统并创建超级管理员。
2. 在后台完成模型供应商、解析服务和知识库配置。
3. 创建或导入用户，并按角色、部门配置可访问范围。
4. 重新上传知识库文件，等待解析和索引完成。
5. 在前台使用问答入口验证检索和回答效果。

## 许可证

查看 [LICENSE](LICENSE) 文件。
