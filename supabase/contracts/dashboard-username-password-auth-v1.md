# 自定义用户名 + 密码认证合同 v1（设计稿）

> 状态：`design_only`。本文件是第 2 步的可审查设计，不代表 Hosted Supabase 已执行，也不代表 GitHub Pages 已接入。
>
> 已确认的审阅决策：登录用户名采用 3—32 位 ASCII 规则，统一小写，首尾为字母/数字，中间允许 `.`、`_`、`-`。
>
> 本阶段明确不执行：Hosted SQL、Auth 生产设置变更、用户创建、密码重置、网页发布、Git commit/push。

## 1. 目标与非目标

### 目标

1. Part 0/1/2/4/5/6 共用同一个 Supabase Auth 会话。
2. 用户在网页上只输入自定义用户名和密码，不要求日常使用 GitHub 登录。
3. 同一浏览器在站点数据仍存在、用户没有主动退出、服务端没有撤销会话时，保持登录状态；不设置应用层固定重新登录期限。
4. 浏览器只持有 Supabase Publishable/anon key 和 Supabase Auth 会话，不持有 GitHub PAT、service-role key、Edge/HMAC secret、VPS 凭据、Provider key 或交易凭据。
5. Supabase `auth.uid()` 是私有数据授权身份；用户名只负责登录适配，不能成为 RLS 授权依据。
6. 用户名不存在、密码错误、账户停用等登录失败对浏览器返回统一错误，避免账户枚举。
7. 找回密码不依赖 GitHub 仓库文件，不在页面、Git、普通日志或 URL 中暴露找回邮箱。

### 非目标

- 不把 GitHub Pages 变成具有 HttpOnly cookie 的服务端应用；静态 Pages 无法自行签发该类 cookie。
- 不把旧 `data/auth-config.json` 加密 vault 继续作为认证机制。
- 不开放公开注册；第一个用户和后续用户均须受控创建。
- 不在本合同中设计持仓、成本、P&L 或 VPS 运行投影字段；这些属于后续私有数据合同。
- 不改变 `vps_admins` 的显式管理员授权语义；登录成功不自动成为 VPS 管理员。

## 2. 信任边界与目标链路

```text
GitHub Pages 静态前端
  └─ Supabase Publishable/anon key
  └─ Supabase Edge Function: username-login
       ├─ 私有 username → auth.users 身份映射
       └─ Supabase Auth email/password 密码校验
  └─ Supabase 浏览器持久 session
       └─ auth.uid() → RLS 私有读取

密码找回：
GitHub Pages → Edge Function: username-recovery-request
                 └─ 私有映射 → Supabase Auth recovery email
```

### 关键边界

- Supabase Auth 原生密码身份仍使用 email/password；“用户名”是服务端适配层，不是把用户名直接伪装成公开邮箱，也不是把密码放入静态文件。
- Auth 用户的实际 email 作为受控找回邮箱保存于 Supabase Auth。它不得进入 GitHub、Pages 静态 JSON、源码常量、普通日志或未认证响应。已登录浏览器可能从 Supabase Auth 用户对象看到该身份字段，但前端不展示、不写入业务数据、不发送到第三方。
- `username-login` Edge Function 可以在服务端解析用户名对应的 Auth 用户，然后使用服务端受保护的 Supabase Auth 调用完成密码校验。service-role/secret 只能存在 Supabase Edge Function 环境，不进入浏览器。
- 浏览器不得自行先查询“用户名是否存在”，不得直接查询用户名映射表来完成登录；这会造成账户枚举面。
- VPS、模拟盘、行情 Provider 和交易接口不参与登录链路。

## 3. 用户名规范化合同

### 3.1 登录用户名（v1）

为降低 Unicode 同形异义、大小写和规范化风险，v1 采用安全 ASCII 登录名：

