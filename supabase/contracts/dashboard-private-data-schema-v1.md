# 股票监控面板私有数据 Schema 与 RLS 合同 v1（第 3 步设计稿）

> 状态：`design_only`。这是第 3 步的本地设计和 SQL 草案，不代表 Hosted Supabase 已执行，不代表 VPS 或 Pages 已接入。
>
> 本阶段不执行：Hosted SQL、Auth 用户创建、私有数据导入、VPS runtime 变更、Git commit/push、Pages 发布。

## 1. 目标与强制边界

本合同同时满足四个要求：

1. 登录后的用户可以查看真实模拟盘持仓：股数、成本、当前未复权价格、市值、浮动盈亏和必要的时间/状态信息。
2. 持仓投影只对显式授权的 Auth 用户开放，不能通过公开 GitHub JSON、Pages bundle、匿名 REST 或旧 localStorage fallback 取得。
3. VPS 仍然只向 Supabase 出站同步，浏览器不直连 VPS；服务端写入仍走窄 RPC，不能给 `service_role` 开通新表的任意 DML。
4. 白名单变更继续使用 immutable revision；网页只提交目标版本，必须经过合法 VPS 周期、durable staging、SQLite activation、active-cache publish 和 ACK accepted 后才显示为已生效。

### 数据分区

```text
公开 GitHub/Pages
  └─ 公开产品数据、公开研究数据、UI 代码

Supabase Auth
  └─ 内部 email/password 身份、refresh session

Supabase app_usernames
  └─ 自定义用户名 → auth.users.id 的私有映射

Supabase vps_private_*
  └─ 显式 scope 授权下的当前模拟盘持仓投影

Supabase vps_* 控制面
  └─ 白名单 revision、VPS 脱敏运行状态、ACK；仍由 vps_admins 控制

VPS
  └─ 本地市场快照、账户只读读取、策略 DRY_RUN、私有投影发布
```

下列内容禁止进入私有投影表、私有读取 RPC、Git、Pages 或普通日志：

- 账户号、账户标识、订单号、成交回执、原始 provider payload；
- HMAC secret、service-role key、数据库密码、Provider key、交易凭据；
- 原始 VPS 日志、完整 API 响应、Authorization/Bearer 值；
- 由浏览器提交的 `user_id` 作为授权依据。

## 2. 新对象清单

| 对象 | 用途 | 浏览器访问 |
|---|---|---|
| `app_usernames` | 自定义用户名到 Auth 用户的私有映射 | 不直接访问 |
| `app_resolve_username(text)` | Edge 登录适配层的内部解析函数 | 仅 service_role |
| `app_get_current_username()` | 登录后返回当前用户显示名的窄 RPC | authenticated |
| `vps_private_scopes` | 私有投影 scope 与目标 device 的内部绑定 | 不直接访问 |
| `vps_private_scope_members` | scope → Auth 用户显式授权 | 不直接访问 |
| `vps_private_projection_state` | 每个 scope 的最新投影状态和新鲜度 | 通过窄 RPC |
| `vps_private_sim_positions` | 每个 scope 的当前持仓和数据库计算指标 | 通过窄 RPC |
| `vps_private_can_view_scope(text)` | RLS 的安全定义授权辅助函数 | 仅 RLS/内部使用 |
| `vps_private_get_portfolio()` | 认证用户私有读取 RPC | authenticated |
| `vps_private_get_runtime_display()` | Part 0 脱敏运行状态和最近 4 条事件 | authenticated + private scope |
| `vps_sync_replace_private_projection(text,text,jsonb)` | VPS 私有投影原子替换 RPC | service_role |
| `vps_get_whitelist_control_state()` | 管理员读取 active/desired/edit base | authenticated + `vps_is_admin()` |
| 三参数 `vps_submit_whitelist_revision(...)` | 带并发基线检查的 immutable revision 提交 | authenticated + `vps_is_admin()` |

旧的 `vps_sim_positions` 保持现有 Stage 1/2 管理员摘要语义，不直接添加成本、市值和 P&L 字段。新字段只进入 `vps_private_sim_positions`。

## 3. 身份映射表

### `app_usernames`

| 字段 | 类型 | 合同 |
|---|---|---|
| `user_id` | `uuid` | 主键，引用 `auth.users(id)`，删除 Auth 用户时级联 |
| `username_norm` | `text` | 唯一、3—32 位、ASCII 小写、首尾字母/数字、中间允许 `.` `_` `-` |
| `username_display` | `text` | 仅展示；不参与授权；最多 80 字符；禁止受保护词 |
| `status` | `text` | 仅 `active` / `disabled` |
| `created_at` / `updated_at` | `timestamptz` | 服务端时间 |

