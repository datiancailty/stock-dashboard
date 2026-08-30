# 策略“研究匹配度”v3：Supabase 前向升级与验收

## 目的

修复旧版 `confidence` 的单位/语义不完整问题，统一为可验证的：

```text
confidenceScale = research_match_percent_0_to_100
confidence = 0—100 的整数
72 = 72%
```

页面统一称为“**研究匹配度**”。它仅表示当前建议与既定规则、当前数据和有限样本的一致性，**不是**涨跌概率、收益概率、收益保证或自动下单依据。

## 安全范围

本次前向迁移只会：

1. 替换 `personal_publish_strategy_worker_result(...)` 的未来写入校验合同；
2. 保持所有既有 `strategy_analysis` 文档原样，包括看似落在 `0—1` 范围的 v2 数值；
3. 重新加载 PostgREST schema cache。

它不会：

- 猜测、换算、乘以 100、回填或改写历史 `confidence`；
- 创建、删除或修改交易、持仓、自选股、反馈、VPS 或自动下单路径；
- 展示、导出或复制任何私有策略正文、令牌、密码、订单或账户数据；
- 修改 `personal_strategy_worker_runs` 中的原始 Worker 审计载荷；
- 删除原始 `source_path` / `source_sha256` 来源字段；
- 加载 launchd 定时任务。

历史 v2 结果没有独立的尺度标记。无论旧值看起来是 `0.72`、`1` 或其他数值，迁移都不会判断它代表多少百分比；它保持原样，页面显示“研究匹配度待刷新”，直到一次成功的 v3 Worker 发布新结果。

## 手工执行顺序

> Hosted SQL 必须由账号所有者在 Supabase SQL Editor 手工执行；不要通过浏览器自动化、不要重跑早先已成功的 Worker/反馈 migration。

1. 在仓库打开并完整复制只读 preflight：

   ```text
   supabase/verification/personal_strategy_confidence_contract_v3_preflight.sql
   ```

   在 Supabase SQL Editor 手工执行。它只返回一行聚合计数：

   ```text
   total_current_strategy_analysis
   already_v3
   legacy_pending_refresh
   requires_fresh_worker
   ```

   - `already_v3`：已经满足明确 v3 语义、尺度和整数范围的结果；
   - `legacy_pending_refresh`：可识别的旧 v2 无尺度结果，**这是预期状态，不是失败**；
   - `requires_fresh_worker`：结构无法识别或不满足 v2/v3 合同的结果，先停止并仅回传该行聚合截图，不要手工改写数值。

   preflight 不显示股票、策略文本或其他私有内容，也不做任何历史单位推断。

2. 在仓库打开并完整复制前向 migration：

   ```text
   supabase/migrations/20260830130000_personal_strategy_confidence_contract_v3.sql
   ```

3. 在 Supabase SQL Editor 手工执行该文件，确认事务成功。
4. 新建查询窗口，完整执行：

   ```text
   supabase/verification/personal_strategy_confidence_contract_v3_postflight.sql
   ```

5. 预期只出现一行聚合结果：

   ```csv
   total_checks,passed_checks,failed_checks,failed_check_names
   14,14,0,[]
   ```

6. 迁移和 postflight 通过后，重新打开或强制刷新 GitHub Pages，退出后重新登录并进入 Part 6，确认：
   - 旧 v2 结果显示 `研究匹配度待刷新`，不会再显示错误的 `1%`；
   - 鼠标悬停新标签可见“不是涨跌概率、收益概率或自动下单依据”的说明；
   - 已通过的反馈按钮状态不回退。

7. 仅在 `14/14` 通过后，进行一次明确意图的手工 v3 Worker 刷新：

   ```bash
   cd /Users/songshuijingbaihe/.hermes/workspace/stock-dashboard
   python3 scripts/plus_strategy_worker.py run --force
   ```

   新 Worker 只接受 v3 的 `0—100` 整数输出；如果模型仍输出 `0.72`、缺少 `confidenceScale` 或其它不合格结构，Worker 将失败并保留上一条成功的分析，不会把错误值写回 Supabase。

8. Worker 成功后，再次退出后登录 Dashboard 并进入 Part 6，确认：
   - 新结果显示 `研究匹配度 <整数>%`；
   - 不出现 `1%` 单位错误；
   - 反馈按钮仍能正常保存；
   - 页面、浏览器 Network、GitHub 仓库和 Release 中没有密码、token、模型提示词或个人结果 JSON。

## 仅在失败时的处理

- **不要编辑、回滚或重跑**已提交成功的 migration。
- 若 migration 或 postflight 失败，只回传 `total_checks`、`passed_checks`、`failed_checks` 和 `failed_check_names` 等聚合结果截图；不要放宽 RLS、不要增加浏览器直连表权限。
- 若 Worker 失败，保留旧 `strategy_analysis` 和脱敏失败健康状态；不要手改历史 `confidence`，不要自动重试或加载定时任务。

## 后续运行边界

只有迁移/postflight、一次手动 v3 Worker 刷新和真实浏览器 Part 6/反馈验收都通过后，才能考虑工作日自动调度。自动工作日调度仍保持未加载状态，除非另行获得明确授权。
