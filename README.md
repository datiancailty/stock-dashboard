# 股票数据监控面板

这是一个可直接部署到 GitHub Pages 的纯静态股票监控网页。

## 功能

- TradingView 行情滚动条
- TradingView 技术 K 线图
- 市场概览组件
- 自选股展示区
- 响应式暗色 UI

## 部署到 GitHub Pages

1. 新建一个 GitHub 仓库，例如 `stock-dashboard`
2. 上传本目录中的 `index.html` 和 `README.md`
3. 进入仓库 `Settings` → `Pages`
4. Source 选择 `Deploy from a branch`
5. Branch 选择 `main` / `/root`
6. 保存后等待 1-2 分钟，访问 GitHub 给出的 Pages 地址

## 修改股票

在 `index.html` 中搜索股票代码，例如 `NASDAQ:NVDA`、`NASDAQ:AAPL`，替换为你想看的股票即可。

