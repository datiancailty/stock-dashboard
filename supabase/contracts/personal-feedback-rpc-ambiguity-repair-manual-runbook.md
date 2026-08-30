# Part 6 反馈按钮 SQLSTATE 42702 前向修复手册

## 已确认现象

Part 6 的 ChatGPT Plus/Codex 本机 Worker 可成功写入策略分析，但点击“已执行 / 没买 / 暂缓观察”等反馈按钮时页面显示：

```text
反馈未保存
私有数据接口暂时失败，请稍后重试
```

这与 Worker 成功是两条独立 RPC 路径，不能互相替代。

## 已确认根因

已使用当前认证账号和既有反馈的**冲突键**做无写入 v2 RPC 冒烟测试：数据库走完真实函数路径，但 `ON CONFLICT DO NOTHING` 保证反馈数量不变。结果为：

```text
HTTP 400
PostgreSQL SQLSTATE 42702
column_reference_ambiguous
```

此前 v2 RPC 内的 PL/pgSQL 局部变量 `source_id` 与表字段 `source_id` 同名；实际执行 `ON CONFLICT (owner_user_id, source_id)` 时存在歧义。此前的 catalog postflight、缓存探测和前端 stub 均不能替代这条真实运行路径。

## 必须由项目所有者在 Supabase SQL Editor 手工执行

> 不要重跑早前 migration，不要编辑历史反馈，不要执行 Stage 2 导入分片。

按顺序执行：

1. [前向修复 SQL](../migrations/20260830120000_personal_feedback_rpc_ambiguity_repair.sql)
2. [aggregate-only postflight](../verification/personal_feedback_rpc_ambiguity_repair_postflight.sql)

预期 postflight：

```csv
total_checks,passed_checks,failed_checks,failed_check_names
11,11,0,[]
```

该迁移仅：

- 以 `v_source_id` 等独立局部变量重建 v2 feedback RPC；
- 使用明确的 `personal_strategy_feedback_pkey` 冲突目标；
- 保留旧 RPC 为 v2 wrapper；
- 维持 authenticated-only execute、无 direct table DML、RLS、受限 digest search path；
- 通知 PostgREST 刷新 catalog。

它不会新增、修改、删除或重新导入历史反馈；不会修改 `vps_*`、Worker、交易、订单或策略参数。

## 修复后的验收顺序

1. SQL Editor 显示 postflight `11 / 11 / 0 / []`；
2. 我会再次运行同一条认证后的“冲突不写入”RPC 冒烟测试，确认 HTTP 成功且反馈数量仍不变；
3. 浏览器强制刷新、退出后重新登录，进入 Part 6；
4. 用户只点击**一个符合自己真实选择**的反馈按钮；
5. 页面显示“反馈已保存”，刷新后该建议仅显示对应已选状态；
6. 不再出现 `42702` 或泛化的“私有数据接口暂时失败”。

在第 1—5 步都通过前，不加载任何 LaunchAgent 定时任务。
