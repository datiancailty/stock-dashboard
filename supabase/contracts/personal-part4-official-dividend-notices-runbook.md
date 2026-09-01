# Part 4 官方分红公告账本：前向升级、补漏与验收

## 为什么要新增这条链路

Part 4 原先的私有 `market.events` 只保存已经具备**股权登记日 / 除权除息日 / 派息日**的实施事件；它没有“公告日”类型。旧 GitHub `live` 更新器还只轮转查询少量公开名单标的，且使用自然语言资讯/分红结果作为辅助，因此不能作为当前私有自选股公告日历的完整来源。

本升级把两类事实严格分开：

| 日历类型 | 证据与用途 | 是否改变正式股息率分子 |
|---|---|---|
| `分红方案公告` / `分红相关决议` / `权益分派公告` | 东方财富公司公告目录中的稳定公告号、公告日、标题、栏目和官方链接 | 否 |
| `中期分红预披露` | 官方公告链接 + 结构化分红字段二次核对；明确标为预披露 | 否 |
| `股权登记日` / `除权除息日` / `派息日` | 已实施现金分红事件 | 仍按既有正式口径处理 |

因此，公告日会出现在 Part 4，但不会提前把预案、政策或当前年度中期信息加入 Part 1 的正式股息率。Part 2 / Part 3 则单列“已公告待实施”每股分红作为**未来测算网格**，并明确标识，绝不称为已实施或保证收益。

## 新采集合同

本机脚本：

```text
scripts/part4_official_announcement_sync.py
```

主来源是东方财富公司公告目录：每一只**当前私有自选股**逐页检索，按公告号去重。它不是 LLM 抓取，也不使用 GitHub Pages 或公开 `live` JSON 作为 Part 4 写入来源。

写入前必须同时满足：

1. 当前私有自选股全部完成分页扫描；
2. 未出现超时、重复页、页数上限或无效响应；
3. 每条入选记录有 `eastmoney:AN...` 稳定公告号、公告日、当前自选股代码、受限官方 URL、标题、类型和来源；
4. 服务器再次核对代码仍属于当前账户自选股；
5. 任一覆盖失败时**不写入**，既有日历保持不变。

对于类似“半年度报告 / 董事会决议”但标题并未直接说明分红的披露，默认不会被猜测为分红公告。只有在明确指定代码并获得现有结构化分红字段的“预披露”二次核对后，才会以 `中期分红预披露` 标签加入；它不被伪装成正式方案或实施事件。

## Hosted SQL（尚未获准执行）

> 当前文件仍是候选源码：在最终独立复核、源码提交、Pages 验证完成前，**不要**执行 preflight、migration、摘要登记或任何私有同步。Hosted 前向 migration 一旦成功提交不得重跑；因此必须先关闭全部源码阻断项。

最终获准后的顺序将是：只读 preflight → 一次前向 migration → aggregate-only postflight → 单独复核的摘要登记 → 三条本机私有同步 → aggregate-only post-sync → 浏览器验收。项目所有者仅在获准后于 Supabase SQL Editor 手工执行；不使用浏览器自动化、不提供数据库密码、不重跑任何既有成功 migration。

### 预期的只读 preflight

文件：

```text
supabase/verification/personal_part4_official_dividend_notices_preflight.sql
```

它只返回聚合计数、存在状态和只读市场文档摘要；不再以固定股票数或固定日历条数作为完整性判断。

### 预期的前向 migration

文件：

```text
supabase/migrations/20260831010000_personal_part4_official_dividend_notices.sql
```

它创建独立的公告账本、精确本轮 source-ID 清单、归档/基线完整性表、私有行情快照表、未来分红网格快照表及受限 RPC；`personal_get_part4()`只在读取时组合这些表。它不会重写原始 `personal_documents.market`，不会碰 `vps_*`，不会交易或下单。

### 预期的可信写入能力登记

迁移和 postflight 都通过后，才可在本机运行一次：

```bash
cd /Users/songshuijingbaihe/.hermes/workspace/stock-dashboard
python3 scripts/part4_official_announcement_sync.py init-writer
```

