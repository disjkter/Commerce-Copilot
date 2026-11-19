# API 限流与防刷

Commerce Copilot 在 API 边界执行滑动窗口限流，避免高频请求持续占用密码哈希、LLM、Agent、数据库和物流工具资源。业务处理器只依赖 `RateLimiter` 协议，当前使用单进程内存实现，后续可以替换 Redis 原子计数或 Lua 实现。

## 保护范围

| Scope | 身份维度 | 默认限制 | 保护对象 |
| --- | --- | --- | --- |
| `login` | 客户端 IP | 20 次 / 60 秒 | 登录和密码校验 |
| `register` | 客户端 IP | 10 次 / 300 秒 | 批量注册 |
| `turn` | 已认证用户 | 60 次 / 60 秒 | LLM、Agent 与业务工具 |
| `logistics_webhook` | 物流商 + IP | 300 次 / 60 秒 | 物流事件写入 |

身份值经过 SHA-256 截断后才进入限流器，不会进入 Metrics 或日志。超限时返回 HTTP `429`，并提供 `Retry-After`、`X-RateLimit-Limit` 和 `X-RateLimit-Remaining` 响应头。

## 滑动窗口

每个 Key 保存当前窗口内的请求时间戳。新请求到达时：

1. 删除早于 `now - window_seconds` 的时间戳。
2. 剩余数量达到上限时拒绝请求，并根据最早时间戳计算恢复时间。
3. 未达到上限时追加当前时间戳。

与固定窗口相比，滑动窗口不会在两个窗口边界处瞬间放行两倍流量。内存实现使用锁保证线程安全，定期清理过期窗口，并通过 `max_keys` 限制身份数量，防止攻击者制造无限 Key。

## 配置与代理安全

限流参数位于 `.env`：

```dotenv
RATE_LIMIT_ENABLED=true
TRUST_PROXY_HEADERS=false
LOGIN_RATE_LIMIT=20
LOGIN_RATE_WINDOW_SECONDS=60
REGISTER_RATE_LIMIT=10
REGISTER_RATE_WINDOW_SECONDS=300
TURN_RATE_LIMIT=60
TURN_RATE_WINDOW_SECONDS=60
WEBHOOK_RATE_LIMIT=300
WEBHOOK_RATE_WINDOW_SECONDS=60
```

默认不信任 `X-Forwarded-For`，防止客户端伪造 IP 绕过限制。只有应用位于可信反向代理后方，并且代理会覆盖该请求头时，才能设置 `TRUST_PROXY_HEADERS=true`。

## 可观测性与生产边界

`commerce_rate_limit_checks_total{scope,outcome}` 统计各接口允许和拒绝次数，但不记录身份。当前内存后端只适合单 Worker；多 Worker 或多实例部署时，各进程无法共享窗口，需要实现同一 `RateLimiter` 协议的 Redis 后端。
