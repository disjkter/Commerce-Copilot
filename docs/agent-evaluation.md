# Agent 自动化评测

## 目标

这套评测回答一个核心问题：修改 Prompt、检索策略、意图识别或 LangGraph 编排后，新版本是否比旧版本更好。

`evals/agent_cases.jsonl` 当前包含 132 条版本化案例，覆盖 11 类场景：

- 商品咨询、商品推荐与商品比较
- 模糊意图与缺少必要信息
- 订单查询与售后政策
- 多 Agent 协作与多轮指代
- 工具超时、模型格式错误等故障注入
- 无证据问题与安全边界

## 为什么不使用逐字标准答案

生成式回答允许措辞变化，因此案例保存的是可执行的预期行为：

- 应选择哪些 Agent
- 应识别哪些 intent 和 focus
- 必须或禁止引用哪些 Evidence
- 是否必须有证据、Grounding 是否通过
- 哪个 Agent 应在故障注入下失败
- 回答必须包含或禁止包含哪些关键内容
- Planner 或 Composer 是否应进入降级路径

只有所有硬检查通过，案例才算通过；同时保留单项得分，便于判断“部分改善”。

## 数据格式

```json
{
  "case_id": "multi_turn_001",
  "category": "multi_turn",
  "difficulty": "medium",
  "turns": ["推荐通勤耳机", "第二款多少钱？"],
  "expected": {
    "agents": ["product_scout"],
    "intents": ["product"],
    "focus": "price",
    "required_evidence_refs": ["CP-AUD-102"],
    "grounding_passed": true
  },
  "tags": ["context", "reference_resolution"]
}
```

## 运行方式

先运行少量本地确定性案例：

```powershell
$env:PYTHONPATH="$PWD\src"; python -m commerce_copilot.agent_evaluation --limit 10
```

运行完整本地基线并保存报告：

```powershell
$env:PYTHONPATH="$PWD\src"; python -m commerce_copilot.agent_evaluation --output artifacts/evals/baseline.json
```

只评测某一类别：

```powershell
$env:PYTHONPATH="$PWD\src"; python -m commerce_copilot.agent_evaluation --category multi_turn --include-passed
```

调用真实 DeepSeek 评测：

```powershell
$env:PYTHONPATH="$PWD\src"; python -m commerce_copilot.agent_evaluation --live-llm --output artifacts/evals/live.json
```

修改 Prompt 或编排后，与基线比较：

```powershell
$env:PYTHONPATH="$PWD\src"; python -m commerce_copilot.agent_evaluation --baseline artifacts/evals/baseline.json --output artifacts/evals/candidate.json
```

输出中的质量指标以正增量为改进，`p95_latency_ms` 以负增量为改进；`regressed_cases` 必须逐条复盘。

每条案例默认有 30 秒独立超时。单案例发生未处理异常或超时会记录为 `execution_error`，但不会中断剩余数据集；可以通过 `--case-timeout-seconds` 调整。

## 核心指标

| 指标 | 含义 |
|---|---|
| `pass_rate` | 所有硬检查均通过的案例比例 |
| `average_score` | 全部单项检查的平均完成度 |
| `routing_accuracy` | Agent 路由准确率 |
| `intent_accuracy` | 结构化意图识别准确率 |
| `evidence_accuracy` | 必要 Evidence 命中率 |
| `grounding_accuracy` | Grounding 结果符合预期的比例 |
| `p50/p95_latency_ms` | 端到端案例延迟分位数 |

## 数据维护规则

1. 失败案例先加入数据集，再修改 Prompt 或代码。
2. 不为提高分数而删除困难样本。
3. 新功能必须同时增加正常、边界、无证据和故障案例。
4. 本地模式用于快速回归，Live LLM 模式用于模型与 Prompt 评测。
5. 基线和候选版本必须使用同一数据集、数据库快照和模型配置。
