# MA策略信号分析平台 - 版本历史

## [v1.1.1] - 2026-05-16

### 修复
- **重分析新闻正文抓取** — 之前只传标题/链接，现改用 `fetch_article_content` 抓取页面正文传入AI分析
- **vol_ratio 计算错误** — `app.py` 内的 `_fetch_indicators` 函数用 `close/vol5` 算成0，改用 `volumes[0]/vol5`（`app.py` 第941行）
- **重分析 reuse_news 丢失** — `reanalyzeStock()` 和 `batchReanalyze()` 前端未传 `reuse_news` 参数，后端无法判断是否复用已有新闻
- **重分析 signal_date 缺失** — `reanalyzeStock()` 未从URL读取signal_date，导致技术指标无法从数据库正确查询
- **batchReanalyze 缺 signal_date** — 前端POST时未传signal_date，后端`/api/full_analyze`收不到
- **historyCompAnalysis signalDate 未定义** — `loadHistoryDetail`的局部变量在`historyCompAnalysis`里引用报错，新增全局变量 `historyDetailSignalDate`
- **综合分析接口 400 错误** — `historyCompAnalysis`前端只传`results`未传`signal_date`，后端要求必须`results`
- **综合分析超时** — 24只股票的分析耗时较长（约2分钟），API超时60s已调整为180s

### 新功能
- **综合分析报告脚本** — `_get_comp_report.py` 从数据库读24只股票明细，调API生成报告，写入文件
- **腾讯频道发帖脚本** — `_post_final.py` 格式化报告内容，调用 `publish_feed` 发帖到腾讯频道

---

## [v1.1.0] - 2026-05-15

### 新功能
- **重分析保存机制** — 重分析结果自动写回数据库，刷新页面不丢失
- **新闻复用** — 已有 `news_links` 的股票不再重复搜索，直接复用已有新闻
- **技术指标传递** — 重分析时从数据库实时拉取量比/RSI/MA距离等技指
- **批量重分析勾选框** — 历史详情加复选框 + 全选按钮，只分析勾选股票
- **run_code 编号系统** — 格式 `yyyymmdd-NNN`（如 `20260515-001`），永久存储在数据库，删除自动重建

### 修复
- `vol_ratio` 计算错误：`_fetch_indicators.py` 改为 `volumes[0]/vol5`（之前误用 `close/vol5`）
- `api_analyze` 补全技术指标获取
- `api_comprehensive` 的 `signal_date` 默认值从空改为"当日"
- 历史记录列表 `display_num` 迁移修复

---

## [v1.0.0] - 2026-05-13

### 初始版本
- MA策略信号计算 + A/B/C/D 四级评级
- MiniMax M2.7 AI 新闻搜索 + 综合分析
- K线蜡烛图（A股红涨绿跌）
- 历史分析记录存储
- 信号展板（严格过滤：vol_ratio>=2.3x、量比用60日均量）
