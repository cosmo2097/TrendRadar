# TrendRadar Briefing Server 接口文档（用户版）

本文档面向接口调用方，覆盖接口说明、请求示例、错误处理、以及二次开发最佳实践。

## 1. 快速开始

### 1.1 服务启动

```bash
python -m briefing_server.main
```

默认地址：`http://127.0.0.1:8000`

### 1.2 健康检查

- 方法：`GET`
- 路径：`/health`
- 示例：

```bash
curl -s http://127.0.0.1:8000/health
```

- 响应示例：

```json
{
  "status": "ok",
  "version": "6.6.1"
}
```

## 2. 接口总览

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/v1/briefing` | POST | 一步生成 AI 简报（可流式） |
| `/api/v1/briefing/data` | POST | 只获取简报数据，不做 AI 分析 |
| `/api/v1/briefing/analyze` | POST | 对已有数据做 AI 分析（可流式） |
| `/api/v1/search` | POST | 新闻检索（热榜 + RSS） |
| `/api/v1/presets` | GET | 获取预设关键词组 |
| `/api/v1/sources` | GET | 获取可用平台与 RSS 源 |
| `/api/v1/reload` | POST | 热重载配置 |
| `/health` | GET | 健康检查 |

## 3. 通用说明

### 3.1 内容类型

`POST` 接口统一使用：

- `Content-Type: application/json`

### 3.2 流式返回（SSE）

`/api/v1/briefing` 与 `/api/v1/briefing/analyze` 在 `stream=true` 时返回 `text/event-stream`。

### 3.3 日期规则

- 格式：`YYYY-MM-DD`
- `search` 支持 `date_range`：
  - `{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}`
  - `"YYYY-MM-DD"`（单日）
  - 自然语言（例如：`今天`、`本周`、`最近7天`）
- 若 `search` 未提供 `date_range`，系统默认使用“有数据的最新日期”。
- 不允许查询未来日期。

### 3.4 常见错误码（search）

- `INVALID_SEARCH_MODE`
- `INVALID_SORT_BY`
- `INVALID_LIMIT`
- `INVALID_RSS_LIMIT`
- `INVALID_THRESHOLD`
- `MISSING_PRESET`
- `INVALID_DATE_RANGE`

## 4. 接口详情

### 4.1 生成简报

- 方法：`POST`
- 路径：`/api/v1/briefing`

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `rules` | string[] | 否 | 频率词规则列表 |
| `preset` | string | 否 | 预设组名称（与 rules 二选一） |
| `allowed_sources` | string[] | 否 | 允许的数据源 ID |
| `custom_rss_urls` | string[] | 否 | 额外实时抓取 RSS URL |
| `stream` | bool | 否 | 是否流式，默认 true |
| `ai_model` | string | 否 | 指定 AI 模型 |
| `date_range` | string | 否 | `daily` / `weekly` / `YYYY-MM-DD` |

请求示例（非流式）：

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/briefing \
  -H 'Content-Type: application/json' \
  -d '{
    "preset": "AI",
    "stream": false,
    "date_range": "daily"
  }'
```

### 4.2 获取简报数据（不分析）

- 方法：`POST`
- 路径：`/api/v1/briefing/data`

用途：用于“数据拉取”和“分析调用”解耦。

请求示例：

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/briefing/data \
  -H 'Content-Type: application/json' \
  -d '{
    "preset": "AI",
    "date_range": "daily"
  }'
```

### 4.3 AI 分析

- 方法：`POST`
- 路径：`/api/v1/briefing/analyze`

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `stats` | object[] | 是 | 热榜统计数据 |
| `rss_stats` | object[] | 是 | RSS 统计数据 |
| `ai_model` | string | 否 | 指定模型 |
| `stream` | bool | 否 | 是否流式 |
| `date_range` | string | 否 | 日期范围 |

请求示例（非流式）：

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/briefing/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "stats": [],
    "rss_stats": [],
    "stream": false,
    "date_range": "daily"
  }'
```

### 4.4 新闻搜索

- 方法：`POST`
- 路径：`/api/v1/search`

请求体字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `query` | string | 否 | - | 查询词（无 preset 时必填，支持多关键词，如 `人工智能 AI AIGC`） |
| `date_range` | object/string | 否 | 最新可用日期 | 日期范围（支持 MCP 风格） |
| `platforms` | string[] | 否 | 全部 | 指定平台 ID |
| `preset` | string | 否 | - | 预设关键词组（填写即启用） |
| `search_mode` | string | 否 | `keyword` | `keyword` / `fuzzy` / `entity` |
| `sort_by` | string | 否 | `relevance` | `relevance` / `weight` / `date` |
| `limit` | int | 否 | `50` | 范围 `1~1000` |
| `rss_limit` | int | 否 | `20` | RSS 条数限制（`include_rss=true` 时生效） |
| `threshold` | float | 否 | `0.6` | fuzzy 阈值 `0~1` |
| `include_url` | bool | 否 | `false` | 是否返回链接 |
| `include_rss` | bool | 否 | `false` | 是否同时检索 RSS |