数据库约束使用 ASCII 小写正则；NFKC、trim 和统一小写由 Edge 适配层先做，数据库再次拒绝非规范值。v1 不开放网页注册，表只能由受控项目管理员路径维护。旧用户名不能被删除后重新分配；如将来支持改名，必须增加 tombstone 设计后再改表。

`app_resolve_username` 只返回内部 `auth.users.id` 或 NULL，不返回 email、用户名映射内容或账户状态细节。Edge Function 随后在服务端完成受控 Auth 密码校验，并向浏览器返回统一的成功/失败语义。

如果登录后的页面需要显示当前用户名，只调用 `app_get_current_username()`；该函数按 `auth.uid()` 读取当前 active 映射，不接受 user id/username 参数，也不返回内部 email。

## 4. 私有 scope 与用户授权

### `vps_private_scopes`

- `scope_key text primary key`：非密钥、稳定的内部 scope 名，例如 `primary`；不保存账户号。
- `target_device_id text`：引用既有 `vps_sync_devices`，只用于阻止其他设备发布。
- `display_label text`：例如“模拟盘”；不写账户标识。
- `active boolean`、时间戳。

### `vps_private_scope_members`

- 复合主键：`scope_key, user_id`；
- `access_role` v1 固定为 `viewer`，不提供浏览器交易权限；
- `active`、`granted_at`、`granted_by`、受保护词检查的内部备注；
- 由项目管理员在 Supabase SQL Editor 中对已确认的 Auth 用户显式插入；
- 不因登录成功、用户名叫 `admin` 或 `vps_admins` 命中而自动创建授权。

一个用户可以同时具有：

```text
vps_admins：可以读控制面、提交白名单目标 revision
vps_private_scope_members：可以读指定私有持仓投影
```

这两个授权集合不自动互相继承。即使是 VPS 管理员，没有 scope membership 也不能读取成本、市值和 P&L；普通 viewer 也不能提交白名单。

## 5. 私有投影状态

### `vps_private_projection_state`

每个 scope 一行，当前状态不保留历史持仓快照：

| 字段 | 语义 |
|---|---|
| `scope_key` | 主键 |
| `schema_version` | v1 为 1 |
| `mode` | 固定 `DRY_RUN`；禁止 `ARMED` |
| `health_status` | `ok` / `degraded` / `failed` / `unknown` |
| `projection_sequence` | VPS 本地持久递增序号；同序号同内容可重放，不同内容拒绝 |
| `active_revision_no` | 已被 VPS activation 证据确认的 revision；不是网页目标 revision |
| `active_generation` | 与 active revision 绑定的 VPS generation |
| `source_market_snapshot_id` | 独立验证的市场快照标识 |
| `source_market_snapshot_sha256` | 快照完整性 hash；只作为内部证据，不在 UI 展示 |
| `account_as_of` | 只读账户持仓读取时间 |
| `quote_as_of` | 当前价格快照时间 |
| `source_generated_at` | VPS 生成投影时间 |
| `position_count` | 当前投影行数，0—50 |
| `sanitized_error` | 限长、脱敏错误；不含 provider 原始响应或密钥 |
| `projection_digest` | 数据库内部用于同序号重放判定的 digest；不授予浏览器列权限 |

`projection_sequence` 与控制面 `active_generation` 是两个不同概念：前者是投影发布序号，后者是白名单/市场包激活代际，不能混淆。

## 6. 私有持仓字段与计算口径

### `vps_private_sim_positions`

主键为 `scope_key, symbol`。VPS 只提交原始获准字段，数据库生成派生金额：

| 字段 | 类型/规则 |
|---|---|
| `scope_key` | 引用私有 scope |
| `symbol` | 规范 `^[0-9]{6}\\.(SH\\|SZ)$` |
| `display_name` | 展示名称，最多 80 字符，受保护词检查 |
| `held_quantity` | `bigint >= 0` |
| `available_quantity` | `bigint >= 0` 且不超过 `held_quantity` |
| `average_cost_per_share` | `numeric(24,6)`；允许 NULL 表示成本缺失，保留来源口径 |
| `current_unadjusted_price` | `numeric(24,6)`；NULL 表示当前价格缺失；有值时必须 `> 0` |
| `price_as_of` | 有价格时必填；与价格使用同一快照时间 |
| `data_status` | `complete` / `stale_price` / `missing_price` / `missing_cost` / `unavailable` |
| `quote_source_kind` | v1 为 `hithink_batch_snapshot` 或 `not_available` |
| `position_state` | 小写有限字符键，不是交易指令 |
| `projection_sequence` | 所属投影序号 |
| `active_revision_no` | 所属 active revision；没有 active revision 时为 NULL |
| `source_generated_at` | 该行生成时间 |
| `cost_basis` | 数据库生成，不接受 VPS/浏览器直接提交 |
| `market_value` | 数据库生成，不接受 VPS/浏览器直接提交 |
| `unrealized_pnl` | 数据库生成，不接受 VPS/浏览器直接提交 |
| `unrealized_pnl_pct` | 数据库生成；成本基数 `<= 0` 时为 NULL |

