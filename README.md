# 我的分红工具

GitHub Pages 是本项目的静态网页托管层，业务登录和私有数据不依赖 GitHub 日常登录。

> 当前工作树正在进行 Supabase 私有控制面切换，尚未 commit、push 或发布本轮前端改动。

## 页面结构

- **Part 0 今日看板**：VPS 白名单、激活版本、DRY_RUN 运行投影和私有模拟盘持仓摘要。
- **Part 1 自选股持仓**：认证后通过 Supabase 私有 RPC 读取持仓股数、成本、现价、市值和浮动盈亏；网页只读。
- **Part 2 周 BOLL 股息网格**：保留现有研究展示和本地网格显示设置；配置写入私有 RPC 前不开放云端写入。
- **Part 4 分红日历**、**Part 5 公告与 AI 分红预估**、**Part 6 操作与策略学习**：导航保留；个人操作记录和策略反馈迁移到私有数据层前，不读取或写入公开 GitHub JSON。

## 认证与数据边界

正式登录路径为：

```text
自定义用户名 + 密码
  → Supabase username-login Edge Function
  → Supabase Auth session
  → auth.uid() + RLS / 窄 RPC
  → 私有持仓和 VPS 控制面
```

- 用户名为服务端映射，不在浏览器直接查询用户名表。
- Supabase 客户端使用 `persistSession: true`、`autoRefreshToken: true`、`detectSessionInUrl: false`，不设置应用层固定 7 天或其他强制重新登录期限。
- GitHub 只负责静态网页和代码协作，不保存密码、service-role/secret key、VPS 凭据、账户标识、订单数据或私有持仓。
- 浏览器只允许使用 Supabase Publishable/anon key，并且所有私有读取依赖认证会话和 RLS/RPC。
- 成本、市值和浮动盈亏由 Supabase 权威数据库计算；网页不使用本地持仓 fallback，也不把私有投影写入 `localStorage`。
- 白名单变更是完整 immutable revision，提交后必须经过 VPS 拉取、durable staging、SQLite activation、active-cache 和 ACK；提交成功不等于已激活。
- 移出白名单不等于卖出、清仓或删除模拟盘持仓。

## 浏览器配置

`assets/runtime-config.js` 只允许放：

```js
window.STOCK_DASHBOARD_SUPABASE_CONFIG = {
  url: "https://<project-ref>.supabase.co",
  anonKey: "<publishable-or-anon-key>"
};
```

项目 URL 可以公开；Publishable/anon key 只能与正确 RLS 一起使用。禁止放入 service-role/secret key、数据库密码、HMAC、Provider key、SSH 或交易凭据。当前本地运行配置已填入目标项目的公开 key；禁止替换为 service-role/secret key。

## 本地预览与验证

普通入口：

```text
http://127.0.0.1:8765/index.html
```

严格 Part 0 本地 UI 预览：

```text
http://127.0.0.1:8765/index.html?part0-local-preview=1
```

本地预览只渲染明确标记的 22 条 synthetic `DEMO-*` 布局样例；启动会在初始化阶段直接返回，不连接 Supabase、VPS、行情、模拟盘、订单接口，也不读写浏览器本地存储。

启动本地静态服务器：

```bash
python3 -m http.server 8765 --bind 127.0.0.1
```

本轮已验证：

- `node --check assets/app.js`
- `node --check assets/supabase-js.v2.112.4.js`
- `git diff --check`（Dashboard 工作树）
- 严格预览入口零网络/零 `localStorage`
- 普通入口公开安全空状态
- mock 用户名密码登录 → 私有 RPC → 持仓渲染 → 白名单 revision 提交 → 退出后私有 DOM 清除
- Supabase/Auth/Stage 3/私有投影桥接静态合同检查

## Hosted 状态

- Stage 1 VPS 控制面、Stage 2 gateway/protocol v2/ACL closure 已执行并完成验证。
- Stage 3 私有用户名、scope、持仓投影和 Auth 限流 migration 已由项目所有者在 Supabase SQL Editor 执行；对应 postflight 分别为 `24/24`、`10/10` 通过。
- `20260829005000_vps_sync_private_projection_bridge.sql` 已由项目所有者手动执行，对应 Hosted postflight 为 `9/9` 通过。
- `username-login`、`username-recovery-request` 和 `vps-sync` Edge Functions 已部署并处于 `ACTIVE`；allowed origin、恢复跳转地址和服务端限流 secret 已配置。
- Auth user、用户名映射、`primary` scope membership、VPS admin 权限已由项目所有者分别显式配置；不会自动创建或授予。
- VPS 私有投影 release 仍需按受控文件范围打包、部署并完成真实 DRY_RUN 只读周期。

## GitHub Pages 发布

GitHub Pages 可以免费托管此类静态 HTML/CSS/JS 页面，但它不能保护浏览器里的秘密，也不能替代 Supabase 认证和 RLS。发布前必须完成：

1. 填入正确的公开 Publishable/anon key，并复核 CSP 只允许实际 Supabase origin。
2. 执行 bridge migration 和 Hosted postflight。
3. 部署并配置两个 username-auth Edge Function，设置精确 Pages origin、恢复跳转地址和服务端限流 secret。
4. 创建并确认 Auth user，再分别配置 username mapping、private scope membership 和可选 VPS admin。
5. 部署 VPS 新 release，保持 `DRY_RUN`，完成 accepted ACK/private projection 证据链。
6. 重新检查 `origin/main` 分歧后，才允许在明确授权下 commit/push，并用 cache-busted URL 验证 Pages 实际返回的 HTML/JS/CSS。

本项目仅用于数据展示与个人研究，不构成投资建议。任何策略状态都必须区分事实、推断和待回测验证，系统不会自动下单。