- 输入先 `trim`；
- 使用 Unicode NFKC 规范化；
- 仅接受 ASCII 小写字母、数字、`.`、`_`、`-`；
- 服务端统一转 ASCII 小写；
- 长度为 3—32 个字符；
- 必须匹配：`^[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])$`；
- 首尾必须是字母或数字；
- 空白、控制字符、斜杠、反斜杠、`@`、中文及其他 Unicode 字符一律拒绝；
- 唯一性按 `username_norm` 判断，不区分大小写；
- v1 用户名不可由用户自行修改；受控改名时旧用户名保留为不可再次分配的 tombstone，避免身份转移或旧链接误指向新用户。

显示名称可以另行使用中文，但显示名称不是登录凭据、不是唯一身份、也不能进入登录映射查询。

### 3.2 映射表（拟议，不在本阶段执行）

拟新增受 RLS 保护的 `app_usernames`：

| 字段 | 约束/用途 |
|---|---|
| `user_id uuid` | 主键，引用 `auth.users(id)`，删除时级联清理映射 |
| `username_norm text` | 非空、唯一、按上述规则校验 |
| `username_display text` | 页面显示用，不参与授权 |
| `status text` | `active` / `disabled`；停用不删除历史身份 |
| `created_at timestamptz` | 服务端生成 |
| `updated_at timestamptz` | 服务端生成 |

该表不得保存明文密码、密码 hash、GitHub token、恢复链接、Provider 响应或账户/订单信息。实际 Auth email 仍由 Supabase Auth 管理，不复制到该表。

权限草案：

- `anon`：无表读取、插入、更新、删除权限；
- `authenticated`：原则上也不直接读取映射表；如 UI 需要显示当前用户名，只提供返回当前用户脱敏身份的窄 RPC；
- Edge Function 的服务端角色：仅用于受控解析和 Auth 适配，不向客户端返回内部 email 或映射表内容；
- RLS 授权永远使用 `auth.uid()`，不接受浏览器传来的 `user_id` 作为权限依据。

## 4. 用户创建与管理员授权

### 4.1 v1 不开放网页注册

- 页面不提供公开 `signUp`。
- 首个用户由项目管理员通过受控 Supabase Auth 管理路径创建，并确认 Auth 用户状态和找回邮箱。
- 用户名映射由受控管理路径写入 `app_usernames`；不能由未登录浏览器自行占用用户名。
- `vps_admins` 仍需对该 `auth.users.id` 做显式授权；第一次登录、用户名叫 `admin` 或登录成功，都不能自动授予 VPS 管理权限。

### 4.2 密码要求

- 初始设置和重置密码至少 6 个字符；允许长口令/口令短语，不把“必须包含某几类字符”作为唯一安全依据；
- 不在前端持久化或自行 hash 密码；密码只通过 TLS 发送到受控 Auth/Edge Function 路径；
- 不在错误、分析、访问日志、审计导出或 URL 中记录密码；
- 密码策略和泄露密码防护以 Supabase Auth 项目实际设置为准，正式接入前必须核对并记录；
- 密码被重置或安全事件发生后，必须验证旧 refresh session 是否失效；若项目默认行为不能满足，发布前必须增加受控的全局会话撤销步骤。

## 5. 登录 Edge Function 合同

### 5.1 请求

端点：`POST /functions/v1/username-login`

请求头：

- `Content-Type: application/json`；
- `Origin` 必须为允许的 Pages 生产 origin；本地验证 origin 另行显式配置；
- 拒绝 query 参数、非 POST 方法和过大的 body。

请求 JSON 仅允许：

```json
{
  "username": "用户输入的登录名",
  "password": "用户输入的密码"
}
```

服务端先按第 3 节规范化用户名；非法输入不进入映射查询，并返回统一登录失败语义。

### 5.2 成功响应

HTTP `200`，返回供浏览器 `supabase.auth.setSession()` 使用的最小 session 字段：

```json
{
  "access_token": "<仅作为响应示例，不得写入文件>",
  "refresh_token": "<仅作为响应示例，不得写入文件>",
  "expires_in": 3600,
  "expires_at": 0,
  "token_type": "bearer"
}
```

示例中的 token 只表示字段形状，不得把真实 token 写入本合同、测试 fixture、聊天、Git 或日志。响应不额外返回：

- 内部 email；
- username 映射表内容；
- service-role/HMAC/Provider/账户/订单字段；
- 密码校验的具体失败原因；
- 管理员身份或 VPS 状态。

