# 实验室安全教育智能对话平台

本项目是面向实验室安全教育场景的知识问答与安全隐患识别平台。系统以前台多普通用户问答、后台单管理员运营管理为核心业务形态，基于 Yuxi 的知识库、智能体、数据看板、用户管理、部门管理和模型管理能力进行了业务整合。

## 业务逻辑

- 前台只面向普通用户，用户身份收敛为学生、教职工等普通问答用户。
- 后台只允许系统管理员登录，负责系统配置、知识库维护、模型供应商、用户、部门、前端展示和运行日志管理。
- 系统是单租户业务，不做多租户运营隔离。
- 前台提供智能问答和实验室安全隐患识别入口。
- 后台不承载对话业务，不展示创建对话、搜索对话、最近对话等前台问答逻辑。
- 知识库、问答对、智能体执行、图谱、数据看板等底层能力沿用现有实现。
- 页面标题、侧边栏头像、Logo、前后台展示文字等由后台「前端配置」统一维护。

## 核心模块

| 模块 | 说明 |
| --- | --- |
| 前台问答 | 普通用户登录后进行知识库问答，按前端配置控制思考过程、引用文档等回答展示。 |
| 安全隐患识别 | 面向实验室安全图片的隐患识别页面，识别结果直接在页面展示。 |
| 数据总揽 | 后台默认进入的数据看板，展示系统运行和知识库相关统计。 |
| 知识库管理 | 创建知识库、上传文件、解析、入库、预览、生成知识导图和测试问题。 |
| 智能体管理 | 管理智能体、模型和提示词扩展配置。 |
| 基本设置 | 配置默认模型、解析配置、内容审查等系统运行参数。 |
| 前端配置 | 配置系统名称、网页标题、侧边栏头像、Logo、前台功能开关和回答展示。 |
| 用户管理 | 管理系统管理员和普通用户。 |
| 部门管理 | 管理用户部门信息。 |
| 运行日志 | 查看系统运行和调试信息。 |

## 知识库文件上传

后台知识库上传支持 PDF、Word、Excel、PPT、Markdown、文本、图片等文件类型。批量上传采用前端队列控制，同一时间最多上传 2 个文件，避免多个大 PDF 同时上传导致 API、MinIO 或容器内存压力过高。

推荐流程：

1. 在后台进入「知识库管理」。
2. 创建或打开目标知识库。
3. 点击上传，选择文件或文件夹。
4. 等待所有文件上传完成。
5. 点击提交处理，系统会创建后台解析任务。
6. 在任务中心或文件状态中查看解析、入库结果。

注意事项：

- 单文件默认最大 100 MB，生产 Nginx 和后端上传限制保持一致。
- PDF 上传阶段采用临时文件分块写入、按文件计算哈希、再从本地路径上传到 MinIO；上传成功后才会进入解析或入库任务。
- 重复内容文件会被明确拒绝，页面会提示已存在相同内容。
- PDF 或图片需要 OCR 时，请先在「基本设置 / 解析配置」配置对应 OCR 服务。
- 大批量文件建议分批上传，先确认解析配置和模型配置可用后再上传全量文件。
- 更新代码或重新部署不要删除数据目录，否则会丢失已上传文件、索引、用户和配置数据。

## 生产部署

### 前置要求

- Docker Engine 24.0+
- Docker Compose 2.20+
- 如需本地 GPU OCR 服务，额外安装 NVIDIA Container Toolkit

### 首次部署

```bash
set -e

YUXI_DIR="/root/Yuxi"
YUXI_REPO="https://gitee.com/lqxtime/yuxi.git"

if [ ! -d "$YUXI_DIR/.git" ]; then
  git clone "$YUXI_REPO" "$YUXI_DIR"
fi

cd "$YUXI_DIR"
./scripts/init_prod_env.sh
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
curl -i http://127.0.0.1/api/system/health
```

首次部署后访问：

- 前台：`http://你的域名或服务器IP`
- 后台：`http://你的域名或服务器IP/back/login`

### 生产配置

生产环境使用 `.env.prod`。首次部署会从 `.env.template` 生成配置文件，并写入必要的持久化配置。

