# Part 1—6 完整私有恢复：Stage 2 手动执行说明

这一步修复的是“读取了旧 `data/` 快照而没有读取原 `live` 分支完整快照”的问题。

## 本次覆盖范围

- Part 1：20 只原自选股，恢复卡片、行情、正式分红和可选私有添加/删除；
- Part 2：周 BOLL 所需完整行情快照，20 只中 19 只带周 BOLL；
- Part 3：恢复 5.0% / 5.5% / 6.0% / 6.5% / 7.0% 股息率价格网格；
- Part 4：87 条历史分红事件；
- Part 5：452 条公告/资讯记忆；
- Part 6：384 条操作、9 条反馈、47 条建议，以及策略命中率和平均达标时间等完整分析字段；
- Part 6：恢复私有 CSV 导出、CSV 导入、新增操作和删除选中操作。

源数据来自本机已 fetch 的 Git 对象：

- `origin/live`：`market.json`、`news-memory.json`、`strategy-analysis.json`、`strategy-api-health.json`；
- 当前 `main`：自选清单、Part 2 配置、操作、反馈、建议、画像、检查点和专项研究。

一次性导入 SQL 由本机脚本生成，包含个人记录正文，因此只能保存在受控私有迁移目录的 mode `0600` 文件中，不能提交、上传到 Release 或发到聊天。

## 手动执行顺序

1. 在 Supabase SQL Editor 执行 forward migration：

   `supabase/migrations/20260830010000_personal_live_archive_stage2.sql`

2. **不要再执行原来的 1.68 MB 单文件导入。** Supabase SQL Editor 会拒绝过大的查询。请使用本机生成的分片目录：

   `~/.hermes/workspace/stock-dashboard-private-migration/personal-live-overlay-stage2-parts/`

   按文件名顺序逐个执行：

   `personal-live-overlay-stage2-00.sql` → `personal-live-overlay-stage2-09.sql`

   每个文件都要单独粘贴、单独执行，不能把多个文件合并到一个查询中。每个分片是独立事务；如果某个分片实际执行失败，该分片事务会回滚，修正后只需重跑失败的分片。已经成功的分片不要重复执行；如果必须从 `00` 重新开始，则按 `00` 到 `09` 全部重新执行。

3. 全部 10 个分片成功后，执行聚合 postflight：

   `supabase/verification/personal_live_archive_stage2_postflight.sql`

4. 只回传结果四列：

   ```text
   total_checks,passed_checks,failed_checks,failed_check_names
   ```

   预期为所有检查通过；不要回传公告正文、交易记录、ID、价格、持仓、token 或私有 SQL 内容。

## 重要边界

- 覆盖只发生在当前 `admin` 账号的 `personal_*` 私有空间；不会删除 GitHub 上旧 `live` 文件，也不会改动 `vps_*` 控制面。
- Part 1 的添加/删除只修改个人自选清单，不会改 VPS 白名单，不会自动卖出或删除模拟盘持仓。
- Part 6 的新增/删除/CSV 导入只写入当前账号私有表；不再写公开 GitHub JSON。
- 仍然保持 `DRY_RUN`，不会构造买卖、撤单或账户写入路径。
- 这次用户验收完成前，旧管理员源文件和旧公开历史仍按既定回滚策略保留；不要提前删除或重写 Git 历史。
