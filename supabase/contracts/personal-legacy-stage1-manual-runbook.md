# Part 1—6 旧内容私有迁移：Stage 1 手动执行说明

状态：本地候选，尚未执行 Hosted SQL，尚未发布 Pages。

## 固定边界

- 目标账号：现有、已显式绑定的 `admin` Auth 用户。
- 仅迁移已批准的 13 个 legacy JSON；不读取或解密 `data/auth-config.json`。
- 不迁移浏览器本地存储，因为助手无法也不得读取用户浏览器中的旧持仓、覆盖值或策略设置。
- 不修改 `vps_*` 控制面，不启用 `ARMED`，不调用行情、模型、账户或订单接口。
- 验收完成前不删除旧数据、不重写 Git 历史、不停用旧管理员源文件/路径。
- Hosted SQL 只能由用户本人在 Supabase SQL Editor 手动执行。

## 手动执行顺序

1. 执行 schema forward migration：
   `supabase/migrations/20260830000000_personal_dashboard_legacy_stage1.sql`
2. 执行导入生成器本机输出的 mode `0600` 私有一次性导入 SQL；该文件位于受控私有迁移目录之外，不得加入 Git、Release asset 或聊天。
3. 执行聚合 postflight：
   `supabase/verification/personal_legacy_stage1_postflight.sql`
4. 只回传 postflight 的一行聚合结果，不回传 SQL、记录正文、ID、价格、持仓或任何 token。

期望聚合结果：

```text
total_checks=22
passed_checks=22
failed_checks=0
failed_check_names=[]
```

若任何一步失败：停止，不重复粘贴其他文件，不删除旧数据，并只提供错误类型与行号；不要粘贴包含个人记录值的上下文。

## 前端候选行为

- Part 1：VPS 私有持仓投影继续只读；额外显示迁入的原自选清单，并明确“不等于实际持仓”。
- Part 2：读取私有板块配置；按需补读 Part 1 自选清单。
- Part 4：读取私有 market/calendar 历史文档。
- Part 5：读取私有新闻元数据和记录。
- Part 6：按需读取私有交易、反馈、建议、画像、分析、检查点、回测和健康文档。
- 页面首次进入相应 Part 时才读取对应窄 RPC；15 分钟刷新只重读当前可见 Part，不轮询所有大数据集。
- 退出登录会清空浏览器内存中的个人数据；不使用公开 JSON 或浏览器本地存储回退。
- 验收阶段不开放 Part 2/Part 6 写入。
