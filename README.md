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
- [ ] 向量召回与混合排序
- [x] DeepSeek 结构化任务规划与本地降级
- [ ] DeepSeek 证据化回答生成
- [x] 结构化 Claim–Evidence Grounding 与失败关闭
- [ ] 离线评测和前端

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

打开 `http://127.0.0.1:8010/docs`，调用 `POST /v1/turns`。

如需启用 DeepSeek，将 `.env.example` 复制为 `.env`，只在 `.env` 中填写新生成的 `LLM_API_KEY`。没有配置 Key、请求超时或模型输出校验失败时，Supervisor 会自动使用本地规划器，并在响应的 `trace.planning_source` 中标记降级状态。

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## 项目原则

1. 事实必须来自工具或数据源，不由模型凭空补充。
2. 每个 Agent 的输入、输出、耗时和错误都可以追踪。
3. 硬约束由代码执行，模型只处理语义理解和自然语言组织。
4. 从第一天保留测试、评测指标和真实 Git 开发历史。

## 数据层

数据库默认写入 `runtime/commerce.db`。首次商品检索时，系统会自动创建商品、订单、订单项和服务政策表，并导入一组原创演示商品；运行时数据库文件不会提交到 Git。

Product Scout 的第一阶段检索流程为：

```text
用户需求 → 预算提取 → FTS5 严格召回 → SQL 价格/库存过滤 → BM25 排序 → Evidence
```

每个推荐结果都会返回商品编号以及价格、评分和库存事实，后续 Grounding 模块只允许根据这些 Evidence 生成确定性描述。

## Grounding

领域 Agent 在生成回复时同步输出结构化 Claim。Composer 在返回答案前，将每条 Claim 的引用编号、字段和值与 Evidence 对齐；缺少证据、缺少字段或值不一致都会产生 Grounding Issue。未通过校验的 Agent 报告不会进入最终回答，但其他并发分支的有效结果仍可保留。

