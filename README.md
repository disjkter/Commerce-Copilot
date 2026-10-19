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
- [ ] SQLite 商品与订单数据层
- [ ] FTS5 与向量混合检索
- [ ] DeepSeek 结构化意图识别与回答生成
- [ ] 引用校验、离线评测和前端

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

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## 项目原则

1. 事实必须来自工具或数据源，不由模型凭空补充。
2. 每个 Agent 的输入、输出、耗时和错误都可以追踪。
3. 硬约束由代码执行，模型只处理语义理解和自然语言组织。
4. 从第一天保留测试、评测指标和真实 Git 开发历史。

