# Commerce Copilot

> 基于 LangGraph、DeepSeek 和 PostgreSQL 构建的可观测多 Agent 电商助手。

Commerce Copilot 面向商品发现、订单售后和普通咨询。项目不是把所有逻辑塞进一次模型调用，而是让 Supervisor 拆分任务，领域 Agent 并发执行确定性工具，Grounding 校验事实后再生成最终回答。

```text
多意图拆分 · 并发 Agent · Hybrid Retrieval · RRF · Grounding
多轮记忆 · 用户偏好 · PostgreSQL · Alembic · 可观测 Trace
```

## 为什么值得做

普通 LLM 电商助手容易混淆商品与售后任务、编造价格库存，并且无法解释错误发生在哪个环节。本项目重点解决：

- **任务隔离：** Product Scout、Order Care、Conversation Guide 使用统一契约独立工作。
- **约束正确：** 品类、预算、库存是数据库硬约束，场景和关注点只影响相关性排序。
- **事实可信：** 商品、订单和政策事实必须通过 Claim–Evidence Grounding。
- **可降级：** DeepSeek 未配置、超时或输出非法时自动切换本地确定性逻辑。
- **可观察：** API 返回 Agent、耗时、规划来源、Grounding 和降级原因。
- **可演进：** Repository、向量器和 Agent Handler 均保留替换边界。

## 已实现能力

| 领域 | 能力 |
|---|---|
| 多 Agent | LangGraph Supervisor、`Send` 并发 fan-out、结果汇合与故障隔离 |
| LLM | DeepSeek 结构化任务规划、证据化回答、本地 Planner/Composer 降级 |
| 检索 | FTS/BM25、离线向量召回、RRF 融合、硬约束过滤、候补推荐 |
| 可信回答 | Evidence、Claim、字段级 Grounding、失败关闭 |
| 多轮能力 | conversation_id、商品序号指代、确定性对比、预算放宽确认 |
| 个性化 | 用户预算、品类、品牌、场景和关注点持久化，本轮条件优先 |
| 数据层 | PostgreSQL、SQLAlchemy Repository、Alembic；测试支持临时 SQLite |
| 安全 | PBKDF2 密码哈希、签名 Token、订单归属和会话隔离 |
| 工程质量 | 73 个自动化测试、离线检索评测、Web Trace、真实 Git 迭代历史 |

## 项目文档

- [系统架构与设计决策](docs/architecture.md)
- [五分钟面试演示手册](docs/demo-guide.md)
- [简历描述与高频面试题](docs/interview-guide.md)
- [后续功能路线图](docs/roadmap.md)
- [产品需求与边界](docs/product-spec.md)

## 当前状态与后续规划

当前版本已经形成可运行、可测试的完整闭环。下一阶段将依次补充 SSE 实时执行事件、结构化日志与指标、BGE + pgvector、Redis 缓存与限流、容器化部署和压力测试。规划功能与已实现能力严格分开，详细验收标准见 [roadmap](docs/roadmap.md)。

## 架构

```text
HTTP request
    │
    ▼
Supervisor ──拆分任务──┬── Product Scout
                       ├── Order Care
                       └── Conversation Guide
                                  │
                                  ▼
                              Composer
                                  │
                                  ▼
                         answer + execution trace
```

领域 Agent 只接收自己的任务，不共享可变中间状态。多个任务由 LangGraph fan-out 并发执行，Composer 只消费标准化的 `AgentReport`。

## 核心目录

```text
src/commerce_copilot/
├─ orchestrator.py      # LangGraph 状态图、并发分发与汇合
├─ planning.py          # DeepSeek Planner 与本地降级
├─ agents.py            # Product Scout / Order Care / Conversation Guide
├─ performance.py       # Agent QPS、成功率与分位延迟压测
├─ retrieval.py         # 关键词、向量召回与 RRF
├─ personalization.py   # 偏好合并、显式条件优先级
├─ grounding.py         # Claim–Evidence 校验
├─ answering.py         # 证据化回答与本地降级
├─ database.py          # SQLAlchemy 表结构与 Repository
├─ api.py               # FastAPI、鉴权依赖与接口
└─ web/                 # 可观测对话界面
```

完整数据流和设计理由见 [架构文档](docs/architecture.md)。

## 本地运行

PowerShell：

```powershell
Set-Location "E:\ProgramFiles\PythonProject\commerce-copilot"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m commerce_copilot.main
```

打开 `http://127.0.0.1:8010/` 使用对话与 Agent 可观测界面；`http://127.0.0.1:8010/docs` 提供 API 调试文档。

推荐用下面的问题验证多 Agent 并发：

```text
推荐一款500元以内的通勤耳机，同时告诉我发货规则
```

更多演示场景见 [五分钟演示手册](docs/demo-guide.md)。

如需启用 DeepSeek，将 `.env.example` 复制为 `.env`，只在 `.env` 中填写新生成的 `LLM_API_KEY`。没有配置 Key、请求超时或模型输出校验失败时，Supervisor 会自动使用本地规划器，并在响应的 `trace.planning_source` 中标记降级状态。