必须重点确认：

- `JWT_SECRET_KEY`：生产 JWT 密钥，生成后不要每次部署变化。
- `YUXI_INSTANCE_ID`：实例 ID，生成后保持稳定。
- `YUXI_DATA_DIR`：生产数据目录，默认是 `./docker/volumes`。
- `POSTGRES_PASSWORD` / `POSTGRES_URL`：数据库密码和连接串必须一致。
- `NEO4J_PASSWORD`：Neo4j 密码。
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`：MinIO 密钥。
- `YUXI_CORS_ORIGINS`：前后端跨域部署时才需要设置。

模型 Key、OCR Key、Tavily Key、默认模型、默认 OCR 引擎、URL 解析白名单等运行时业务配置，优先在后台「基本设置」「模型供应商」中维护。

## 保留旧数据更新部署

日常更新使用下面命令。该命令不会删除 Docker volume，也不会删除 `${YUXI_DATA_DIR}`，会保留旧知识库文件、索引、用户和后台配置。

```bash
set -e

YUXI_DIR="/root/Yuxi"

echo "========================================"
echo "1. 进入 Yuxi 目录"
echo "========================================"
cd "$YUXI_DIR"

echo "========================================"
echo "2. 备份生产配置"
echo "========================================"
if [ -f .env.prod ]; then
  cp .env.prod ".env.prod.bak.$(date +%Y%m%d%H%M%S)"
else
  cp .env.template .env.prod
fi

echo "========================================"
echo "3. 拉取最新代码"
echo "========================================"
git fetch origin main
git reset --hard origin/main

echo "========================================"
echo "4. 初始化生产配置"
echo "========================================"
./scripts/init_prod_env.sh

echo "========================================"
echo "5. 重新构建并启动生产容器"
echo "========================================"
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build

echo "========================================"
echo "6. 等待 API 健康状态"
echo "========================================"
for i in $(seq 1 60); do
  STATUS="$(docker inspect -f '{{.State.Health.Status}}' api-prod 2>/dev/null || true)"
  echo "api-prod health: ${STATUS:-unknown}"
  if [ "$STATUS" = "healthy" ]; then
    break
  fi
  sleep 3
done

echo "========================================"
echo "7. 查看容器状态"
echo "========================================"
docker compose --env-file .env.prod -f docker-compose.prod.yml ps

echo "========================================"
echo "8. 健康检查"
echo "========================================"
curl -i http://127.0.0.1/api/system/health || true

echo "========================================"
echo "部署完成"
echo "前台: http://yuxi.mergeai.online"
echo "后台: http://yuxi.mergeai.online/back/login"
echo "========================================"
```

禁止在日常更新中执行：

```bash
docker compose down -v
rm -rf "$YUXI_DATA_DIR"
rm -rf /root/Yuxi/docker/volumes
```

这些命令会删除数据库、MinIO 文件、Milvus 索引等生产数据。

## 健康检查和排障命令

查看容器状态：

```bash
cd /root/Yuxi
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

检查 API：

```bash
curl -i http://127.0.0.1/api/system/health
docker inspect -f '{{.State.Health.Status}}' api-prod
```

查看日志：

```bash
docker logs -f api-prod
docker logs -f worker-prod
docker logs -f web-prod
docker logs -f minio
docker logs -f postgres
```

查看 80 端口占用：

```bash
ss -lntp | grep ':80 ' || true
```

检查生产配置关键项：

```bash
cd /root/Yuxi
grep -E '^(YUXI_ENV|YUXI_DATA_DIR|JWT_SECRET_KEY|YUXI_INSTANCE_ID|POSTGRES_URL|MINIO_URI|YUXI_CORS_ORIGINS)=' .env.prod
```

知识库上传失败时优先查看：

```bash
docker logs --tail=200 api-prod
docker logs --tail=200 worker-prod
docker logs --tail=200 minio
```

如果页面在「上传文件」阶段失败，重点查看 `api-prod` 与 `minio` 日志；如果文件已上传但解析失败，重点查看 `api-prod`、`worker-prod` 以及 OCR 服务配置。