示例1：关键词搜索

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"AI"}'
```

示例2：MCP 风格日期 + 同时检索 RSS

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{
    "query":"AI",
    "date_range":{"start":"2026-04-15","end":"2026-04-22"},
    "include_rss": true,
    "rss_limit": 10
  }'
```

响应结构（简化）：

```json
{
  "success": true,
  "summary": {
    "description": "新闻搜索结果（keyword模式）",
    "total_found": 40,
    "returned": 40,
    "search_mode": "keyword",
    "query": "AI",
    "time_range": "2026-04-22 至 2026-04-22",
    "sort_by": "relevance",
    "include_rss": true,
    "rss_found": 10,
    "rss_returned": 10
  },
  "data": [
    {
      "title": "灵魂摆渡电影全AI生成",
      "source_name": "微博",
      "source_id": "weibo",
      "type": "hotlist",
      "published_at": "10-00",
      "rank": 1,
      "count": 1,
      "similarity_score": 1.0,
      "weight_score": 1999,
      "url": "..."
    }
  ],
  "rss": [
    {
      "title": "AI Doesn't Reduce Work–It Intensifies It",
      "source_name": "Hacker News",
      "source_id": "hacker-news",
      "type": "rss",
      "published_at": "2026-04-22T10:00:00",
      "summary": "...",
      "similarity_score": 1.0,
      "url": "..."
    }
  ],
  "rss_total": 10
}
```

### 4.5 获取预设

- 方法：`GET`
- 路径：`/api/v1/presets`

### 4.6 获取数据源

- 方法：`GET`
- 路径：`/api/v1/sources`

### 4.7 热重载配置

- 方法：`POST`
- 路径：`/api/v1/reload`

## 5. 推荐调用流程

### 5.1 一步式（最简单）

直接调用 `/api/v1/briefing`，服务内部完成“取数 + AI 分析”。

### 5.2 两步式（可控性更强）

1. 调 `/api/v1/briefing/data` 获取结构化数据。
2. 自定义过滤/聚合后，把结果传给 `/api/v1/briefing/analyze`。

### 5.3 搜索 + 深读

1. 先用 `/api/v1/search` 精确检索。
2. 取返回的 `url` 再做正文解析或外部阅读。

## 6. 二次开发最佳实践（已整合）

### 6.1 架构边界

- `trendradar/`：爬虫、存储、报告、推送、调度等核心业务。
- `mcp_server/`：面向 AI Agent 的 MCP 工具层。
- `briefing_server/`：面向 HTTP 调用方的简报服务层。

建议保持“核心业务层”和“服务接入层”解耦。

### 6.2 扩展数据源

1. 在 `trendradar/crawler/` 新增 fetcher，尽量复用现有数据结构（`title/url/rank` 等）。
2. 在主流程中挂接新 fetcher。
3. 在 `config/config.yaml` 中补充平台配置。

### 6.3 扩展通知渠道

1. 在 `trendradar/notification/senders.py` 新增发送方法。
2. 在 `dispatcher.py` 注册分发逻辑。
3. 在 `config/config.yaml` 增加渠道配置。

### 6.4 扩展 MCP 工具

1. 在 `mcp_server/tools/` 新增能力模块。
2. 在 `mcp_server/server.py` 注册 tool。
3. 为每个工具写清晰 docstring，提升 AI 调用准确率。

### 6.5 AI 模型与提示词

- 模型：`config/config.yaml` 的 AI 配置。
- 提示词：`config/ai_analysis_prompt.txt`。
- 深度改造分析逻辑：`trendradar/ai/analyzer.py`。

### 6.6 调试建议

- 本地运行：`python -m trendradar` 或 `python -m briefing_server.main`
- 搜索调试：优先固定 `date_range`，排除“当天无数据”因素。
- 保持“配置 -> 调度 -> 执行”扩展模式，降低维护成本。

## 7. FAQ

### 7.1 为什么有时搜索为空？

常见原因：

1. 查询词过窄。
2. 日期范围内确实无匹配。
3. 平台过滤过严（`platforms` 限制）或未启用 `include_rss`。

### 7.2 如何提高召回？

1. 在 `query` 里直接写多个关键词（例如 `人工智能 AI AIGC`）。
2. fuzzy 模式适当降低 `threshold`（例如 `0.45~0.55`）。
3. 如需覆盖 RSS，请设置 `include_rss=true` 并调大 `rss_limit`。