首次本地运行可使用演示账号 `demo / demo12345` 查询演示订单。部署到共享环境前必须在 `.env` 中设置足够长且随机的 `AUTH_SECRET`，并删除或禁用演示账号。

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

运行离线检索评测：

```powershell
.\.venv\Scripts\python.exe -m commerce_copilot.evaluation --k 3 --details
```

评测使用固定查询集，分别执行 FTS5 基线和混合检索，并输出 Recall@K、Hit Rate@K 与 MRR。固定小数据集用于回归测试和演示工程链路，不替代生产数据上的人工相关性标注。

运行 Agent 性能评测：

```powershell
.\.venv\Scripts\python.exe -m commerce_copilot.performance --agent all --requests 300 --concurrency 20 --warmup 10 --output artifacts\agent-performance.json
```

性能评测直接并发调用 Agent 执行入口，输出整体及各角色的 QPS、成功率、失败数与 P50/P95/P99 延迟。QPS 按“完成请求数 ÷ 整段压测墙钟时间”计算；当前结果衡量 Agent 执行层，不包含浏览器渲染和 HTTP 网络耗时。建议分别使用 1、5、10、20 等并发度多轮运行，观察吞吐增长、尾延迟和数据库连接池瓶颈，不把单次本机结果当作生产容量结论。

自动执行并发阶梯压测：

```powershell
.\.venv\Scripts\python.exe -m commerce_copilot.performance --agent product_scout --requests 1000 --sweep 1,5,10,20,50,100 --warmup 20 --output artifacts\product-scout-sweep.json
```

阶梯报告会给出实测峰值，以及“达到至少 90% 峰值 QPS 且成功率不低于 99%”的最低推荐并发度。Order Care 默认覆盖发货政策、本人订单状态和非本人订单三条场景；也可以使用 `--scenario order_policy`、`--scenario order_status_owned` 或 `--scenario order_status_not_owned` 单独测试。场景成功率不仅检查 Agent 是否报错，还会检查回答中是否出现预期的关键结果。

运行完整编排链路阶梯压测：

```powershell
.\.venv\Scripts\python.exe -m commerce_copilot.workflow_performance --requests 300 --sweep 1,5,10,20,50 --warmup 10 --output artifacts\workflow-sweep.json
```

完整链路评测覆盖本地 Planner、LangGraph 分发与汇合、Agent、Grounding、回答组织、会话归属与消息持久化，并在计时结束后清理压测会话。默认使用本地确定性 Planner 和 Composer，防止批量请求产生外部 LLM 费用；只有显式添加 `--allow-llm` 才会使用当前配置的模型。报告同时记录业务成功率、Grounding 失败数、Agent 失败数以及规划和回答来源。单 Agent 报告用于定位执行层瓶颈，完整链路报告用于衡量一次真实对话的服务能力，两者不能混为同一个 QPS。

## 实时执行轨迹

`POST /v1/turns/stream` 使用 SSE 逐步输出 `planning_started`、`planning_completed`、`agent_started`、`agent_completed`、`grounding_completed`、`answer_completed` 和最终 `complete` 事件。每轮请求生成独立 `trace_id`，最终响应还包含 Planner、各 Agent、Grounding 和 Composer 的分阶段耗时。网页使用带 Bearer Token 的 `fetch` 流读取这些事件，在回答完成前实时更新右侧执行链路；原有 `POST /v1/turns` 非流式接口继续保留。

## 项目原则

1. 事实必须来自工具或数据源，不由模型凭空补充。
2. 每个 Agent 的输入、输出、耗时和错误都可以追踪。
3. 硬约束由代码执行，模型只处理语义理解和自然语言组织。
4. 从第一天保留测试、评测指标和真实 Git 开发历史。

## 数据层

配置 `DATABASE_URL` 后运行数据写入 PostgreSQL；未配置时使用 `runtime/commerce.db` 方便快速体验。首次商品检索时系统会导入一组原创演示商品；运行时数据库文件不会提交到 Git。

Product Scout 的第一阶段检索流程为：

```text
用户需求 → 预算提取 → SQL 价格/库存过滤
                    ├─ FTS5 / BM25 召回
                    └─ 本地向量召回
                              ↓
                         RRF 排名融合
                              ↓
                           Evidence
```

离线向量通道默认使用无外部依赖的中文二元组特征哈希，确保项目首次运行不下载模型；`Vectorizer` 协议允许后续替换为 BGE 等语义 Embedding。该默认实现用于验证向量索引与融合链路，不冒充预训练语义模型。

每个推荐结果都会返回商品编号以及价格、评分和库存事实，后续 Grounding 模块只允许根据这些 Evidence 生成确定性描述。

## 证据化回答生成

通过 Grounding 的 Agent 报告会交给 DeepSeek 组织自然语言。模型必须同时返回回答和结构化 Claims，每条事实都要标注 Evidence 引用；代码会重新校验引用、字段和值。未知引用、字段缺失、值被修改、接口超时或 JSON 不合法时，系统自动返回确定性的本地答案。响应中的 `trace.answer_source` 和 `trace.composer_error` 用于观察生成来源与降级原因。

## 多轮会话