它只在 macOS Keychain 写入随机能力密钥，并输出**不可反推密钥的 SHA-256 摘要**；不会输出原始密钥。随后由项目所有者在 Supabase SQL Editor 手工把这个摘要登记到 `personal_part4_sync_writer_credentials` 的当前 owner 行。浏览器没有该 Keychain 能力，因此不能伪造公告账本、行情快照或未来分红网格写入。

> 这一步的精确、最小化 SQL 必须在 migration postflight 已通过后单独生成、复核并获授权；不要提前写入或把原始密钥复制到 SQL、GitHub、浏览器或聊天记录。

### 预期的 aggregate-only postflight

新建查询窗口执行：

```text
supabase/verification/personal_part4_official_dividend_notices_postflight.sql
```

预期 `failed_checks = 0`。若不是全通过：不要回滚或重跑已提交的 migration；仅发送聚合结果截图。

## 2026 年 8 月一次性补漏（最终获准后）

在 postflight、摘要登记均通过且得到单独同步授权后，先在本机仓库执行公告补漏：

```bash
cd /Users/songshuijingbaihe/.hermes/workspace/stock-dashboard
python3 scripts/part4_official_announcement_sync.py sync \
  --from 2026-08-01 \
  --to 2026-08-31 \
  --include-structured-pre-disclosures \
  --structured-code 600036
```

这一命令：

- 重新分页查询当前私有自选股的整月官方公告；
- 用官方公告号去重；
- 只把确定的分红方案 / 权益分派 / 分红相关决议写入新的私有账本；
- 对招商银行 `600036` 的中期分红**预披露**做一次明确、受限的结构化二次核对；
- 不运行 Codex，不调用券商/VPS/交易接口，不修改策略参数；
- 输出仅含覆盖数、官方公告数、入选数、类型计数和脱敏写入摘要，不输出私有自选清单或公告正文。

成功输出必须同时具备：

```text
status = ok
coverageComplete = true
published = true
```

若输出 `error`，不应手工删改日历或重试部分股票；先保留错误类别并排查覆盖失败。

## post-sync 验收

三条本机同步都成功后，手工执行：

```text
supabase/verification/personal_part4_official_dividend_notices_post_sync.sql
```

三条同步分别是：公告账本补漏、私有行情快照、未来分红网格。后两条命令为：

```bash
python3 scripts/personal_market_snapshot_sync.py
python3 scripts/personal_future_dividend_grid_sync.py
```

两条快照输出都必须显示 `coverageComplete = true` 与 `published = true`；行情时间含义是**私有行情快照采集时间**，不是交易所逐笔/官方撮合时间。它们只覆盖当前 Part 1 私有自选股，绝不以 GitHub `live` 成功时间冒充私有快照时间。未来分红快照的正数仅允许状态 `已公告待实施` 或 `已公告预披露`，零值必须无状态；预披露只有在私有 Part 4 已有同代码的官方 `中期分红预披露`事实、且结构化来源给出可计算的每股金额时才能进入网格，并必须以“已公告预披露 · 未来测算”显示。它绝不修改正式分红字段。

1. `2026-08-29` 有云南白药、美的集团、中国核电的官方分红公告/决议条目；
2. 招商银行显示为 **中期分红预披露**，而不是被误标为已实施或正式每股现金方案；
3. 原有登记日、除权除息日、派息日仍存在；
4. Part 1 正式股息率未因上述公告/预披露而变化；Part 2 / 3 若存在已公告待实施金额，只能显示明确的未来测算标识；
5. 页面未出现私有令牌、原始公告正文、交易信息或公开 JSON 回退。

## 后续防漏运行边界

推荐每次使用一个滚动重扫窗口，而非仅从“上次成功时刻”开始，以覆盖周末公告、延迟入库和短暂网络失败。例如下一次手工核对可使用最近 35 天的日期范围。

脚本已经是可调度的本机安全组件，但**没有创建或加载任何 LaunchAgent**。若要每日自动运行，必须在本轮 Hosted、真实同步和浏览器验收全部通过后，再取得单独授权；届时应每日一次、使用重叠回扫和覆盖门禁，不使用高频轮询或强制唤醒。