### 5.3 失败响应

登录失败统一为：

- HTTP `401`：`{"error":"invalid_credentials"}`；
- 页面显示：`用户名或密码错误`；
- 不区分用户名不存在、密码错误、账户停用、映射不存在或 Auth 用户未确认；
- 不返回用户 ID、email、锁定剩余时间或是否存在账户。

限流时可以使用 HTTP `429`，但页面只显示：`登录请求过于频繁，请稍后重试`，不得说明该用户名是否存在。服务端错误只返回不含内部细节的通用错误 ID；日志只保存脱敏计数和错误类别。

### 5.4 限流与防枚举

正式实现的最低要求：

- 按 IP 和 `username_norm` 的不可逆摘要分别限流；不保存原始密码；
- 默认门槛：同一复合键 15 分钟内最多 5 次失败；持续失败进入退避；同一 IP 的全局失败也必须有限额；
- 限流状态必须由受控服务端持久化或使用已核对的 Supabase Auth 限流能力，不能只依赖浏览器计数；
- 账户不存在时也返回同类错误和近似响应路径，不提供“用户名存在性”探针；
- 记录访问时间、结果类别、限流计数和脱敏请求 ID，不记录密码、email、完整 IP 或内部映射值；
- Edge Function CORS 只允许明确 origin，不允许 `*` 搭配凭据；
- 函数必须拒绝重复/未知敏感字段，避免把调试字段写入日志或传给 Auth。

## 6. 浏览器 session 合同

### 6.1 初始化

正式前端使用 Supabase 浏览器客户端，配置目标为：

```js
{
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: false
  }
}
```

- session 存储在浏览器的 Supabase Auth storage 中；这属于浏览器会话持久化，不是 GitHub 文件，也不是自制的加密 GitHub token vault；
- 不添加应用层固定过期时间、不在每次页面加载时重置自定义 TTL；
- token 的短期 `expires_at` 只用于 Supabase 自动刷新，不等于要求用户重新登录；
- 同一浏览器只要站点数据、refresh session 和服务端授权仍有效，就恢复登录；
- 换浏览器、无痕窗口、清除站点数据、主动退出、服务端撤销、密码重置或异常安全处置可能要求重新登录；
- 页面恢复 session 后仍须通过 RLS 受保护的身份/权限读取确认页面能力，不能把“本地有 token”当作管理员授权。

### 6.2 登录成功后的顺序

1. 浏览器提交用户名/密码到 `username-login`；
2. 收到成功 session 后调用 `supabase.auth.setSession()`；
3. 监听 `onAuthStateChange`；
4. 先读取当前用户身份和管理员/用户权限摘要；
5. 权限确认后才读取私有持仓、白名单或运行投影；
6. 任何私有读取失败都显示受控错误状态，不回退到公开 GitHub JSON 冒充私有数据。

### 6.3 退出与失效

- “退出登录”必须调用 Supabase `signOut`，清理浏览器 session、内存中的私有数据和页面私有缓存；
- 退出后不能继续显示上一次的持仓、成本、P&L、用户名映射或 VPS 状态；
- 收到 `SIGNED_OUT`、refresh 失败、RLS `401/403` 或服务端撤销信号时，立即回到未登录安全空状态；
- 页面不能仅删除一个自定义 localStorage 标志而声称已退出；
- 后续可另设“撤销全部会话”安全操作，但它必须与单浏览器退出的 UI 语义区分。

## 7. 找回密码合同

### 7.1 请求找回

端点：`POST /functions/v1/username-recovery-request`

请求 JSON：

```json
{
  "username": "用户输入的登录名"
}
```

无论用户名是否存在，都返回相同的成功文案：

> 如果该账户允许找回，系统会向已登记的找回邮箱发送邮件。

响应不得包含 email、user ID、用户名是否存在、账户状态或发送失败的内部原因。Edge Function 内部使用受控 Auth email 触发 Supabase recovery 流程，并把回调地址限制为正式 Pages origin。

### 7.2 重置页面