`POST /v1/turns` 接受可选的 `conversation_id`，未提供时由服务端生成并在响应中返回。每轮用户消息和回答写入关系型数据库；Planner 最多读取最近 6 轮，在识别到指代或追问表达时补充上下文。网页会在当前浏览器标签页保存会话编号，点击“清空会话”会开启新的会话。

Order Care 会依据当前一轮的问题选择状态、下单时间或商品明细，避免状态追问重复输出整张订单；政策识别也只检查当前消息，不会被历史中的“退货”或“物流”关键词重复触发。

商品推荐的 Evidence 会按展示顺序随会话保存。后续出现“第一款、第二款、第三款”等表达时，Planner 将序号映射回上一轮真实商品编号；Product Scout 直接按编号读取商品，避免重新检索造成排序变化。多个序号可以组成同一轮商品对比。

商品对比由确定性代码计算最低价格、最高评分和价差，并生成 `derived_comparison` Evidence。对比结论和普通商品事实使用同一套 Claim–Evidence Grounding，不依赖模型进行算术计算。

## 用户鉴权

`POST /v1/auth/register` 和 `POST /v1/auth/login` 接收 JSON 用户名与密码，密码使用带随机盐的 PBKDF2-SHA256 哈希保存。登录返回带过期时间和 HMAC-SHA256 签名的 Bearer Token；`POST /v1/turns` 必须携带该 Token。订单查询同时校验订单归属，会话编号在首次使用时绑定当前用户，其他用户复用该编号会得到 `403`。网页将 Token 保存在当前标签页的 `sessionStorage`，关闭标签页后不会长期保留。

## 用户偏好与个性化

登录用户可以点击网页右上角“偏好设置”，保存预算、偏好品类、品牌、使用场景和关注点；也可以通过 `GET /v1/me/preferences` 和 `PUT /v1/me/preferences` 操作。偏好存入 PostgreSQL 的 `user_preferences` 表，不依赖进程内存。

Supervisor 只用历史偏好补充本轮缺失的条件：用户本轮明确说出的预算或品类具有更高优先级；“第一款”等直接商品指代不会被历史偏好再次过滤。实际采用的偏好会出现在响应的 `trace.applied_preferences` 和网页右侧“本轮个性化”区域中。没有有效偏好的用户只输入“推荐商品”时，Product Scout 会追问品类、预算或场景，而不是执行无意义检索。

严格检索无结果且确认预算是唯一阻塞条件时，Product Scout 会保持品类与库存约束，只查询价格差距最小的候补商品。候补结果明确标记 `match_status=near_miss`，超预算金额由代码计算并接受 Grounding 校验，不会伪装成符合预算的商品；用户下一轮明确接受提高预算后，Planner 才会按保存的商品编号继续介绍。

## PostgreSQL 数据存储

运行环境通过 `DATABASE_URL` 连接 PostgreSQL，SQLAlchemy 负责连接池、事务和跨数据库的数据访问。用户、商品、订单、政策、会话、Evidence 及资源归属都持久化在 PostgreSQL。测试仍可向 `CatalogRepository` 传入临时 SQLite 路径，因此测试不依赖本机 PostgreSQL，也不会污染开发数据。

本机辅助脚本：

```powershell
.\scripts\setup_postgres_database.ps1
.\scripts\configure_postgres_env.ps1
```

第一个脚本创建项目数据库与最小权限应用账号；第二个脚本验证账号并将连接地址写入 Git 忽略的 `.env`。应用只使用 `commerce_copilot` 账号，不使用 `postgres` 超级用户运行。

当前 Windows 本地安装采用免 Docker 的二进制模式。电脑重启后，先执行 `.\scripts\start_postgres.ps1`；需要停止数据库时执行 `.\scripts\stop_postgres.ps1`。两个脚本都是幂等的，重复执行不会重复启动或停止。

数据库表结构由 Alembic 管理。首次创建项目数据库或拉取到新的迁移文件后执行：

```powershell
.\scripts\migrate_database.ps1
```

也可以直接运行：

```powershell
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

`upgrade head` 将数据库升级到最新版本，`current` 显示当前版本，`check` 检查 SQLAlchemy 模型是否存在尚未生成的结构变化。PostgreSQL 运行时不会再调用 `metadata.create_all()` 偷偷修改表结构；如果没有执行迁移，应用会明确提示先运行 `alembic upgrade head`。测试使用临时 SQLite，并会验证第一版迁移可以从空数据库完整升级和回滚。

## Grounding

领域 Agent 在生成回复时同步输出结构化 Claim。Composer 在返回答案前，将每条 Claim 的引用编号、字段和值与 Evidence 对齐；缺少证据、缺少字段或值不一致都会产生 Grounding Issue。未通过校验的 Agent 报告不会进入最终回答，但其他并发分支的有效结果仍可保留。

## 订单与售后

Order Care Agent 能从用户消息中提取演示订单号，查询订单状态与商品明细，并按意图补充退货或发货政策。没有订单号时只提供通用政策并要求用户补充编号；不存在的订单不会生成虚构状态。演示编号为 `ORD-2026-1001` 和 `ORD-2026-1002`。

