# 多轮上下文压缩与分层记忆

早期版本每次只读取最近 6 轮，窗口之外的内容直接不可见。这种做法能限制 Prompt 长度，但属于截断，不是压缩，也无法支持跨越多轮无关对话的业务指代。

当前上下文与长期画像由四层组成：

1. **原始会话层**：`conversation_turns` 和 `conversation_turn_evidence` 保留完整用户消息、回答和 Evidence，支持审计与问题回放。
2. **近期工作记忆**：Planner 默认读取最近 4 轮原始对话，保留当前语义、语气和短期指代。
3. **结构化长期记忆**：窗口外历史增量合并到 `conversation_memories`，保存业务主题、订单号、运单号、售后工单、最近预算和最近一组商品 Evidence。
4. **原子记忆项**：`memory_items` 按用户或会话保存偏好、约束和实体；每条记录包含类型、来源 Turn、Evidence、置信度、生效区间、状态与被替代版本。

Planner 实际接收的是：

```text
结构化历史摘要（如果存在） + 最近 4 轮原始对话 + 当前消息
```

## 压缩过程

每轮回答和 Evidence 持久化后，系统查找近期窗口之外、且尚未压缩的 Turn：

1. 根据 `compacted_through_turn_id` 实现增量处理，避免重复压缩。
2. 使用确定性正则和业务字段提取订单、物流及售后实体。
3. 使用预算解析器保存最近预算硬约束。
4. 保存最近一组商品 Evidence 及顺序，使“第二款怎么样”仍能映射到正确商品。
5. 摘要文本限制为 1000 字符，实体列表和 Evidence 数量设置上限。
6. 原始 Turn 不删除，压缩结果仅作为 Planner 的低成本上下文视图。

压缩不调用 LLM，因此不会引入额外 Token、网络延迟或摘要幻觉。未来如增加模型摘要，只能补充自然语言信息，订单号、价格和商品引用等业务事实仍应由确定性提取结果控制。

## 原子记忆与冲突处理

摘要用于给 Planner 提供有界上下文，原子记忆用于可更新、可撤销的长期事实。当前高精度抽取覆盖预算、订单号、商品 Evidence，以及用户明确表达的商品功能偏好。例如“我不喜欢入耳式，但更喜欢头戴式”会形成两条独立语义记忆。

写入相同 `scope + key` 的不同新值时，Repository 在同一事务内：

1. 将旧记录从 `active` 更新为 `invalidated` 并写入 `valid_to`；
2. 创建新记录并用 `supersedes_memory_id` 指向旧版本；
3. 保留旧记录及原始 `source_turn_id`，用于审计而不是物理覆盖；
4. 使用数据库部分唯一索引防止并发产生多个活跃版本。

用户级明确偏好可以跨会话参与商品召回。正向偏好增强检索词，负向偏好对候选结果执行硬过滤；带有“这次/本次/当前”等词的偏好只写入当前会话作用域。订单状态、退款金额等业务事实不从自由文本写入长期记忆，仍必须来自经过验证的业务工具。

用户可通过以下接口查看或撤销记忆：

```text
GET    /v1/memories
GET    /v1/memories?include_invalidated=true
DELETE /v1/memories/{memory_id}
```

`DELETE` 执行逻辑失效而不是物理删除，活跃召回会立即停止使用该记录，同时保留来源审计链。

## 指代恢复

Planner 不再机械使用“上一条消息”。当当前输入包含“它”“第二款”“继续”“到哪了”等追问表达时，会从最近历史向前寻找带 Evidence、业务实体或结构化摘要的相关 Turn。因此中间即使存在多轮“你好”“谢谢”，仍可恢复之前的商品或订单。

## 可观测性

每次响应的 `trace.memory` 包含：

- `summary_loaded`：本轮是否读取了长期记忆。
- `recent_turns`：本轮加载的近期原始轮数。
- `compacted_turns`：本轮新压缩的轮数。
- `source_turns`：长期记忆累计覆盖的轮数。

执行耗时记录为 `memory_compaction` 和 `memory_item_persistence` Stage，并暴露 `commerce_memory_compactions_total` 和 `commerce_memory_turns_compacted_total` 指标。

## 配置

```dotenv
CONTEXT_COMPACTION_ENABLED=true
CONTEXT_RECENT_TURNS=4
```

`CONTEXT_RECENT_TURNS` 被限制在 1～20。关闭压缩时仍只读取配置的近期窗口，不读取摘要；原子记忆继续按用户与会话作用域工作。部署新版本前需要执行 Alembic migration 创建 `conversation_memories` 与 `memory_items` 表。

迁移后可以使用真实数据库运行并自动清理冒烟场景：

```powershell
$env:PYTHONPATH="$PWD\src"
.\.venv\Scripts\python.exe scripts\verify_context_memory.py
```
