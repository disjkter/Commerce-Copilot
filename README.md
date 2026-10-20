# Commerce Copilot

一个从需求和代码层面独立实现的多 Agent 电商助手，面向商品发现、购买后服务和普通咨询。

这个仓库的目标不是堆叠模型调用，而是把一次回答拆成可观察、可测试的协作过程：Supervisor 将用户输入拆成任务，领域 Agent 独立执行，最后由 Composer 汇总结果。

## 当前里程碑

- [x] 定义独立的产品需求和架构边界
- [x] Supervisor 支持同一轮多意图拆分
- [x] Product Scout、Order Care、Conversation Guide 三个 Agent
- [x] LangGraph 并发分发与结果汇合
- [x] FastAPI 对话接口及 Agent Trace
- [x] 第一组自动化测试
- [x] SQLite 商品、订单与政策表结构
- [x] FTS5 商品召回、预算和库存硬过滤
- [x] 离线向量召回与 RRF 混合排序
- [x] DeepSeek 结构化任务规划与本地降级
- [x] DeepSeek 证据化回答生成与本地降级
- [x] 结构化 Claim–Evidence Grounding 与失败关闭
- [x] 订单状态与售后政策真实查询
- [x] 离线检索评测（Recall@K、Hit Rate@K、MRR）
- [x] 前端可观测界面
- [x] conversation_id 多轮会话记忆与上下文追问

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

## 本地运行

PowerShell：

```powershell
Set-Location "E:\ProgramFiles\PythonProject\commerce-copilot"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m commerce_copilot.main
```

打开 `http://127.0.0.1:8010/` 使用对话与 Agent 可观测界面；`http://127.0.0.1:8010/docs` 提供 API 调试文档。

如需启用 DeepSeek，将 `.env.example` 复制为 `.env`，只在 `.env` 中填写新生成的 `LLM_API_KEY`。没有配置 Key、请求超时或模型输出校验失败时，Supervisor 会自动使用本地规划器，并在响应的 `trace.planning_source` 中标记降级状态。

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

运行离线检索评测：

```powershell
.\.venv\Scripts\python.exe -m commerce_copilot.evaluation --k 3 --details
```

评测使用固定查询集，分别执行 FTS5 基线和混合检索，并输出 Recall@K、Hit Rate@K 与 MRR。固定小数据集用于回归测试和演示工程链路，不替代生产数据上的人工相关性标注。

## 项目原则

1. 事实必须来自工具或数据源，不由模型凭空补充。
2. 每个 Agent 的输入、输出、耗时和错误都可以追踪。
3. 硬约束由代码执行，模型只处理语义理解和自然语言组织。
4. 从第一天保留测试、评测指标和真实 Git 开发历史。

## 数据层

数据库默认写入 `runtime/commerce.db`。首次商品检索时，系统会自动创建商品、订单、订单项和服务政策表，并导入一组原创演示商品；运行时数据库文件不会提交到 Git。

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

`POST /v1/turns` 接受可选的 `conversation_id`，未提供时由服务端生成并在响应中返回。每轮用户消息和回答写入 SQLite；Planner 最多读取最近 6 轮，在识别到指代或追问表达时补充上下文。网页会在当前浏览器标签页保存会话编号，点击“清空会话”会开启新的会话。

## Grounding

领域 Agent 在生成回复时同步输出结构化 Claim。Composer 在返回答案前，将每条 Claim 的引用编号、字段和值与 Evidence 对齐；缺少证据、缺少字段或值不一致都会产生 Grounding Issue。未通过校验的 Agent 报告不会进入最终回答，但其他并发分支的有效结果仍可保留。

## 订单与售后

Order Care Agent 能从用户消息中提取演示订单号，查询订单状态与商品明细，并按意图补充退货或发货政策。没有订单号时只提供通用政策并要求用户补充编号；不存在的订单不会生成虚构状态。演示编号为 `ORD-2026-1001` 和 `ORD-2026-1002`。

