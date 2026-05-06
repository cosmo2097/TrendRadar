# TrendRadar MCP 接口文档（mcporter 视角）

## 1. 文档范围

- 服务地址（本次分析）：`http://127.0.0.1:3333/mcp`
- 工具总数：`27`
- 分析来源：`mcporter list --json` 的 `inputSchema/outputSchema` + 本地 `mcp_server/server.py`
- 文档时间：`2026-05-06`

## 2. 统一请求与响应封装

### 2.1 调用方式（ad-hoc）

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http <tool_name> key:value
```

### 2.2 响应外壳

所有工具的 `outputSchema` 一致：

```json
{
  "result": "{...JSON字符串...}"
}
```

- 第一层：mcporter/FastMCP 包装对象（固定 `result` 字段）
- 第二层：业务 JSON 字符串（需要再执行一次 `JSON.parse(result)`）

### 2.3 参数编码规则

- 标量：`key:value`，如 `limit:20`、`include_rss:true`
- 数组与对象：建议使用 `--args` JSON，避免 shell 转义问题
- ad-hoc 模式下，工具名不要带 server 前缀（`search_news`，不是 `trendradar.search_news`）

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http search_news --args '{"query":"AI","date_range":{"start":"2026-05-01","end":"2026-05-06"}}'
```

## 3. 工具索引

1. `resolve_date_range`
2. `get_latest_news`
3. `get_trending_topics`
4. `get_latest_rss`
5. `search_rss`
6. `get_rss_feeds_status`
7. `get_news_by_date`
8. `analyze_topic_trend`
9. `analyze_data_insights`
10. `analyze_sentiment`
11. `find_related_news`
12. `generate_summary_report`
13. `aggregate_news`
14. `compare_periods`
15. `search_news`
16. `get_current_config`
17. `get_system_status`
18. `check_version`
19. `trigger_crawl`
20. `sync_from_remote`
21. `get_storage_status`
22. `list_available_dates`
23. `read_article`
24. `read_articles_batch`
25. `get_channel_format_guide`
26. `get_notification_channels`
27. `send_notification`

## 4. 接口明细（27个）

### 4.1 `resolve_date_range`

