# ChatGPT Plus/Codex 本机策略 Worker 手工运行手册

## 目的

Part 6 的策略画像和操作复盘不再依赖 GitHub Actions 的 OpenAI Platform API Key。新链路使用用户本机已经登录的 Codex CLI（ChatGPT Plus/Codex 订阅），并将经过严格校验的派生结果写入当前账户的 Supabase `personal_*` 私有空间。

```text
macOS launchd（工作日一次）
  → scripts/plus_strategy_worker.py
  → Codex CLI（ChatGPT 订阅登录）
  → 严格 JSON Schema + 白名单校验
  → personal_publish_strategy_worker_result
  → personal_documents / personal_strategy_worker_runs
  → Part 6 personal_get_part6
```

## 隐私和边界

- 不使用、不读取、不保存 OpenAI Platform `OPENAI_API_KEY`。
- 不复制 ChatGPT/Codex OAuth 文件、Cookie、access token 或 refresh token 到 GitHub、Supabase、Pages 或聊天。
- 本机 Worker 的 Supabase refresh token 只放在 macOS Keychain；本地 `config.json` 仅保存项目公开 anon/publishable key、用户名和固定绑定信息。
- 用户密码只在首次 `setup` 的交互式终端输入，不写入文件；Worker 不读取浏览器 Cookie。
- Worker 只调用认证用户的窄 RPC；`authenticated` 没有 `personal_*` 表的直接 DML 权限。
- 模型输出不能修改固定护栏、执行参数或任何交易路径；VPS 仍保持 `DRY_RUN`。
- 失败时保留上一次成功的 `strategy_analysis`，只更新脱敏健康状态（如果当次仍能连接 Supabase）。

## Hosted SQL（必须由项目所有者在 Supabase SQL Editor 执行）

按顺序执行：

1. `supabase/migrations/20260830110000_personal_chatgpt_plus_worker.sql`
2. `supabase/verification/personal_chatgpt_plus_worker_postflight.sql`

预期 postflight：

```csv
total_checks,passed_checks,failed_checks,failed_check_names
10,10,0,[]
```

如果不是 `10/10`，不要重复 migration；保留失败检查名并先核对 Hosted catalog 状态。

## 本机首次设置

Hosted postflight 通过后，在本机项目目录执行：

```bash
cd /Users/songshuijingbaihe/.hermes/workspace/stock-dashboard
python3 scripts/plus_strategy_worker.py setup --username admin
```

按终端提示输入 Dashboard 用户名和密码。密码不会保存。setup 会：

1. 通过现有 `username-login` Edge Function 获取短期 session；
2. 仅把 refresh token 写入 macOS Keychain；
3. 通过 `personal_get_part6` 验证当前账户的私有读取权限；
4. 在以下本机私有目录保存无密码配置：
   `~/.hermes/workspace/stock-dashboard-private-worker/config.json`。

setup 成功只应输出 `setup_ok`、`chatgpt_subscription`、`keychain=configured` 和 `privateRpc=verified` 等状态，不应输出密码、token、个人记录或模型正文。

## 首次真实运行

setup 成功后先手动运行一次：

```bash
python3 scripts/plus_strategy_worker.py run --force
```

成功输出只应包含类似：

```json
{"status":"ok","authMode":"chatgpt_subscription","published":true}
```

然后在真实浏览器退出后重新登录 Dashboard，进入 Part 6，确认：

- 策略状态显示 `通过`，并标明 `ChatGPT Plus/Codex 本机 Worker`；
- `最后检查`时间已更新；
- 策略摘要/规则/建议来自新的私有结果；
- Part 6 三个反馈按钮仍能写入（若页面曾出现 SQLSTATE `42702` 对应的反馈失败，须先执行 `supabase/contracts/personal-feedback-rpc-ambiguity-repair-manual-runbook.md` 中的前向修复及 `11/11` postflight）；
- 页面、浏览器 Network、GitHub 仓库和 Release 中都没有密码、token、模型提示词或个人结果 JSON。

若 Codex、网络或 Supabase 临时失败，Worker 输出脱敏 `category`，不会输出 stderr、提示词或私有数据。

## 工作日调度（最后一步，必须单独执行）

只有 Hosted postflight、setup 和一次真实 `run --force` 成功后，才执行：

```bash
python3 scripts/plus_strategy_worker.py install-schedule
```

这一步只写用户目录下的 launchd plist，不加载任务。确认文件内容和目录权限后，再由用户明确执行：

```bash
python3 scripts/plus_strategy_worker.py install-schedule --load
```

调度语义：

- 北京时间工作日 08:45 作为目标运行时间；
- `RunAtLoad` 作为登录/唤醒后的补偿触发入口；
- 每个 Asia/Shanghai 日期通过 `codex-YYYY-MM-DD` 做幂等保护；
- 不强制唤醒 Mac，不承诺睡眠期间执行；
- 自动任务当天最多尝试一次；只有用户明确执行 `--force` 才允许手动重试；
- Worker 标准输出和错误日志保存在 `~/.hermes/workspace/stock-dashboard-private-worker/logs/`，目录权限为 `0700`，内容只允许是脱敏状态。

如需停用：

```bash
python3 scripts/plus_strategy_worker.py remove-schedule
```

## 旧 GitHub Actions

`check-strategy-api.yml` 和 `update-strategy.yml` 已改为仅手动说明入口，自动调度已停用。它们不再读取 Platform Key，也不再把个人策略结果写入 GitHub `main`/`live` 分支。Pages 仍是静态 UI；Part 6 的新结果来源是认证后的 Supabase 私有 RPC。
