# Part 0 · 周一 Stage 2 DRY_RUN 审计展示模板（v1）

**用途**：把 `2026-08-31` 的日期限定 `DRY_RUN` postflight 结果，以经过 RLS 和字段投影的方式展示在“我的分红工具”Part 0。

这是一份**本地前端契约和展示模板**，不是运行结果、不是 Hosted SQL、不是 VPS 配置，也不授权网页主动触发 VPS、行情或模拟盘接口。

配套模板：

- `vps-runtime-audit-display-v1.template.json`

---

## 1. 权威边界

```text
VPS 本地 SQLite / active cache / sanitized export
    → VPS 出站 HTTPS publish
    → Supabase 私有表 / 窄 RPC / RLS 字段投影
    → GitHub OAuth 已授权管理员的 Part 0
```

浏览器不得直接读取 VPS、调用 HiThink、调用妙想、执行 SSH、读取本地日志文件，或调用 Edge service-role 路径。

| 数据域 | 页面展示方式 | 不能从页面推断的内容 |
|---|---|---|
| 目标 revision | 编号、状态、股票数量、证据是否通过 | 股票代码列表、成员哈希、原始合同 |
| VPS activation | local stage、active revision/generation、cache/ACK 布尔状态 | SQLite 路径、raw cache、原始 ACK body |
| 运行周期 | 17 个时点统计、EOD 是否完成、失败类别 | 原始 systemd/journal 日志 |
| 行情链路 | provider 名称、请求/返回/新鲜数量、完整性状态 | 原始报价、Provider payload、密钥 |
| 账户只读 | 只读调用次数与状态 | 账户标识、持仓、成本、余额、市值、P&L |
| 交易安全 | 真实订单/撤单写调用是否为零；`would_submit` 仅作本地审计 | 订单号、委托内容、价格、数量 |

---

## 2. 不可混淆的状态机

页面必须把以下事实分开，不得将 HTTP 200、`sync_pending`、inbox receipt 或本地 preview 显示为 activation。

```text
submitted
  → sync_pending
  → received / preparing
  → activated（VPS SQLite 事务已提交）
  → active cache published
  → ACK accepted
```

### 对应页面用语

| 原始状态 | 页面文字 | 禁止文字 |
|---|---|---|
| `submitted` | 已提交，等待网关领取 | 已生效 / 已激活 |
| `sync_pending` | 已领取，等待 VPS 持久化处理 | VPS 已运行成功 |
| `received` | VPS 已接收，待本地市场包核验 | 已切换策略白名单 |
| `preparing` | 等待或核验本地市场快照 | 激活中且必然会成功 |
| `activated` | VPS SQLite activation 已完成 | Hosted ACK 已接受（除非 ACK 证据也通过） |
| ACK `accepted` | Hosted 已接受 activation ACK | 本轮行情、账户或 EOD 必然成功 |
| `rejected` / `failed` | 已拒绝 / 本轮失败，保留旧 active revision | 自动重试或自动补跑 |

---

## 3. Part 0 页面映射

### 顶部单一状态条

只显示四个短字段：

```text
数据链路：正常 / 异常 / 等待运行
最后一次合法周期：时间或“未运行”
DRY_RUN
revision 1：状态
```

绿色“正常”只有在以下全部通过时才能出现：

1. `execution_mode=DRY_RUN`；
2. 17 个目标时点都已完成，或明确显示不完整而非绿色；
3. 行情覆盖完整且无陈旧/缺失行；
4. SQLite `quick_check=ok`；
5. 没有真实订单写调用，也没有真实撤单写调用；
6. 如果 revision 1 被本轮处理，必须同时具备 durable activation、active-cache 和 accepted ACK 证据；
7. 收尾后通用 market timer 与 backup timer 仍为 disabled。

### 白名单状态卡（三张）

| 卡片 | 对应模板字段 | 正确语义 |
|---|---|---|
| 目标白名单 | `desired_revision` | 用户提交给 VPS 的不可变目标，不等于 active |
| VPS 已激活白名单 | `activation.active_revision_no` / `active_generation` | 只能由 SQLite activation 证据确认 |
| 私有访问 | OAuth/RLS 结果 | 未登录或非管理员时不显示私有运行数据 |

