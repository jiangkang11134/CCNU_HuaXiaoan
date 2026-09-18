# 服务器部署环境需求清单

> 适用版本：`0.7.1.beta1`　分支：`main`　代码仓库：`https://github.com/jiangkang11134/CCNU_HuaXiaoan`
>
> 给部署同学：本清单只讲**服务器上要准备什么**，具体命令可直接复制执行。

## 一、结论先说

**服务器上只需要装 Docker。** Python、Node、PostgreSQL、Redis、Milvus、Neo4j、MinIO 全部由 Docker Compose 拉起，不需要在宿主机单独安装或配置。

⚠️ 注意：仓库 `README.md` 的生产部署示例里写的是 **gitee 地址**（`https://gitee.com/lqxtime/yuxi.git`），那是上游原仓库。**请改用下面的 GitHub 地址**，否则拉到的不是我们的代码。

## 二、服务器基础环境

| 项目 | 要求 | 说明 |
| --- | --- | --- |
| 操作系统 | Linux x86_64（Ubuntu 22.04 / 24.04 或同代发行版） | 需支持 `host.docker.internal`（Compose 已配置 `host-gateway`） |
| Docker Engine | **24.0+** | 硬性要求 |
| Docker Compose | **2.20+** | 命令形式为 `docker compose`（带空格），不是旧版 `docker-compose` |
| NVIDIA Container Toolkit | 可选 | **仅当**需要本地 GPU OCR（PaddleX）时才装，默认不启用 |
| 网络 | 需出公网 | 拉取镜像；LLM / Embedding 走外部 API（如 SiliconFlow），服务器必须能访问 |

### 硬件建议（经验值，非官方硬指标）

| 资源 | 建议 | 理由 |
| --- | --- | --- |
| 内存 | **16 GB 起，32 GB 稳妥** | Milvus 单机版官方建议 ≥8 GB，另有 Neo4j / PostgreSQL / Redis / MinIO / API / Worker 同机 |
| 磁盘 | **100 GB+** | Milvus 向量数据、MinIO 上传文件、Neo4j 数据、以及 `/app/models` 下的本地模型 |
| CPU | 4 核起 | 文档解析与入库是 CPU 密集 |
| GPU | 非必需 | 默认走 MinerU 云端 API；只有启用 PaddleX 本地 OCR（`--profile all`）才需要 |

## 三、端口占用（生产 compose 实际只暴露两个）

| 端口 | 服务 | 说明 |
| --- | --- | --- |
| **80** | web（Nginx + 前端静态资源） | 对外唯一入口，同时反代后端 API |
| **5432** | PostgreSQL | 已映射到宿主机，建议用防火墙收紧，不要对公网开放 |
| 8002 | sandbox-provisioner | 生产 compose 未映射，仅容器网络内访问 |

其余组件（Neo4j 7474/7687、Milvus 19530/9091、MinIO 9000/9001、Redis 6379、API 5050）在生产 compose 中**不对外暴露**，只在内部网络互通。

> 若 80 端口已被占用，部署前先处理，或改 `docker-compose.prod.yml` 里 web 的 `ports`。

## 四、已由镜像提供、无需在服务器安装

| 组件 | 版本 | 来源 |
| --- | --- | --- |
| Python | 3.13（`python:3.13-slim`） | `docker/api.Dockerfile` |
| uv | 0.11.26 | 同上 |
| Node.js | 24 | `docker/web.Dockerfile`；API 镜像也内置 Node 24 |
| pnpm | 最新版 | web 构建阶段安装 |
| PostgreSQL | 16 | `postgres:16` |
| Redis | 7-alpine | `redis:7-alpine` |
| Milvus | v2.5.6（standalone） | `milvusdb/milvus:v2.5.6` |
| etcd | v3.5.5（Milvus 依赖） | `quay.io/coreos/etcd:v3.5.5` |
| Neo4j | 5.26 | `neo4j:5.26` |
| MinIO | RELEASE.2023-03-20T20-16-18Z | `minio/minio` |

依赖锁定文件齐全：`backend/uv.lock`、`web/pnpm-lock.yaml`，构建可复现。

## 五、部署步骤

```bash
set -e

YUXI_DIR="/root/Yuxi"
YUXI_REPO="https://github.com/jiangkang11134/CCNU_HuaXiaoan"   # ← GitHub，不是 gitee

# 1. 首次拉代码
if [ ! -d "$YUXI_DIR/.git" ]; then
  git clone -b main "$YUXI_REPO" "$YUXI_DIR"
fi
cd "$YUXI_DIR"

# 2. 生成生产配置（首次会从 .env.template 生成 .env.prod 并自动填随机密钥）
./scripts/init_prod_env.sh

# 3. 按第七节检查并修改 .env.prod
vi .env.prod

# 4. 构建并启动
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build

# 5. 查看状态与健康检查
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
curl -i http://127.0.0.1/api/system/health
```

