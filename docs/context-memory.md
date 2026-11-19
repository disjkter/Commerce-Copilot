# 多轮上下文压缩与分层记忆

早期版本每次只读取最近 6 轮，窗口之外的内容直接不可见。这种做法能限制 Prompt 长度，但属于截断，不是压缩，也无法支持跨越多轮无关对话的业务指代。

当前上下文由三层组成：

1. **原始会话层**：`conversation_turns` 和 `conversation_turn_evidence` 保留完整用户消息、回答和 Evidence，支持审计与问题回放。
2. **近期工作记忆**：Planner 默认读取最近 4 轮原始对话，保留当前语义、语气和短期指代。
3. **结构化长期记忆**：窗口外历史增量合并到 `conversation_memories`，保存业务主题、订单号、运单号、售后工单、最近预算和最近一组商品 Evidence。

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

## 指代恢复

Planner 不再机械使用“上一条消息”。当当前输入包含“它”“第二款”“继续”“到哪了”等追问表达时，会从最近历史向前寻找带 Evidence、业务实体或结构化摘要的相关 Turn。因此中间即使存在多轮“你好”“谢谢”，仍可恢复之前的商品或订单。

## 可观测性

每次响应的 `trace.memory` 包含：

- `summary_loaded`：本轮是否读取了长期记忆。
- `recent_turns`：本轮加载的近期原始轮数。
- `compacted_turns`：本轮新压缩的轮数。
- `source_turns`：长期记忆累计覆盖的轮数。

执行耗时记录为 `memory_compaction` Stage，并暴露 `commerce_memory_compactions_total` 和 `commerce_memory_turns_compacted_total` 指标。

## 配置

```dotenv
CONTEXT_COMPACTION_ENABLED=true
CONTEXT_RECENT_TURNS=4
```

`CONTEXT_RECENT_TURNS` 被限制在 1～20。关闭压缩时仍只读取配置的近期窗口，不读取结构化记忆。部署新版本前需要执行 Alembic migration 创建 `conversation_memories` 表。

迁移后可以使用真实数据库运行并自动清理冒烟场景：

```powershell
$env:PYTHONPATH="$PWD\src"
.\.venv\Scripts\python.exe scripts\verify_context_memory.py
```
