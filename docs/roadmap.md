# Commerce Copilot 路线图

路线图用于说明项目从“可运行的多 Agent 原型”走向“可观测、可评测、可部署的 AI 应用系统”的演进过程。未完成事项不会写成既有成果。

## 当前基线：已完成

- LangGraph Supervisor、并发 fan-out 与结果汇合
- Product Scout、Order Care、Conversation Guide 三个领域 Agent
- DeepSeek 结构化规划、证据化回答与本地确定性降级
- PostgreSQL、SQLAlchemy Repository 与 Alembic 迁移
- FTS/BM25、离线向量召回、RRF 融合排序
- 品类、预算、库存硬约束与个性化软偏好
- 无结果时的可解释候补推荐和渐进式预算放宽
- Claim–Evidence Grounding 与失败关闭
- 用户鉴权、订单归属、会话隔离和偏好持久化
- 多轮商品指代、确定性商品对比和执行 Trace
- Agent 执行层与完整编排链路的场景化阶梯压测
- 自动化测试及离线检索评测

## Phase 1：实时交互与可观测性

目标：让多 Agent 的执行过程在网页中实时可见，并能够定位慢请求和失败节点。

- [x] 使用 SSE 输出 `planning_started`、`agent_completed`、`grounding_completed`、`answer_completed`
- [x] 为每次请求生成 `trace_id`，贯穿 API、编排、Agent、Repository 和最终响应
- [x] 输出结构化 JSON 日志，记录阶段耗时、Agent 状态、数据访问和降级原因
- [ ] 增加基础指标：P50/P95 延迟、LLM 降级率、Grounding 失败率、无结果率

验收标准：网页可以逐步展示执行进度；一次异常能够通过 `trace_id` 还原完整链路。

## Phase 2：真实语义检索

目标：从教学型特征哈希向量升级到可量化的中文语义检索。

- [x] PostgreSQL 使用持久化 `tsvector`、GIN 索引与 `ts_rank_cd` 完成关键词召回
- [ ] 接入 BGE 中文 Embedding
- [x] PostgreSQL 安装并启用 pgvector
- [x] 增加 1024 维商品向量列、HNSW 索引及按模型版本和内容哈希的增量更新
- [ ] 商品向量离线构建与增量更新
- [ ] 对比 FTS、Vector、Hybrid 三组检索结果
- [ ] 扩充人工标注查询集，报告 Recall@K、MRR、NDCG@K

验收标准：在固定评测集上，Hybrid 相比关键词基线获得可复现提升，并记录数据、参数和实验结果。

## Phase 3：缓存、限流与异步任务

目标：补齐传统后端工程能力，提高重复查询和高并发情况下的稳定性。

- [ ] Redis 缓存热门商品、政策和短期会话数据
- [ ] 设计缓存 Key、TTL、失效和防穿透策略
- [ ] 基于用户和 IP 的接口限流
- [ ] 将 Embedding 构建、评测等耗时任务放入后台任务队列
- [ ] 验证缓存命中率及数据库压力变化

验收标准：提供压测前后对比，说明吞吐、延迟、缓存一致性和失败处理策略。

## Phase 4：部署与质量闭环

目标：形成可公开演示、可回滚、可持续验证的完整工程交付。

- [ ] Docker Compose 编排 API、PostgreSQL、Redis
- [ ] GitHub Actions 执行测试、迁移检查和静态检查
- [ ] 云端部署、健康检查和环境变量管理
- [ ] k6 或 Locust 压力测试报告
- [ ] bad case 数据集与回归评测
- [ ] 安全检查：密钥泄漏、弱口令、越权和提示注入

验收标准：新环境可以按文档一键启动；提交代码后自动验证；关键版本具备部署记录和测试报告。

## 简历更新原则

只有达到对应验收标准后，才能把规划项改写为简历成果。例如，在没有真实评测结果前，不写“显著提高召回率”；在没有压力测试前，不写“支持高并发”。