- 说明：【推荐优先调用】将自然语言日期表达式解析为标准日期范围

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `expression` | 是 | `string` | - |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true,"expression":"本周","date_range":{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"},"current_date":"YYYY-MM-DD","description":"..."}`

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http resolve_date_range expression:<value>
```

### 4.2 `get_latest_news`

- 说明：获取最新一批爬取的新闻数据，快速了解当前热点

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `platforms` | 否 | `array<string> | null` | `null` |
| `limit` | 否 | `integer` | `50` |
| `include_url` | 否 | `boolean` | `false` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true,"summary":{"total":20,"returned":20,"platforms":["baidu", "..."]},"data":[{"title":"...","platform":"baidu","platform_name":"百度热搜","rank":1,"timestamp":"YYYY-MM-DD HH:mm:ss"}]}`

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http get_latest_news
```

### 4.3 `get_trending_topics`

- 说明：获取热点话题统计

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `top_n` | 否 | `integer` | `10` |
| `mode` | 否 | `string` | `current` |
| `extract_mode` | 否 | `string` | `keywords` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true,"summary":{...},"data":[{"keyword":"...","count":123,"rank":1}]}`

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http get_trending_topics
```

### 4.4 `get_latest_rss`

- 说明：获取最新的 RSS 订阅数据（支持多日查询）

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `feeds` | 否 | `array<string> | null` | `null` |
| `days` | 否 | `integer` | `1` |
| `limit` | 否 | `integer` | `50` |
| `include_summary` | 否 | `boolean` | `false` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true,"summary":{...},"data":[{"title":"...","feed_id":"...","published_at":"..."}]}`

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http get_latest_rss
```

### 4.5 `search_rss`

- 说明：搜索 RSS 数据

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `keyword` | 是 | `string` | - |
| `feeds` | 否 | `array<string> | null` | `null` |
| `days` | 否 | `integer` | `7` |
| `limit` | 否 | `integer` | `50` |
| `include_summary` | 否 | `boolean` | `false` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true,"summary":{...},"data":[{"title":"...","feed_id":"...","match_in":"title|summary"}]}`

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http search_rss keyword:<value>
```

### 4.6 `get_rss_feeds_status`

- 说明：获取 RSS 源状态信息

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| _(无参数)_ | 否 | - | - |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http get_rss_feeds_status
```

### 4.7 `get_news_by_date`

- 说明：获取指定日期的新闻数据，用于历史数据分析和对比

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `date_range` | 否 | `string | object | null` | `null` |
| `platforms` | 否 | `array<string> | null` | `null` |
| `limit` | 否 | `integer` | `50` |
| `include_url` | 否 | `boolean` | `false` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http get_news_by_date
```

### 4.8 `analyze_topic_trend`

- 说明：统一话题趋势分析工具 - 整合多种趋势分析模式

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `topic` | 是 | `string` | - |
| `analysis_type` | 否 | `string` | `trend` |
| `date_range` | 否 | `string | object | null` | `null` |
| `granularity` | 否 | `string` | `day` |
| `spike_threshold` | 否 | `number` | `3` |
| `time_window` | 否 | `integer` | `24` |
| `lookahead_hours` | 否 | `integer` | `6` |
| `confidence_threshold` | 否 | `number` | `0.7` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true,"summary":{...},"data":{"analysis_type":"trend|lifecycle|viral|predict","topic":"...","...":"..."}}`

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http analyze_topic_trend topic:<value>
```

### 4.9 `analyze_data_insights`

- 说明：统一数据洞察分析工具 - 整合多种数据分析模式

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `insight_type` | 否 | `string` | `platform_compare` |
| `topic` | 否 | `string | null` | `null` |
| `date_range` | 否 | `string | object | null` | `null` |
| `min_frequency` | 否 | `integer` | `3` |
| `top_n` | 否 | `integer` | `20` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true,"summary":{...},"data":{"insight_type":"platform_compare|platform_activity|keyword_cooccur","...":"..."}}`

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http analyze_data_insights
```

### 4.10 `analyze_sentiment`

- 说明：分析新闻的情感倾向和热度趋势

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `topic` | 否 | `string | null` | `null` |
| `platforms` | 否 | `array<string> | null` | `null` |
| `date_range` | 否 | `string | object | null` | `null` |
| `limit` | 否 | `integer` | `50` |
| `sort_by_weight` | 否 | `boolean` | `true` |
| `include_url` | 否 | `boolean` | `false` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true,"summary":{...},"data":{"sentiment_distribution":{...},"trend":{...},"news":[...]}}`

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http analyze_sentiment
```

### 4.11 `find_related_news`

- 说明：查找与指定新闻标题相关的其他新闻（支持当天和历史数据）

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `reference_title` | 是 | `string` | - |
| `date_range` | 否 | `string | object | null` | `null` |
| `threshold` | 否 | `number` | `0.5` |
| `limit` | 否 | `integer` | `50` |
| `include_url` | 否 | `boolean` | `false` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http find_related_news reference_title:<value>
```

### 4.12 `generate_summary_report`

- 说明：每日/每周摘要生成器 - 自动生成热点摘要报告

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `report_type` | 否 | `string` | `daily` |
| `date_range` | 否 | `string | object | null` | `null` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http generate_summary_report
```

### 4.13 `aggregate_news`

- 说明：跨平台新闻聚合 - 对相似新闻进行去重合并

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `date_range` | 否 | `string | object | null` | `null` |
| `platforms` | 否 | `array<string> | null` | `null` |
| `similarity_threshold` | 否 | `number` | `0.7` |
| `limit` | 否 | `integer` | `50` |
| `include_url` | 否 | `boolean` | `false` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http aggregate_news
```

### 4.14 `compare_periods`

- 说明：时期对比分析 - 比较两个时间段的新闻数据

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `period1` | 是 | `object | string` | - |
| `period2` | 是 | `object | string` | - |
| `topic` | 否 | `string | null` | `null` |
| `compare_type` | 否 | `string` | `overview` |
| `platforms` | 否 | `array<string> | null` | `null` |
| `top_n` | 否 | `integer` | `10` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true,"summary":{...},"data":{"periods":{...},"compare_type":"overview|topic_shift|platform_activity","...":"..."}}`

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http compare_periods period1:<value> period2:<value>
```

### 4.15 `search_news`

