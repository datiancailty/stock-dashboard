# Part 4 / Part 6 前向修复手工执行说明

本次修复包含两个独立问题，均不删除历史、不修改 `vps_*`、不启用交易：

## 已确认的根因

### Part 4

旧 `scripts/update_market.py` 只接受上一完整年度的固定标签（例如 `2025年度分配`、`2025中期分配`），没有把妙想返回的 `2026中报`实施事件写入日历；同时旧解析器只读取中文列名，遇到妙想的编码列名时也可能漏读。现已改为：

- 同时识别 `年度分配 / 年报 / 中报 / 中期分配` 等标签；
- 保留上一完整年度正式股息分子不变；
- 把当前年度已实施的登记日、除权除息日、派息日加入 Part 4；
- 支持妙想 `nameMap` 编码列映射。

本次已从东方财富妙想核实东阿阿胶（000423）：

- 股权登记日：2026-08-28；
- 除权除息日：2026-08-31；
- 派息日：2026-08-31；
- 2026 中期现金分红：每股税前 1.344811 元（每 10 股 13.448110 元）。

### Part 6

三个按钮共用一个写入 RPC。旧函数在手工 SQL 执行后可能尚未进入 PostgREST schema cache，且函数内的 `pgcrypto digest` 路径没有做运行期复核，因此浏览器只看到统一的 `rpc_failed`。现改为版本化 RPC、兼容旧函数、锁定实际 digest schema，并主动通知 PostgREST reload schema。

## Hosted SQL Editor 执行顺序

1. 下载/打开并完整执行：

   `supabase/migrations/20260830100000_personal_feedback_rpc_repair.sql`

2. 执行验证：

   `supabase/verification/personal_feedback_rpc_repair_postflight.sql`

   预期返回 `6 / 6 / 0 / []`。

3. 执行本机受控私有补丁文件：

   `~/.hermes/workspace/stock-dashboard-private-migration/personal-market-event-patch-000423-20260830.sql`

   该文件含完整私有市场文档，只能在本机受控目录中打开并粘贴；不要提交 Git、上传 Release 或发到聊天。

4. 执行验证：

   `supabase/verification/personal_market_event_patch_postflight.sql`

   预期返回 `5 / 5 / 0 / []`。

5. 打开最新 GitHub Pages 页面，强制刷新并重新登录一次，然后检查：

   - Part 4：2026-08-31 可看到东阿阿胶“派息日”和“除权除息日”，2026-08-28 可看到“股权登记日”；
   - Part 6：三个反馈按钮均可成功保存；点击期间其他反馈按钮会暂时禁用，避免重复提交；
   - 错误时页面显示安全的中文原因，不再只显示 `rpc_failed`。

## 证据边界

- Part 4 的 90 条是本次私有快照补丁后的事件数；正式股息率网格仍使用上一完整年度已实施年报 + 中报，不把 2026 中期分红叠加到该分子。
- “派息日”是上市公司登记的实施日期；券商账户实际入账显示可能受券商清算/展示时间影响，不能仅凭页面日期断言到账时刻。
- SQL Editor 执行成功、postflight 通过、浏览器按钮成功是三个独立证据；不能用静态代码检查替代最后两项。
