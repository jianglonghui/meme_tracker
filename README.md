# Meme Tracker

一个多服务系统，用于监控加密货币 KOL 的推文，匹配新发行的 Meme 代币，并追踪价格表现。支持自动交易功能。

## 功能特性

- **推文监控**: 实时监听币安广场 KOL 推文
- **代币发现**: 监控 BSC 和 Solana 链上的新代币
- **智能撮合**: AI 提取推文关键词，匹配相关代币
- **价格追踪**: 追踪匹配代币的 1/5/10 分钟价格变化
- **自动交易**: 白名单作者推文触发自动买入，市值监控止盈止损
- **实时看板**: Web UI 实时展示所有服务状态（SSE 推送）

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置环境变量

```bash
export DEEPSEEK_API_KEY="your_deepseek_api_key"
export GEMINI_API_KEY="your_gemini_api_key"
export BINANCE_COOKIES="your_binance_cookies"
```

### 启动所有服务

```bash
python start.py
```

服务将在后台运行，日志保存在 `./logs/` 目录。

### 访问看板

打开浏览器访问: http://localhost:5080

## 服务架构

```
┌─────────────┐     ┌─────────────┐
│ news_service│     │token_service│
│   (5050)    │     │   (5051)    │
└──────┬──────┘     └──────┬──────┘
       │    SSE Stream     │
       └────────┬──────────┘
                ▼
        ┌───────────────┐
        │ match_service │ ◄── AI 关键词提取
        │    (5053)     │     (DeepSeek/Gemini)
        └───────┬───────┘
                │ 匹配信号
       ┌────────┼────────┐
       ▼        ▼        ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ tracker  │ │  trade   │ │  alpha   │
│ service  │ │ service  │ │  call    │
│  (5052)  │ │  (5055)  │ │  (5056)  │
└────┬─────┘ └────┬─────┘ └──────────┘
     │            │
     ▼            ▼
  SQLite      Telegram
  (追踪)       (交易)
                │
                ▼
        ┌───────────────┐
        │   dashboard   │ ◄── SSE 实时推送
        │    (5080)     │
        └───────────────┘
```

## 服务说明

### news_service (端口 5050)
- 轮询币安广场 API 获取 KOL 推文
- 支持作者白名单过滤
- 白名单作者推文触发高频模式

### token_service (端口 5051)
- 监控 BSC (币安 API) 和 Solana (DexScreener) 新代币
- 智能调频：普通模式 5秒/次，高频模式 1秒/次
- 高频模式仅监控 BSC，提升响应速度
- 并行请求多链，优化获取效率

### match_service (端口 5053)
- 消费推文和代币的 SSE 流
- AI 提取推文关键词（DeepSeek 文本 / Gemini 图片）
- 60 秒时间窗口内匹配代币
- 支持硬编码匹配和搜索旧代币

### tracker_service (端口 5052)
- 追踪匹配代币的价格变化
- 1/5/10 分钟检查点
- 仅保存达到阈值的记录（新币 >= 10万市值 或 涨幅 >= 10%）

### trade_service (端口 5055)
- 接收匹配信号，白名单过滤
- 自动买入/卖出（通过 Telegram Bot）
- 市值监控：止盈（翻倍卖50%）、止损（跌50%清仓）
- 无波动检测：市值无变化自动清仓
- 配置持久化到 `trade_config.json`

### alpha_call_service (端口 5056)
- 监控 Telegram Alpha 群组的合约推荐
- 追踪合约市值变化

### dashboard (端口 5080)
- Web UI 实时监控所有服务
- SSE 实时推送（每秒更新）
- 代币列表、持仓管理、交易历史
- 白名单/黑名单管理

## 配置说明

### config.py 主要参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `TIME_WINDOW_MS` | 60000 | 撮合时间窗口（毫秒） |
| `TRACK_INTERVALS` | [60, 300, 600] | 价格追踪间隔（秒） |
| `MIN_MCAP_TO_KEEP` | 100000 | 新币最低市值 |
| `MIN_CHANGE_TO_RECORD` | 0.1 | 旧币最低涨幅 |

### 交易服务配置

通过 Web UI 或 API 配置：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | true | 交易开关 |
| `default_buy_amount` | 0.5 | 默认买入金额(BNB) |
| `sell_trigger_multiple` | 2.0 | 触发卖出的倍数 |
| `sell_percentage` | 0.5 | 每次卖出比例 |
| `stop_loss_ratio` | 0.5 | 止损比例 |
| `max_positions` | 10 | 最大持仓数 |
| `no_change_timeout` | 20 | 无波动超时(秒) |
| `whitelist_mode` | any | 白名单模式 |

### 端口配置

通过环境变量覆盖默认端口：

```bash
export MEME_NEWS_PORT=5050
export MEME_TOKEN_PORT=5051
export MEME_TRACKER_PORT=5052
export MEME_MATCH_PORT=5053
export MEME_TRADE_PORT=5055
export MEME_DASHBOARD_PORT=5080
```

## API 接口

### 通用接口

每个服务都提供：
- `GET /health` - 健康检查
- `GET /status` - 服务状态
- `GET /recent` - 最近数据

### 特殊接口

```bash
# 触发高频模式
POST /api/token/boost

# 注入测试代币
POST /api/inject_token
{"symbol": "TEST", "name": "Test Token", "ca": "0x..."}

# 交易配置
GET/POST /api/trade/config

# 持仓管理
GET /api/trade/positions
DELETE /api/trade/positions/{id}

# 白名单管理
GET/POST/DELETE /api/trade/whitelist/authors
GET/POST/DELETE /api/trade/whitelist/tokens
```

## 数据库

### token_tracker.db (tracker_service)

- `match_records` - 推文匹配记录
- `matched_tokens` - 匹配的代币
- `market_cap_tracking` - 市值追踪
- `top_performers` - 最佳表现代币

### trade.db (trade_service)

- `positions` - 持仓数据
- `trade_history` - 交易历史

## 文件说明

```
meme_tracker/
├── start.py              # 启动脚本
├── config.py             # 配置文件
├── news_service.py       # 推文发现服务
├── token_service.py      # 代币发现服务
├── match_service.py      # 撮合服务
├── tracker_service.py    # 追踪服务
├── trade_service.py      # 自动交易服务
├── alpha_call_service.py # Alpha Call 服务
├── dashboard.py          # Web 看板
├── match_service/        # 撮合服务模块
│   ├── ai_clients.py     # AI 客户端
│   ├── matchers.py       # 匹配逻辑
│   ├── orchestrator.py   # 流程编排
│   └── state.py          # 状态管理
├── logs/                 # 日志目录
├── trade_config.json     # 交易配置
├── trade_author_whitelist.json   # 作者白名单
└── trade_token_whitelist.json    # 代币白名单
```

## 注意事项

1. 需要配置代理访问币安 API（默认 127.0.0.1:7890）
2. 交易功能需要配置 Telegram Bot API
3. 建议在测试环境充分验证后再用于实盘
4. 定期备份数据库文件

## License

MIT