正式计算：

```text
cost_basis = held_quantity × average_cost_per_share
market_value = held_quantity × current_unadjusted_price
unrealized_pnl = market_value − cost_basis
unrealized_pnl_pct = unrealized_pnl ÷ cost_basis × 100  （cost_basis > 0）
```

如果价格或成本缺失，金额字段为 NULL，页面显示“暂无”而不是 0；如果成本基数为 0 或负数，浮动盈亏百分比显示“—”，不强行制造百分比。`current_unadjusted_price` 只用于账户市值展示，不能被误标为前复权指标价格。

VPS 投影 payload **禁止提交** `cost_basis`、`market_value`、`unrealized_pnl`、`unrealized_pnl_pct` 等派生字段；这样可以避免客户端或上游重复计算造成口径漂移。

## 7. 私有读取 RPC

### `vps_private_get_portfolio()`

- 无用户可控参数，避免通过 scope 参数做枚举；
- `SECURITY DEFINER`，内部使用 `auth.uid()` 与显式 membership 判断；
- 非认证或没有 membership 时返回空的安全结果，不返回任何私有行；
- 返回 `scope_key`、展示标签、模式、健康状态、时间戳、active revision/generation 和持仓行；
- 不返回 `user_id`、内部 email、target device、projection digest、账户标识、订单数据、原始响应或密钥；
- 按 `market_value DESC NULLS LAST, symbol ASC` 排序；缺失市值放在末尾；
- 前端必须把 RPC 成功但 `positions=[]` 与“未登录/未授权/尚未观察到”区分展示，不能用公开 JSON 填充。

建议返回形状：

```json
[
  {
    "scope_key": "primary",
    "display_label": "模拟盘",
    "mode": "DRY_RUN",
    "health_status": "unknown",
    "projection_sequence": 0,
    "active_revision_no": null,
    "active_generation": null,
    "source_market_snapshot_id": null,
    "account_as_of": null,
    "quote_as_of": null,
    "source_generated_at": null,
    "position_count": 0,
    "sanitized_error": null,
    "positions": []
  }
]
```

上面只是字段形状示例，不是真实账户数据。真实返回不应写入 GitHub JSON 或静态 bundle。

### `vps_private_get_runtime_display()`

该 RPC 只返回私有 scope 成员需要的 Part 0 运行显示字段：`health_status`、`mode`、revision/generation、各阶段时间、Provider 读取计数、脱敏状态文本，以及按时间倒序最多 4 条已脱敏事件。它不返回设备标识、hash、账户/订单信息或原始 payload；未登录或没有 `primary` scope membership 时不返回私有运行状态。

## 8. VPS 写入 RPC 与同事务要求

### `vps_sync_replace_private_projection(device_id, scope_key, projection)`

仅 `service_role` 可执行，且只能被已部署的 HMAC gateway 在 `vps_sync_publish_request` 的同一数据库事务中调用。

允许的 projection 顶层字段：

```text
schema_version
scope_key
mode
health_status
projection_sequence
generated_at
account_as_of
quote_as_of
source_market_snapshot_id
source_market_snapshot_sha256
active_revision_no
active_generation
active_pack_sha256
active_control_payload_sha256
active_control_raw_contract_sha256
active_members_sha256
active_snapshot_id
active_snapshot_sha256
sanitized_error
positions
```

每个 position 仅允许：

```text
symbol
display_name
held_quantity
available_quantity
average_cost_per_share
current_unadjusted_price
price_as_of
data_status
quote_source_kind
position_state
```

服务器必须重复校验：

- device、scope 和目标设备绑定；
- `DRY_RUN`、1—50 行、规范代码、无重复；
- 数量和价格/成本数值边界；
- active revision 的完整证据链与数据库记录一致；
- source market snapshot 与 active revision 要求一致；
- 禁止账户/订单/密钥/原始 payload 字段；
- 同一 `projection_sequence` 仅允许完全相同内容重放；不同内容 fail-closed；较小序号拒绝；
- 删除旧行和插入新行、更新 state 必须在一个数据库事务内完成。

如果私有投影失败，整个 publish report 事务失败：不得产生“accepted” ACK，不得把新的运行状态提前显示到页面。ACK accepted 仍然只代表完整 activation/publish 链已经被协议验证，不能由浏览器或 HTTP 200 伪造。

## 9. RLS 与权限矩阵

所有新表启用 RLS，并撤销 `anon`、`authenticated` 和不必要角色的直接表权限。浏览器只获得两个窄读取/提交入口：私有 portfolio RPC，以及现有/更新后的 whitelist revision RPC。

