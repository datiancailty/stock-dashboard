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

浏览器只使用 Supabase 的公开 Publishable/anon key，并且只可在 GitHub OAuth 登录且命中 `vps_admins` 后读取私有控制面数据。浏览器没有直接写表权限；白名单变更只能经过窄范围 RPC 创建一个不可变“目标股票池版本”。

VPS 将在第 2 步通过独立的服务器端/Edge Function 访问路径读写这些表。该路径的 service-role key 必须只保存在 Supabase/VPS 私密环境变量中，绝不能进入 GitHub Pages、Git 历史或聊天内容。

## 管理员引导

迁移故意不会预置管理员。完成 GitHub OAuth 首次登录后，在 Supabase SQL Editor 中确认该 Auth 用户，再显式写入其 `user_id` 到 `vps_admins`。这样不会出现“首个登录者自动成为管理员”的漏洞。

## 首阶段验收

1. 所有 `vps_*` 表均已创建并开启 RLS。
2. 未登录/非管理员读取不到任何 VPS 控制面数据。
3. 浏览器侧没有 `service_role`、数据库连接串、VPS/SSH 凭据。
4. `vps_submit_whitelist_revision(...)` 可拒绝空列表、重复代码、非 `000000.SH/SZ` 格式和超过 50 只的请求。
5. 现有 Dayflow 表及数据未被修改。
