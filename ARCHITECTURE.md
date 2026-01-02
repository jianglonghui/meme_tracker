# Meme Tracker 架构

## 核心目标
追踪推文发出后代币的市值变化，看推文对代币的影响。

## 模块

### news_service (5050)
获取推文，推送到流。

### token_service (5051)
获取新代币，推送到流。

### match_service (5053)
提取推文关键词，匹配新币和搜索老币，发送到 tracker 追踪。

### tracker_service (5052)
内存中追踪代币 1/5/10 分钟市值变化。追踪结束后判定：新币市值 >= 10万 或 老币涨幅 >= 10% 才写入 token_tracker.db。

### dashboard (5080)
显示状态和数据，提供测试功能。
