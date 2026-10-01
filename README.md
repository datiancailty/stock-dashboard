# 我的分红工具

GitHub Pages只承载静态界面；业务登录和私有数据由Supabase Auth/RLS/窄RPC保护，不依赖GitHub日常登录。

**当前维护入口：[MAINTENANCE-ENTRY.md](docs/MAINTENANCE-ENTRY.md)；版本状态：[STOCK-CURRENT-PUBLIC.md](STOCK-CURRENT-PUBLIC.md)。** 完整运维交接在私有维护归档；每次变更必须同步当前MD，每个Release保存对应版本MD。历史Git说明中的旧股票数量、待执行SQL、AI安装步骤不是当前任务清单。

## 页面

- Part0：只读VPS目标、实际激活、状态与私有模拟盘投影；管理入口跳到Part1。
- Part1：唯一自选股清单编辑入口，草稿/保存/认证读回；个人清单保存不等于VPS目标提交或激活。
- Part2：当前清单的周BOLL与日周月位置。
- Part3：可靠前瞻优先、已核实现金回退的股息率/目标价网格；未知不填零。
- Part4：分红实施事件、官方公告日与当前清单过滤。
- Part5：公司公告索引和保留的研究；AI新预估暂停。
- Part6：私有操作、反馈、历史研究与建议结果机械观察；AI自动画像/新策略暂停。

## 运行边界

确定性日更由独立VPS按工作日北京时间18:05执行；Mac自动日更暂停。Part0只读监控按工作日09:40、11:30、15:10执行，网页已登录且可见时每15分钟读私有结果。新鲜度按最近应到监控时点及宽限判断，不把午间、夜间和周末正常间隔当故障。交易授权与运行结果另以私有版本MD及现场回执确认，Dashboard成功不等于交易成功。

认证链：用户名密码 → username-login Edge → Supabase Auth → auth.uid/RLS/窄RPC。公开配置只放Supabase地址和publishable/anon key。凭据、个人记录、账户/订单、数据库和原始日志不得进入公开Git或Release。

## 当前说明

- [确定性日更与边界](docs/private-refresh-operations.md)
- [Part0/Part1统一清单](docs/unified-watchlist.md)
- [逐版本MD与发布回滚纪律](docs/release-rollback-policy.md)

旧修复SQL和历史runbook保留为取证。Hosted SQL仍由用户本人手动执行，不因源码中存在迁移而重新执行。任何策略、timer、ARM或写入权限变更都需要对应授权。