### VPS 脚本运行（五张卡）

| 卡片 | 字段来源 | 状态原则 |
|---|---|---|
| 09:00 自检 | postflight self-check event | 未观察不能显示通过 |
| 行情快照 | `market.coverage_status` | 不完整或陈旧必须红色 / fail-closed |
| 指标计算 | sanitized cycle status | 未处理时显示未观察 |
| 状态持久化 | `persistence` + `activation` | inbox 与 activation 分开 |
| 15 分钟策略周期 | `cycle` | 只显示 17 个目标时点的实际统计 |

### 最近运行记录

最多 4 行；每行只能使用：

```json
{
  "at_cn": "2026-08-31T15:00:00+08:00",
  "severity": "info | warn | error",
  "category": "scheduled_cycle | control_sync | market | persistence | safety | eod",
  "message": "经过脱敏的短文本"
}
```

不得显示原始 Python 异常栈、systemd journal、文件路径、Endpoint、订单/账户标识或 Provider 原始响应。

---

## 4. 收盘后模板填写规则

| 审计项 | 模板字段 | 通过条件 | 页面失败文本 |
|---|---|---|---|
| 日期限定 17 个时点 | `cycle.expected_slots/triggered_slots/completed_slots` | `17 / 17` | 本轮不完整，未补跑 |
| 22 股行情完整性 | `market.*symbols` | requested = returned = fresh，missing/stale = 0 | 行情不完整或陈旧，已 fail-closed |
| 只读账户边界 | `account_readonly` | 仅只读调用有审计证据 | 账户只读核对异常 |
| 真实订单写调用 | `safety.real_order_write_calls` | `0` | 发现写调用，必须升级为安全事件 |
| 真实撤单写调用 | `safety.real_cancel_write_calls` | `0` | 发现撤单调用，必须升级为安全事件 |
| SQLite | `persistence.sqlite_quick_check` | `ok` | SQLite 完整性检查未通过 |
| revision 1 activation | `activation.local_stage` 等 | `activated`、cache published、ACK accepted | 当前停在具体阶段，不得笼统称为激活 |
| timer 收尾 | `safety.generic_timer_enabled/backup_timer_enabled` + postflight | 两者均 false，日期 timer 已移除 | timer 收尾未完成 |

---

## 5. 字段白名单与拒绝字段

页面允许的数据仅限模板 JSON 中的字段。以下数据即使存在于 VPS 或 Hosted 侧，也不能进入 GitHub Pages bundle、公开 JSON、普通运行日志或 Part 0 DOM：

```text
HMAC / service-role / Provider key / OAuth secret / SSH 详情
账户标识 / 实际持仓 / 成本 / 余额 / 市值 / P&L
订单号 / 委托价格 / 委托数量 / 成交明细 / 撤单明细
原始 Provider 响应 / 原始 ACK body / 原始 systemd 日志
VPS 主机、端口、用户名、文件系统路径、预签名 URL
成员、payload、raw-contract 的完整 hash 值
```

---

## 6. 本地预览与真实接入的边界

当前 `assets/app.js` 的 `?part0-local-preview=1` 只能使用显著标记的固定布局示例，且不应读取此模板之外的真实数据。正式接入必须满足：

1. GitHub OAuth 已登录；
2. `vps_is_admin` / RLS 验证通过；
3. 浏览器只读取 Supabase 投影出的此白名单字段；
4. 普通公开路由继续显示安全空状态；
5. 周一 postflight 结果先通过本模板的语义检查，才允许替换 `not_run` / `not_observed` 字段。

---

## 7. 当前模板初始状态

模板中故意保持：

```text
overall.state = not_run
desired_revision.status = sync_pending
activation.local_stage = not_observed
cycle.* = null / not_observed
```

这些是当前准确边界。模板不得因为周一 timer 已武装就显示已运行、已激活、账户空仓、行情正常或 ACK 成功。
