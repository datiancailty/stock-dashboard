# Supabase：VPS 私有控制面

本目录是“我的分红工具”新增的 **Part 0 / VPS 私有控制面** 的数据库迁移记录。

## 阶段 1 的边界

`migrations/20260825000000_vps_control_plane_stage1.sql` 建立：

- 版本化 VPS 白名单及条目；
- 白名单已提交 / 等待同步 / 准备中 / 已激活 / 被拒绝等审计状态；
- VPS 当前运行快照、逐股策略状态、当前模拟持仓摘要、脱敏事件、同步回执；
- 管理员白名单表与严格 RLS；
- 仅允许已授权管理员调用的 `vps_submit_whitelist_revision(...)`；
- 50 只股票上限、`Asia/Shanghai`、`DRY_RUN` 固定边界；
- 日志文本的基础敏感词拦截。

阶段 1 **不包含**：

- VPS 的公网入口、SSH 信息、模拟盘账户标识、成本价、市值、盈亏、订单明细；
- Supabase service-role key、GitHub OAuth secret、数据库密码；
- 真实下单、撤单、买卖接口；
- Dashboard 前端接入、VPS 同步服务、15 分钟调度变更。

## 权限原则

浏览器只使用 Supabase 的公开 Publishable/anon key，并且只有在自定义用户名/密码登录后、命中相应 RLS 或 `vps_admins` 授权时才能读取私有数据。浏览器没有直接写表权限；白名单变更只能经过窄范围 RPC 创建一个不可变“目标股票池版本”。GitHub 只负责静态网页托管和代码协作，不是日常业务登录入口。

VPS 将在第 2 步通过独立的服务器端/Edge Function 访问路径读写这些表。该路径的 service-role key 必须只保存在 Supabase/VPS 私密环境变量中，绝不能进入 GitHub Pages、Git 历史或聊天内容。

## 管理员引导

迁移故意不会预置管理员。创建并确认 Supabase Auth 用户后，在 Supabase SQL Editor 中显式写入其 `user_id` 到 `vps_admins`；私有持仓查看权限还要单独写入 `vps_private_scope_members`。登录成功、用户名为 `admin` 或首个用户都不会自动取得任何业务权限。

## 首阶段验收

1. 所有 `vps_*` 表均已创建并开启 RLS。
2. 未登录/非管理员读取不到任何 VPS 控制面数据。
3. 浏览器侧没有 `service_role`、数据库连接串、VPS/SSH 凭据。
4. `vps_submit_whitelist_revision(...)` 可拒绝空列表、重复代码、非 `000000.SH/SZ` 格式和超过 50 只的请求。
5. 现有 Dayflow 表及数据未被修改。

## 已确认的正式运行权威（去 TickFlow 化）

```text
GitHub Pages Part 0
    → Supabase 自定义用户名/密码 Auth + RLS：目标 VPS 白名单版本
    → VPS 出站 HTTPS pull：只取目标 symbols 与版本状态
VPS 独立市场缓存
    ← 不可变 Tushare 原始归档的版本化派生快照
    ← 盘外 HiThink 未复权日线增量的版本化派生快照
VPS 15 分钟 DRY_RUN
    ← HiThink：当前全白名单批量行情
    ← 妙想：模拟盘账户/持仓只读核对
    → Supabase 出站 HTTPS publish：严格脱敏状态
GitHub Pages Part 0
    ← Supabase：请求 / 已接收 / 数据准备 / 已激活 / 运行状态
```

TickFlow 不在上述正式链路中：它不是白名单权威、VPS 市场包生产器、VPS 状态接收端或任何定时任务依赖。原有 TickFlow 项目最多保留作本地研究/历史工具；它的自选股不能自动合并到 VPS 白名单。

`Tushare` 在这里是既有的**不可变历史归档**，不是网页或 15 分钟 Worker 的运行时 API 依赖。后续 HiThink 数据必须另建派生快照，永远不得覆盖、改写或删除原始归档。

## 阶段 2 的当前边界

本仓库保存的 Stage 2 工件现在分三层：

1. 初始 gateway、protocol v2 和 ACL closure 已在 Hosted Supabase 执行并通过 postflight；
2. `functions/vps-sync/` 已部署，VPS 出站 HMAC pull/publish 已做过真实路径验证；
3. VPS Stage 2 release 已安装，正式运行仍固定为 `DRY_RUN`，不包含真实交易写入。

v2 收口新增：完整不可变白名单合同（目标 VPS、适配器、`DRY_RUN`、过期时间、成员/策略/市场快照/hash 证据）、revision/item 冻结触发器、1–50 股票约束、稳定 `request_id` 的服务端请求回执账本、ACK-ID 同内容重试/冲突拒绝、精确路径（拒绝 query）/协议/HMAC 绑定，以及 Edge service-role 只执行窄 RPC 的设计。

`python3 supabase/verify-stage2-v2-static.py` 是一个**不联网、不连接数据库**的迁移源码回归检查；它不能代替后续在托管 Supabase 上执行 migration、验证 SECURITY DEFINER/RLS/ACL 与 Edge 行为。

本地静态测试不能替代 Hosted RLS/ACL、Edge 请求和远端运行证据。通用 market/backup timer 仍保持 disabled；任何日期限定的 `DRY_RUN` 运行都必须单独核对，不得称为已经执行。

## 阶段 3：自定义登录与私有持仓

以下 Stage 3 工件已由项目所有者在 Hosted Supabase SQL Editor 执行，并通过只读 postflight：

- `migrations/20260829003000_dashboard_private_data_schema_stage3.sql`：用户名映射、私有 scope、持仓成本/市值/P&L 投影和并发安全白名单 RPC；
- `migrations/20260829004000_dashboard_auth_rate_limit.sql`：只保存不可逆 hash 的服务端失败限流；
- `functions/username-login/` 与 `functions/username-recovery-request/`：自定义用户名适配层候选实现；
- `contracts/verify-stage3-hosted-postflight.sql` 与 `contracts/verify-auth-hosted-postflight.sql`：只读验收查询。

Hosted 证据：Stage 3 postflight `24/24` 通过，Auth postflight `10/10` 通过。以上工件不会创建 Auth 用户、不会自动授予权限、不会导入持仓，也不会启用 `ARMED`。

## 阶段 3.1：私有投影桥接（待手动执行）

`migrations/20260829005000_vps_sync_private_projection_bridge.sql` 是前向迁移候选：保留已部署的 v2 ingest 为内部 base，并让新 wrapper 在同一 PostgreSQL transaction 中先处理 v2 脱敏报告，再写入 Stage 3 私有投影。私有 writer 失败时，ACK/runtime 和私有投影整体回滚。

这份迁移尚未在 Hosted 执行。执行后必须运行 `contracts/verify-private-projection-bridge-hosted-postflight.sql`，确认 wrapper/base 存在、ACL 收敛、浏览器不能直接表 DML，且所有失败检查为零。