如果浏览器提示 413 或文件刚开始上传就失败，优先检查 Web 容器内 Nginx 配置：

```bash
docker exec web-prod nginx -T | grep client_max_body_size
```

## 本地开发

```bash
cd Yuxi
./scripts/init.sh
docker compose up --build
```

## 目录说明

| 目录 | 说明 |
| --- | --- |
| `web` | 前端应用 |
| `backend` | 后端服务 |
| `docs` | 项目文档 |
| `scripts` | 初始化、部署和维护脚本 |
| `packages` | CLI 等附属包 |

## 许可证

查看 [LICENSE](LICENSE) 文件。

## t26.9.18 四模块更新

本轮对四个核心模块（知识图谱 / 记忆与上下文 / 自进化 / 个性化）做了实现补全与缺陷修复，并同步更新了两份说明文档。

### 知识图谱
- 抽取域收口到 `knowledge/graphs/extractors/domains.py`：未知域直接 raise（不静默回落到 generic），支持文件级域路由（`knowledge_files.doc_domain`，文件覆盖知识库）。
- 三族本体已建且白名单真拦截：`chemical` 11 节点 / 13 关系、`laboratory_management` 11 / 10、`policy` 8 / 10；越界实体连同其关系一并丢弃。
- 化学族补 MSDS 字段规则：新增 `PhysicalProperty` 节点承接「三、理化特性」；问号是来源标注而非「无数据」；平台元数据全部丢弃。
- 解析层：`docx` / `pptx` 内嵌图片纳入 OCR（原先只覆盖 PDF 页面与独立图片），识别失败不打断解析。

### 记忆与上下文
- 抽取从回答链路剥离为独立 ARQ 任务，围栏协议已删除；游标以 `agent_runs` 为基准，节流 / `no_content` 不推进游标。
- 通道与置信度由服务端裁定：稳定的 `user.*` 且置信度 ≥ 0.85 才 `confirmed`，通道只认 `user.*` / `project.*` 前缀。
- 注入顺序固定为 `[当前可用事实] → [个人资料] → [权威修正]`，`suppressed_fact_keys` 摘掉被个人资料覆盖的长期记忆键。
- 时间戳修复：全站 naive UTC 列改用 `format_naive_utc_datetime`，结束偏早 8 小时问题（仅 `knowledge/eval` 的 aware 列刻意保留旧函数）。

### 自进化
- 两层结构：收集层 `message_feedbacks`（所有人可点踩）与处置层 `correction_tickets`（仅 `faculty` / `system_admin` 自动建单）。
- 作用域分层 `kb_truth` / `user_pref` / `dept_rule` 判定在 `scope.py`，非法取值一律 422 不静默回落。
- 写回与撤回走统一审计实现 `event_log.record_event`；修正确认写回是唯一能改动知识库的动作。
- 手写 SQL 的 `text()` 条件必须自带外层括号，防止 `OR` 绕过作用域过滤（M1 红线）。

### 个性化
- 三层边界：L1 表达层 / L2 检索层可个性化，L3 结论层禁止个性化。
- 意图 → 回答口径：默认口径任意置信度生效，偏离默认的三个须 ≥ 0.7；阈值闸门写 `not (value >= θ)` 以兼容 JSON `NaN`。
- 个人资料：`users.uid` / `business_role` 注册不可改，`UserConfigSchema` 用 `extra="forbid"` 返回 422；`response_style=normal` 不产出任何指令。

### 交付文档
- `四大模块技术方案说明.docx`（教师版 v2）：面向无计算机背景，三套框架 + 大白话。
- `四大模块技术实现说明.docx`（技术版）：面向后端 / 架构，代码路径 + 阈值 + 否决方案表。

### 验证
新增回归守卫（时间戳序列化 19 例、内嵌图 OCR 6 例、抽取技能文档漂移 17 例）；全量 `test/unit` 仍维持既有失败基线（deepagents 0.7 API 漂移、缺 `pypdf` / `pymysql` / `langgraph`、Windows symlink、GBK 编码等本机环境问题，与本轮改动无交集）。