- 说明：统一搜索接口，支持多种搜索模式，可同时搜索热榜和RSS

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `query` | 是 | `string` | - |
| `search_mode` | 否 | `string` | `keyword` |
| `date_range` | 否 | `string | object | null` | `null` |
| `platforms` | 否 | `array<string> | null` | `null` |
| `limit` | 否 | `integer` | `50` |
| `sort_by` | 否 | `string` | `relevance` |
| `threshold` | 否 | `number` | `0.6` |
| `include_url` | 否 | `boolean` | `false` |
| `include_rss` | 否 | `boolean` | `false` |
| `rss_limit` | 否 | `integer` | `20` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true,"summary":{"total_found":26,"returned":5,"search_mode":"keyword","query":"AI"},"data":[...],"rss":[...],"rss_total":24}`

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http search_news query:<value>
```

### 4.16 `get_current_config`

- 说明：获取当前系统配置

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `section` | 否 | `string` | `all` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http get_current_config
```

### 4.17 `get_system_status`

- 说明：获取系统运行状态和健康检查信息

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| _(无参数)_ | 否 | - | - |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true,"summary":{"description":"系统运行状态和健康检查信息"},"data":{"system":{...},"data":{...},"cache":{...},"health":"healthy"}}`

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http get_system_status
```

### 4.18 `check_version`

- 说明：检查版本更新（同时检查 TrendRadar 和 MCP Server）

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `proxy_url` | 否 | `string | null` | `null` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http check_version
```

### 4.19 `trigger_crawl`

- 说明：手动触发一次爬取任务（可选持久化）

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `platforms` | 否 | `array<string> | null` | `null` |
| `save_to_local` | 否 | `boolean` | `false` |
| `include_url` | 否 | `boolean` | `false` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http trigger_crawl
```

### 4.20 `sync_from_remote`

- 说明：从远程存储拉取数据到本地

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `days` | 否 | `integer` | `7` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http sync_from_remote
```

### 4.21 `get_storage_status`

- 说明：获取存储配置和状态

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| _(无参数)_ | 否 | - | - |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http get_storage_status
```

### 4.22 `list_available_dates`

- 说明：列出本地/远程可用的日期范围

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `source` | 否 | `string` | `both` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http list_available_dates
```

### 4.23 `read_article`

- 说明：读取指定 URL 的文章内容，返回 LLM 友好的 Markdown 格式

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `url` | 是 | `string` | - |
| `timeout` | 否 | `integer` | `30` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http read_article url:<value>
```

### 4.24 `read_articles_batch`

- 说明：批量读取多篇文章内容（最多 5 篇，间隔 5 秒）

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `urls` | 是 | `array<string>` | - |
| `timeout` | 否 | `integer` | `30` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http read_articles_batch urls:<value>
```

### 4.25 `get_channel_format_guide`

- 说明：获取通知渠道的格式化策略指南

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `channel` | 否 | `string | null` | `null` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http get_channel_format_guide
```

### 4.26 `get_notification_channels`

- 说明：获取所有已配置的通知渠道及其状态

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| _(无参数)_ | 否 | - | - |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true|false, "summary":{...}, "data":{...}|[...]}`（具体字段按工具语义变化）

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http get_notification_channels
```

### 4.27 `send_notification`

- 说明：向已配置的通知渠道发送消息

参数：

| 参数 | 必填 | 类型 | 默认值 |
|---|---|---|---|
| `message` | 是 | `string` | - |
| `title` | 否 | `string` | `TrendRadar 通知` |
| `channels` | 否 | `array<string> | null` | `null` |

返回格式：

- 外层固定：`{"result":"..."}`
- 内层典型：`{"success":true,"summary":{...},"data":{"channels":[{"channel":"feishu","success":true,"message":"..."}]}}`

示例调用：

```bash
npx mcporter call --http-url http://127.0.0.1:3333/mcp --allow-http send_notification message:<value>
```

## 5. 已实测样例（2026-05-06）

- `get_system_status`：返回 `success/summary/data/health`，实测 `health=healthy`。
- `get_latest_news limit=20`：返回 `summary.total/returned/platforms` 与 `data[]`（含 `title/platform/platform_name/rank/timestamp`）。
- `search_news query=AI include_rss=true`：返回 `summary + data[] + rss[] + rss_total`。

## 6. 对接建议

- 若是 Web 项目，建议在后端/BFF 层封装 `mcporter` 调用，不建议前端直连 MCP。
- 对 `date_range/period1/period2` 一律先走 `resolve_date_range` 或传标准日期对象，避免模型自行推导日期。
- 对外 REST 化时，统一把外层 `result` 解析后再返回，避免下游重复解析。