首次启动较慢（要拉 Milvus / Neo4j 等大镜像并构建前后端），健康检查有 180 秒 `start_period`，请耐心等待。

**后续更新代码**（保留数据）：

```bash
cd "$YUXI_DIR"
bash scripts/deploy_prod.sh        # 内部依次执行：停容器 → git pull → 生成配置 → 重建启动 → 健康检查
```

访问地址：

- 前台：`http://服务器IP`
- 后台：`http://服务器IP/back/login`

## 六、`.env.prod` 必须确认的项

`init_prod_env.sh` 会自动生成随机密钥，但以下几项**必须人工确认**：

| 变量 | 说明 | 注意 |
| --- | --- | --- |
| `JWT_SECRET_KEY` | 生产 JWT 密钥 | **生成后不要每次部署变化**，否则所有登录态失效 |
| `YUXI_INSTANCE_ID` | 实例 ID | 同上，要保持稳定 |
| `YUXI_DATA_DIR` | 生产数据目录 | **务必放在代码目录之外**（例如 `/root/yuxi-data`），否则重新拉代码会丢数据 |
| `POSTGRES_PASSWORD` / `POSTGRES_URL` | 数据库密码与连接串 | 两处密码**必须一致**，否则后端连不上库 |
| `NEO4J_PASSWORD` | 图数据库密码 | 改了要同步 `NEO4J_URI` 侧配置 |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | 对象存储密钥 | 不要用默认的 `minioadmin` |
| `HOST_IP` | 服务器对外 IP/域名 | 容器生成外部可达 URL 时使用 |
| `YUXI_CORS_ORIGINS` | 跨域来源 | 前后端同域（走 Nginx）**留空即可** |
| `YUXI_SUPER_ADMIN_NAME` / `_PASSWORD` | 初始超管 | 部署后请立即改密码 |
| `YUXI_ENV` | 应为 `production` | compose 已固定，无需手动改 |

## 七、部署完成后还要在后台做的配置

**模型相关的配置不在 `.env` 里，而是在数据库 + 管理后台。** 全新数据库里没有任何模型，必须配置后系统才能正常对话：

1. 用超管登录 `http://服务器IP/back/login`
2. 进入「**模型供应商**」：为每个供应商填写 `Base URL` 与 `API Key`（密钥只在服务端保存，接口不回显明文）
3. 在该供应商下「**管理模型**」启用需要的对话模型 / Embedding 模型
4. 在「**基本设置**」设置默认对话模型、默认 Embedding 模型
5. 如需 OCR：默认走 MinerU 云端 API，在后台填 MinerU Key；只有要用本地 PaddleX 才需要 GPU 并启用 `--profile all`

### 关于「主 + 两个备用」故障切换（本次新增）

- **默认关闭**，不影响现有部署，不开启时行为与之前完全一致。
- 不增加任何新服务、新端口、新环境变量，配置为数据库里的 `model_routing` 记录。
- 若需要：后台「模型路由与负载均衡」里选好**回答模型**、**备用模型 1/2**，打开「启用主备故障切换」保存即可。每个模型的 `base_url`/`api_key` 仍在其所属供应商里配置。
- 出问题就在同一页面关掉开关，立即回退。

## 八、数据与备份注意事项

- **更新代码或重新部署，绝不要删除数据目录**（`YUXI_DATA_DIR`），否则丢失上传文件、向量索引、用户与配置。
- **不要执行 `docker compose down -v`**——`-v` 会删掉数据卷。
- 备份就是备份 `YUXI_DATA_DIR` 整个目录（PostgreSQL / Neo4j / Milvus / MinIO / Redis 数据都在里面）。
- `paddlex` 服务带 `profiles: [all]` 且需要 NVIDIA GPU，**默认不启动**；不要加 `--profile all` 除非确实要本地 GPU OCR。

## 九、常见问题排查

| 现象 | 检查 |
| --- | --- |
| 健康检查一直失败 | `docker compose ... logs api`；首次启动有 180 秒宽限期，Milvus 就绪较慢 |
| 80 端口起不来 | `lsof -i:80` 或 `netstat -tlnp | grep :80` 查占用 |
| 后端连不上数据库 | 确认 `POSTGRES_PASSWORD` 与 `POSTGRES_URL` 里的密码一致 |
| 对话报"未找到模型" | 模型未在后台「模型供应商」配置或未启用，见第七节 |
| 想降低资源占用 | 设 `LITE_MODE=true` 可跳过知识库/评测/图谱路由，但会牺牲相应功能 |
| 容器之间访问异常 | 确认 `NO_PROXY` 包含内部服务名；服务器若设了全局代理需放行 |