| 主体 | 用户名映射 | 私有持仓 | VPS 状态/白名单 | 私有投影写入 |
|---|---:|---:|---:|---:|
| anonymous | 无 | 无 | 无 | 无 |
| authenticated 非 scope member | 无 | 空结果 | 仅在 `vps_admins` 中才有 | 无 |
| authenticated scope member | 无直接表读 | `vps_private_get_portfolio` | 仅在 `vps_admins` 中才有 | 无 |
| authenticated + `vps_admins` | 无直接表读 | 仍需 scope member | 管理员只读 + 白名单窄 RPC | 无 |
| Edge `service_role` | `app_resolve_username` | 不直接表读 | gateway 窄 RPC | `vps_sync_replace_private_projection` |
| SQL Editor 受控管理员 | 按项目权限 | 可 bootstrap/审计 | 可 bootstrap/审计 | 仅用于迁移/人工维护，不是网页路径 |

`vps_private_can_view_scope` 只服务于 RLS/内部函数；scope key 不是密钥，页面不直接调用它。RLS 授权的根依据仍是 `auth.uid()`，而不是浏览器传来的用户名、scope 或 user id。

## 10. 白名单 RPC 变更

现有两参数函数保留为历史对象但撤销所有 `EXECUTE`：

```text
vps_submit_whitelist_revision(text[], text)
```

新增唯一的浏览器写入口：

```text
vps_submit_whitelist_revision(
  p_symbols text[],
  p_request_note text,
  p_expected_base_revision_no bigint
)
```

新增参数解决旧页面/两个并发管理员基于旧列表提交的问题：

1. 页面先调用 `vps_get_whitelist_control_state()`；
2. RPC 计算编辑基线：最新 `submitted/sync_pending/preparing` desired revision；如果没有，则使用当前 `active` revision；两者都没有时为 NULL；
3. 页面提交完整的新 symbol 列表和 `expected_base_revision_no`；
4. RPC 在 advisory transaction lock 内重新读取基线；基线不一致就拒绝并要求刷新；
5. 通过现有 v2 contract hash、snapshot requirement、`DRY_RUN` 和 1—50 校验创建新的 immutable revision；
6. 旧 pending desired revision 只标记 `superseded`，不会改写 active revision；
7. 返回 `submitted`。之后页面必须显示 `sync_pending`/`preparing`，直到真实 VPS ACK/activation evidence 到达。

删除最后一只股票、空数组、`SUSPENDED_EMPTY`、默认池回退、浏览器直接写表和浏览器直接改 VPS 文件全部拒绝。删除白名单成员不等于卖出、清仓或删除模拟盘持仓。

建议 RPC 返回：

```json
{
  "revision_id": "<uuid>",
  "revision_no": 123,
  "status": "submitted",
  "base_revision_no": 122
}
```

这里的 UUID 只是形状示例；真实 revision/账户值不能复制到公开文件。

## 11. 执行顺序与验证门槛

### 本地准备阶段

1. 审阅本合同和 SQL 草案；
2. 运行静态 SQL/JSON 验证；
3. 再确认本地工作树和 `origin/main` 分歧；
4. 用户明确授权后，才把草案作为 Hosted SQL 输入。

### Hosted 手动阶段

1. 用户确认目标 Supabase 项目和 Auth 设置；
2. 用户在 SQL Editor 手动执行草案；
3. 手动建立/确认 Auth 用户；
4. 手动插入 `app_usernames` 映射和 `vps_private_scope_members` membership；
5. 执行 postflight，确认表/函数/RLS/ACL；
6. 先做 anonymous、authenticated 非授权、authenticated 授权三类读取验证；
7. 以上通过后才部署 username Edge 函数和私有投影 wire 更新。

### 发布阻断

- 任何新表未启用 RLS；
- anonymous 或非 member 能返回真实持仓；
- `authenticated` 能直接写私有表；
- `service_role` 获得新表任意 DML；
- 新私有投影能脱离 v2 HMAC/request receipt/activation 链被写入；
- 数据库接受前端提交的派生金额；
- 新 revision 没有 expected base 并发保护；
- 旧两参数白名单 RPC 仍可由 authenticated 执行；
- 前端仍可从公开 `trade-records.json`、旧 vault 或 localStorage fallback 显示真实私有数据；
- `ARMED`、交易买卖、撤单或账户写路径被打开。

## 12. 当前证据状态

| 项目 | 状态 |
|---|---|
| 设计稿 | 本地已创建，未提交/推送 |
| 机器可读合同 | 本地已创建，未提交/推送 |
| SQL 草案 | 本地已创建，明确 `DO NOT APPLY` |
| Hosted migration | 未执行 |
| Auth 设置/用户 | 未变更/未创建 |
| VPS 私有 projection | 未改动 |
| Pages 前端 | 未接入 |
| GitHub | 未授权、未提交、未推送 |
