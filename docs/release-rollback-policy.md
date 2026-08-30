# 版本发布与回滚策略

本项目的 GitHub Pages 只承载静态 UI；Supabase 私有数据、VPS 运行投影和本机加密备份属于独立信任边界。GitHub Release 用于**代码与可追溯发布点回滚**，不用于保存个人数据或凭据。

## 何时必须创建 GitHub Release

以下任一“阶段性大版本”在完成部署验证后必须创建一个新的 GitHub Release：

1. 认证、RLS、`personal_*`、`vps_*` 或 Edge Function 契约发生结构性变化；
2. Part 0—6 的数据边界、私有读取路径或用户可见功能发生重大变化；
3. VPS release/协议、市场快照或策略执行边界发生版本切换；
4. 准备停用、删除或替换旧管理员源文件/旧浏览器管理路径之前。

Release 采用可排序标签，例如 `v0.2.0`；标题和说明必须包含变更范围、验证结论、明确的回滚目标与未覆盖边界。

## 每次发布的固定步骤

1. 重新检查 `git status`、分支、`origin/main` 分歧和待提交路径；不使用 `git add -A`、`git reset`、`git clean` 或强推。
2. 只暂存经过审阅的文件，运行语法、静态安全边界、私有数据泄漏扫描和 `git diff --check`。
3. 正常 commit/push；确认本地 commit 与 `origin/main` 一致。
4. 用 cache-busted URL 验证 GitHub Pages 的 HTML 和其精确引用的 CSS/JS；无法进行真实浏览器登录时，明确区分 HTTP/DOM mock 验证与用户浏览器验收。
5. 创建 GitHub Release，绑定该 commit 的标签，并再次读取 Release 的 tag、target commit 和 URL 作为回滚证据。

## 旧管理员源文件的特殊保留

- 旧管理员源文件、旧认证配置和旧浏览器管理实现**在用户验收前保持不删除、不重写**。
- 每次会影响这些文件的重大迁移前，先创建一个仍包含它们的 GitHub Release/tag；它是代码回滚点，不代表旧浏览器凭据路径被重新启用。
- 迁移期间的真实个人记录只存于 RLS 私有 Supabase、用户本机受控加密备份或已批准的私有系统；不加入 GitHub commit、Release asset、Release note 或 Pages 资源。
- 不创建任何包含 decrypted vault、Auth 密码、service-role、HMAC、Provider key、账户/订单、私有 SQL 导入文件或加密备份的 Release asset。
- Git 历史和公开 tag 不是删除旧公开数据的安全方式；停用旧路径后仍应保守地视为既有历史可能已被复制或缓存。

## 回滚原则

- **Pages/UI 回滚**：回退到经过验证的 GitHub Release tag 后重新部署；不得用恢复公开 JSON/旧浏览器 GitHub 写入能力作为回滚方式。
- **Supabase/VPS 回滚**：遵循对应的 forward migration、VPS release 或受控备份方案；Git tag 本身不会自动回滚 Hosted schema 或 VPS 状态。
- **旧管理员路径退役**：仅在私有导入完整性、各 Part 页面显示和用户浏览器验收完成，并由用户明确批准后执行；退役前必须已有最后一个 legacy-preserving Release/tag。
