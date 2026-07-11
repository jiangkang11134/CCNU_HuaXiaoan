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

#### 2. 启动服务

使用生产环境配置文件启动：

```bash
# 仅启动核心服务（CPU 模式）
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

# 启动所有服务（包含 GPU OCR）
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile all up -d --build
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
# 已在 Yuxi 目录内时
git pull
./scripts/deploy_prod.sh
```

服务器首次部署或必须重新拉取代码时，优先复用已有数据目录。已有 `/root/Yuxi/docker/volumes` 数据的服务器不要改 `YUXI_DATA_DIR`，否则旧库和文件不会被当前容器挂载到：

```bash
export YUXI_DIR=/root/Yuxi
export YUXI_REPO=https://gitee.com/lqxtime/yuxi.git

if [ ! -d "$YUXI_DIR/.git" ]; then
  git clone "$YUXI_REPO" "$YUXI_DIR"
fi

cd "$YUXI_DIR"
git pull
./scripts/init_prod_env.sh
./scripts/deploy_prod.sh
```

全新服务器可以在执行 `./scripts/init_prod_env.sh` 前设置 `YUXI_DATA_DIR=/root/yuxi-data`，把生产数据放到代码目录外。

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

1. 初始化系统并创建系统管理员。
2. 在后台完成模型供应商、解析服务和知识库配置。
3. 创建或导入用户，并按角色、部门配置可访问范围。
4. 重新上传知识库文件，等待解析和索引完成。
5. 在前台使用问答入口验证检索和回答效果。

## 许可证

查看 [LICENSE](LICENSE) 文件。