- 页面只接受明确的 recovery callback；
- 采用 `detectSessionInUrl:false`，对 `?code=` 或 Supabase recovery 回调做一次显式交换；
- 成功建立 recovery session 后才显示新密码表单；
- 新密码再次执行至少 6 字符校验；
- 完成后使用 Supabase Auth 更新密码，立即清除 URL 中的 code/token，使用 `history.replaceState`；
- 找回链接、code、access token 不写入日志、Git、分析参数或业务表；
- 没有找回邮箱时不能通过读取 GitHub 文件恢复密码；由项目管理员使用受控 Auth 管理路径重置，并随后验证旧 session 撤销情况。

## 8. RLS 与业务授权边界

认证层只回答“这个浏览器属于哪个 Auth 用户”，不回答“这个用户能否操作 VPS”。

- 普通私有表：策略使用 `auth.uid() = user_id`；
- `vps_*` 控制面：继续使用显式 `vps_admins` 成员判断；
- 白名单写入：只能调用窄 RPC 创建 immutable revision，不能直接写表、不能直接改 VPS；
- VPS 运行快照、持仓投影和事件：浏览器只读，VPS/Edge 受控写入；
- 未认证浏览器不得通过匿名 fallback 读取任何真实私有数据；
- 用户名、Auth email、user ID 不能作为公开页面展示字段或 GitHub JSON 字段。

## 9. CSP 与静态页面要求

正式接入时：

1. 删除旧 `auth-config.json` 获取、浏览器 AES vault、GitHub bearer header 和 GitHub Contents 写路径；
2. `connect-src` 只增加实际 Supabase 项目 HTTPS origin 和必要的函数路径，不放宽为任意 `https:`；
3. 浏览器源码只包含 Supabase Project URL 与 Publishable/anon key；
4. 不包含 service-role/secret key、数据库密码、JWT secret、HMAC、VPS SSH、Provider key、交易凭据；
5. 保持严格 CSP，外部 SDK 必须版本固定或作为本地版本化资产；
6. 未登录页面显示安全空状态，不能因为静态 fallback 继续展示真实私有数据；
7. 登录、刷新、退出和 recovery callback 均使用版本化、可缓存失效的 JS/CSS 资产。

## 10. 本阶段后的实现顺序

1. 用户确认本合同的用户名规则、找回路径和无固定期限 session 语义；
2. 准备新的前向 migration：`app_usernames`、RLS、最小权限和必要的服务端辅助对象；
3. 准备并静态验证 `username-login` / `username-recovery-request` Edge Function；
4. 由用户在 Supabase Dashboard 手动创建/确认 Auth 用户并执行 Hosted SQL；
5. 先做匿名、已认证非管理员、已认证管理员三类 RLS 验证；
6. 再替换前端旧登录和 GitHub 写路径；
7. 最后接入私有持仓/运行投影，不把认证完成误报成数据已接入。

## 11. 发布阻断条件

下列任一条件未满足，不得发布正式 Pages：

- 仍能从公开页面读取旧 `auth-config.json` 并作为认证入口；
- 前端仍能持有或发送 GitHub PAT；
- 用户名映射表可被匿名读取或用于枚举；
- 登录错误可以区分“用户不存在”和“密码错误”；
- 浏览器没有持久 session 恢复、退出或撤销处理；
- 找回流程没有真实可用的私有邮箱或受控人工重置路径；
- RLS 只依赖 authenticated 角色而没有 `auth.uid()`/显式管理员判断；
- 未登录时有真实持仓、成本、P&L、账户、订单或 Provider 原始数据 fallback；
- 任何验证只检查源码，没有通过实际请求/浏览器 session/RLS 行为验证。

## 12. 当前证据状态

| 项目 | 当前状态 |
|---|---|
| 设计文档 | 本地已创建；未提交、未推送 |
| 机器可读合同 | 本地已创建；未提交、未推送 |
| Hosted migration | 未准备执行；本阶段不执行 |
| Auth 生产设置 | 未变更 |
| 用户创建/确认 | 未执行 |
| Pages 正式前端接入 | 未执行 |
| 旧公开页面 | 仍按现状运行，不能当作新合同已生效 |
| GitHub 授权/推送 | 未使用 |
