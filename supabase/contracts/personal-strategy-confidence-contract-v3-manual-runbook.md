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

1. 替换 `personal_publish_strategy_worker_result(...)` 的校验合同；
2. 在**完整旧批次可明确识别为 0—1 刻度**时，升级当前 `strategy_analysis` 文档；
3. 重新加载 PostgREST schema cache。

它不会：

- 创建、删除或修改交易、持仓、自选股、反馈、VPS 或自动下单路径；
- 展示、导出或复制任何私有策略正文、令牌、密码、订单或账户数据；
- 修改 `personal_strategy_worker_runs` 中的原始 Worker 审计载荷；
- 删除原始 `source_path` / `source_sha256` 来源字段；
- 加载 launchd 定时任务。

对于没有真小数伴随值的旧 `1`，迁移**不会猜测**它是 `1%` 还是 `100%`。这种记录保持原样，页面会显示“研究匹配度待刷新”，等待一次新的 v3 Worker 结果。

## 手工执行顺序

> Hosted SQL 必须由账号所有者在 Supabase SQL Editor 手工执行；不要通过浏览器自动化、不要重跑早先已成功的 Worker/反馈 migration。

1. 在仓库打开并完整复制只读 preflight：

   ```text
   supabase/verification/personal_strategy_confidence_contract_v3_preflight.sql
   ```

   在 Supabase SQL Editor 手工执行。对当前可安全升级的旧结果，预期只出现一行聚合结果，其中：

   ```text
   safe_legacy_probability_batches = 1
   requires_fresh_worker_batches = 0
   ```

   它不显示股票、策略文本或其他私有内容。若 `requires_fresh_worker_batches > 0`，先停止并把这行聚合结果截图发回；不要尝试猜测或手改旧数值。

2. 在仓库打开并完整复制前向 migration：

   ```text
   supabase/migrations/20260830130000_personal_strategy_confidence_contract_v3.sql
   ```

3. 在 Supabase SQL Editor 手工执行该文件，确认事务成功。
4. 新建查询窗口，完整执行：

   ```text
   supabase/verification/personal_strategy_confidence_contract_v3_postflight.sql
   ```

5. 预期仅有一行聚合结果：

   ```csv
   total_checks,passed_checks,failed_checks,failed_check_names
   13,13,0,[]
   ```

6. 浏览器强制刷新或重新打开 GitHub Pages，退出后重新登录，进入 Part 6：
   - 原先错误的 `1%` 不应再出现；
   - 已被安全升级的旧结果应显示 `研究匹配度 <整数>%`；
   - 鼠标悬停标签可见“不是涨跌概率、收益概率或自动下单依据”的说明；
   - 已通过的反馈按钮状态不应回退。

## 仅在 postflight 失败时的处理

- **不要编辑、回滚或重跑**已提交成功的 migration。
- 若失败项是：

  ```text
  current_strategy_analysis_uses_v3_integer_research_match
  ```

  代表旧结果无法安全判定刻度。保持页面“待刷新”，在完成本迁移后手工运行一次本机 Worker：

  ```bash
  cd ~/.hermes/workspace/stock-dashboard
  python3 scripts/plus_strategy_worker.py run --force
  ```

  新 Worker 只接受 v3 的 `0—100` 整数输出；如果模型仍输出 `0.72`、缺少 `confidenceScale` 或其它不合格结构，Worker 将失败并保留上一条成功的分析，不会把错误值写回 Supabase。

- 若失败项是 RPC 权限、`SECURITY DEFINER`、锁定 `search_path` 或函数缺失，停止后把**聚合结果截图**发回；不要放宽 RLS、不要增加浏览器直连表权限。

## 后续运行边界

迁移与 postflight 通过后，才允许手工 Worker 运行使用 v3 发布合同。自动工作日调度仍保持未加载状态，除非另行获得明确授权。
