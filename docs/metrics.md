# 聚合指标监控

单次请求问题通过 `trace_id` 和结构化日志定位，跨请求趋势通过 `GET /metrics` 观察。两者职责不同：Trace 保留一次执行链路，Metrics 只聚合有限维度，不保存用户输入和身份信息。

## 指标范围

| 指标 | 类型 | 用途 |
| --- | --- | --- |
| `commerce_turns_total` | Counter | 请求成功率和失败率 |
| `commerce_turn_duration_seconds` | Histogram | 端到端 P50/P95/P99 |
| `commerce_agent_executions_total` | Counter | 各 Agent 调用量和状态 |
| `commerce_agent_duration_seconds` | Histogram | 各 Agent 延迟分布 |
| `commerce_tool_calls_total` | Counter | Tool 成功率和失败率 |
| `commerce_tool_retries_total` | Counter | Tool 重试次数 |
| `commerce_llm_calls_total` | Counter | Planner/Composer 模型调用量 |
| `commerce_llm_duration_seconds` | Histogram | 模型调用延迟 |
| `commerce_llm_tokens_total` | Counter | 输入、输出 Token 用量 |
| `commerce_llm_fallbacks_total` | Counter | LLM 失败降级次数 |
| `commerce_grounding_validations_total` | Counter | Grounding 通过和拦截次数 |
| `commerce_grounding_claims_checked_total` | Counter | 已校验 Claim 数量 |
| `commerce_business_outcomes_total` | Counter | 无结果、人工审核等业务结果 |
| `commerce_rate_limit_checks_total` | Counter | 各 API Scope 的允许和拒绝次数 |
| `commerce_memory_compactions_total` | Counter | 会话记忆压缩更新和空操作次数 |
| `commerce_memory_turns_compacted_total` | Counter | 已合并进结构化记忆的原始轮次数 |

## 常用 PromQL

端到端 P95：

```promql
histogram_quantile(0.95, sum by (le) (rate(commerce_turn_duration_seconds_bucket[5m])))
```

Tool 失败率：

```promql
sum(rate(commerce_tool_calls_total{outcome="failure"}[5m]))
/
sum(rate(commerce_tool_calls_total[5m]))
```

Grounding 拦截率：

```promql
sum(rate(commerce_grounding_validations_total{outcome="blocked"}[5m]))
/
sum(rate(commerce_grounding_validations_total[5m]))
```

LLM 降级率：

```promql
sum(rate(commerce_llm_fallbacks_total[5m]))
/
(
  sum(rate(commerce_llm_fallbacks_total[5m]))
  + sum(rate(commerce_llm_calls_total[5m]))
)
```

## 设计约束

- 标签中禁止使用 `trace_id`、用户编号、会话编号和商品编号，避免高基数。
- 指标异常采用 fail-open，不得中断正常业务回答。
- 当前实现按进程保存在内存中，服务重启后清零。
- 多实例部署由 Prometheus 抓取各实例并聚合，不在应用中跨进程共享计数。

## Windows 本地监控

本项目不要求 Docker。先保持 Commerce Copilot 运行在 `127.0.0.1:8010`，再安装官方 Windows amd64 版 Prometheus：

```powershell
Set-Location "E:\ProgramFiles\PythonProject\commerce-copilot"
.\scripts\install_prometheus.ps1
```

安装脚本从 Prometheus 官方 GitHub Release 下载固定版本、校验 SHA-256，并安装到 `E:\ProgramFiles\Prometheus`。安装完成后执行：

```powershell
Set-Location "E:\ProgramFiles\PythonProject\commerce-copilot"
.\scripts\start_prometheus.ps1
```

如果 Prometheus 位于其他目录：

```powershell
.\scripts\start_prometheus.ps1 -PrometheusExe "你的目录\prometheus.exe"
```

浏览器访问 `http://127.0.0.1:9090/targets`，`commerce-copilot` 为 `UP` 即表示采集成功。抓取配置位于 `ops/prometheus/prometheus.yml`。

Grafana 中添加 Prometheus 数据源 `http://127.0.0.1:9090`，UID 使用 `commerce-prometheus`，然后导入 `ops/grafana/commerce-copilot-dashboard.json`。项目也提供了可复制到 Grafana provisioning 目录的数据源文件 `ops/grafana/datasource.yml`。

预制 Dashboard 包含请求吞吐量、成功率、端到端 P95、LLM 降级率、Grounding 拦截率、Tool 失败率、各 Agent P95、业务结果和 Token 速率，并以 `Asia/Shanghai` 显示时间。
