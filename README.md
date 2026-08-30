# 我的分红工具

GitHub Pages 是本项目的静态网页托管层，业务登录和私有数据不依赖 GitHub 日常登录。

> Part 1—6 owner-scoped 私有迁移 Stage 1 和 Stage 2 已完成；基础 Stage 2 快照的 Hosted 聚合 postflight `18/18` 通过。当前正在执行一次前向修复：补入 Part 4 的当前年度已实施分红事件，并修复 Part 6 三个反馈按钮的私有 RPC。修复后的 Part 4 目标为 20 只行情、90 条日历、452 条公告、384 条操作和 47 条建议；Hosted SQL 仍须由项目所有者手动执行并单独 postflight 验证。

> 本次已核实东阿阿胶（000423）2026 中期权益分派：股权登记日 `2026-08-28`，除权除息日/派息日 `2026-08-31`，每股税前现金分红 `1.344811` 元。该当前年度事件只补入 Part 4 日历，不叠加到上一完整年度正式股息率分子。

## 页面结构

- **Part 0 今日看板**：VPS 白名单、激活版本、DRY_RUN 运行投影和私有模拟盘持仓摘要。
- **Part 1 自选股持仓**：认证后读取 20 只原自选股、私有行情与正式分红；模拟盘投影如有则叠加展示，添加/删除只修改个人私有自选清单。
- **Part 2 周 BOLL 股息网格**：认证后读取完整私有行情快照和板块配置，显示 20 只自选范围中的周 BOLL 状态。
- **Part 3 股息率价格网格**：恢复 5.0% / 5.5% / 6.0% / 6.5% / 7.0% 目标价表。
- **Part 4 分红日历**、**Part 5 公告与 AI 分红预估**、**Part 6 操作与策略学习**：认证后分别读取完整私有历史；Part 6 提供私有 CSV 导出、CSV 导入、新增和删除。

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
- 成本、市值和浮动盈亏由 Supabase 权威数据库计算；网页不使用本地持仓或 Part 1—6 历史数据 fallback，也不把私有投影/个人记录写入浏览器本地存储。
- Part 1—6 历史内容位于独立 `personal_*` owner-scoped 命名空间，不混入 `vps_*` 控制面；浏览器只能调用相应窄 RPC，不能直接读取表。
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
- `20260830000000_personal_dashboard_legacy_stage1.sql` 和受控的一次性私有导入已由项目所有者手动执行；Stage 1 聚合 postflight 为 `22/22` 通过。
- `20260830010000_personal_live_archive_stage2.sql`、10 个私有覆盖导入分片和 `personal_live_archive_stage2_postflight.sql` 已由项目所有者手动执行；Stage 2 聚合 postflight 为 `18/18` 通过。
- `username-login`、`username-recovery-request` 和 `vps-sync` Edge Functions 已部署并处于 `ACTIVE`；allowed origin、恢复跳转地址和服务端限流 secret 已配置。
- Auth user、用户名映射、`primary` scope membership、VPS admin 权限已由项目所有者分别显式配置；不会自动创建或授予。
- VPS 保持 `DRY_RUN`；本轮 Pages 私有历史读取不会触发 VPS、行情 Provider、账户、订单或策略执行路径。

## Part 6 ChatGPT Plus/Codex 策略更新

策略画像与操作复盘已切换为**本机私有 Worker**路线，不再依赖 GitHub Actions 的 OpenAI Platform API Key：

```text
macOS 工作日调度
  → scripts/plus_strategy_worker.py
  → 本机已登录的 Codex CLI（ChatGPT Plus/Codex）
  → 严格 JSON Schema 校验
  → personal_publish_strategy_worker_result
  → Supabase personal_* 私有空间
  → Part 6 登录后读取最新结果
```

- Worker 只使用本机 Codex CLI 的 ChatGPT 订阅登录，不读取或接收 `OPENAI_API_KEY`。
- Dashboard 用户密码只在首次 `setup` 时输入，不写入配置；Supabase refresh token 只存放在 macOS Keychain。
- 本机 Worker 通过已有认证用户的窄 RPC 读取 Part 4/Part 6 私有数据，不能直接对 `personal_*` 表做 DML。
- Codex 输出必须通过固定 JSON Schema、股票白名单、动作枚举、长度和置信度校验；失败时保留上一次成功的策略分析。
- Worker 结果包含运行日期、输入/输出哈希、模型、认证方式和脱敏状态，不包含密码、OAuth 文件或 Platform Key。
- 旧版 `check-strategy-api.yml` 和 `update-strategy.yml` 已停用自动调度，避免继续显示“未配置 OPENAI_API_KEY”。首次 Worker 成功运行前，页面会把旧状态显示为“待切换”，而不是伪装成 AI 已成功。

Hosted migration 执行并完成本机 setup 后，手动测试命令为：

```bash
python3 scripts/plus_strategy_worker.py run --force
```

工作日调度由单独的 `install-schedule` 步骤安装；在 Hosted postflight、本机 Codex 登录和一次真实私有 Worker 成功运行前，不会自动加载调度。


GitHub Pages 可以免费托管此类静态 HTML/CSS/JS 页面，但它不能保护浏览器里的秘密，也不能替代 Supabase 认证和 RLS。发布前必须完成：

1. 填入正确的公开 Publishable/anon key，并复核 CSP 只允许实际 Supabase origin。
2. 执行 bridge migration、owner-scoped personal migration、受控私有导入和 Hosted postflight。
3. 部署并配置两个 username-auth Edge Function，设置精确 Pages origin、恢复跳转地址和服务端限流 secret。
4. 创建并确认 Auth user，再分别配置 username mapping、private scope membership 和可选 VPS admin。
5. 部署 VPS 新 release，保持 `DRY_RUN`，完成 accepted ACK/private projection 证据链。
6. 重新检查 `origin/main` 分歧后，才允许在明确授权下 commit/push，并用 cache-busted URL 验证 Pages 实际返回的 HTML/JS/CSS。
7. 每个阶段性大版本在验证后创建 GitHub Release；旧管理员源文件停用前必须保留包含它们的回退 release/tag。详见 [版本发布与回滚策略](docs/release-rollback-policy.md)。

本项目仅用于数据展示与个人研究，不构成投资建议。任何策略状态都必须区分事实、推断和待回测验证，系统不会自动下单。
