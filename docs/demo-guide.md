# 面试演示手册

## 1. 演示前准备

PowerShell 中依次执行：

```powershell
Set-Location "E:\ProgramFiles\PythonProject\commerce-copilot"
.\scripts\start_postgres.ps1
$env:PYTHONPATH="$PWD\src"; & "E:\ProgramFiles\PythonProject\ecommerce-agent-main\.venv\Scripts\python.exe" -m commerce_copilot.main
```

打开 `http://127.0.0.1:8010/`，使用演示账号 `demo / demo12345`，或现场创建账号。

## 2. 五分钟演示路线

### 场景一：多 Agent 并发

输入：

```text
推荐一款500元以内的通勤耳机，同时告诉我发货规则
```

观察点：右侧 Trace 同时出现 Product Scout 和 Order Care，说明 Planner 拆分了两个 Mission，并通过 LangGraph fan-out 并发执行。

### 场景二：Grounding

展开 Product Scout 报告，展示商品 Evidence 中的编号、价格、评分和库存。说明回答中的事实不是模型记忆，而是 Claim 与 Evidence 对齐后的结果。

### 场景三：多轮指代与确定性对比

继续输入：

```text
第一款和第二款对比一下
```

观察点：系统根据上一轮 Evidence 顺序解析真实商品编号，并由代码计算最低价格、最高评分和价差。

### 场景四：用户偏好

在右上角“偏好设置”中填写：

- 预算：500
- 品类：耳机
- 场景：通勤
- 关注点：降噪

然后输入：

```text
推荐商品
```

观察点：右侧“本轮个性化”展示实际应用的偏好；本轮明确条件仍可覆盖这些默认值。

### 场景五：约束冲突和候补推荐

把默认预算改为 300 元，然后输入：

```text
我要办公用的耳机
```

预期：系统不返回鼠标，也不谎称商品满足预算；它说明 300 元以内暂无耳机，并提供 329 元的最近预算候补及 29 元差额。

继续输入：

```text
可以，提高预算
```

预期：Planner 使用上一轮保存的商品编号继续介绍，不进行不稳定的重新检索。

## 3. 演示时的讲解顺序

推荐使用“问题—设计—证据”的结构：

1. 问题：单次模型调用难以处理混合意图，也容易产生事实幻觉。
2. 设计：Supervisor 拆任务，领域 Agent 并发调用确定性工具，Grounding 校验后再组织回答。
3. 证据：展示 Trace、Evidence、自动化测试以及模型失败时的降级字段。

## 4. 常见异常处理

| 现象 | 检查方式 |
|---|---|
| 页面无法访问 | 检查 8010 端口及服务日志 |
| 数据库连接失败 | 执行 `start_postgres.ps1` 并检查 `.env` |
| 页面仍是旧版本 | 打开带版本参数的地址并强制刷新 |
| DeepSeek 调用失败 | 查看 `trace.planning_source` 与错误字段；本地降级仍应工作 |
| 修改表结构后启动失败 | 执行 `scripts\migrate_database.ps1` |

## 5. 演示禁区

- 不把 5 条演示商品描述成真实生产数据。
- 不宣称特征哈希向量等同于 BGE 等预训练 Embedding。
- 不宣称系统已经经过高并发压测。
- 不隐藏模型降级，反而应把降级能力作为工程亮点展示。
