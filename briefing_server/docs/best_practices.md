# TrendRadar 二次开发最佳实践指南

本指南基于 v6.0.0 版本代码结构，为希望对 TrendRadar 进行二次开发的开发者提供架构概览和扩展建议。

## 1. 核心架构理解

TrendRadar 目前由两个主要部分组成：

- **`trendradar/` (核心业务)**：负责爬虫、数据存储、报告生成、消息推送。
- **`mcp_server/` (AI 能力层)**：基于 Model Context Protocol (MCP) 提供数据查询和分析服务，可被 Cursor、Claude Desktop 等 AI 客户端调用。

**关键目录结构：**

```
trendradar/
├── crawler/       # 数据采集 (目前主要依赖 NewsNow API)
├── notification/  # 消息推送 (飞书, 钉钉, Telegram 等)
├── storage/       # 数据存储 (SQLite, S3/R2)
├── ai/            # AI 客户端 (基于 LiteLLM)
└── core/          # 配置与调度 (Config, Timeline)

mcp_server/
├── server.py      # MCP 服务器入口
└── tools/         # MCP 工具实现 (搜索, 分析, RSS 等)
```

---

## 2. 扩展数据源 (Crawlers)

目前的 `trendradar.crawler.fetcher.DataFetcher` 主要依赖 `newsnow` 的统一 API 接口。如果你需要添加新的数据源（例如某个垂直领域的网站），建议按以下步骤操作：

1.  **创建新的 Fetcher**:
    在 `trendradar/crawler/` 下创建一个新的模块（例如 `custom_fetcher.py`），实现类似 `crawl_websites` 的接口。
    _建议：_ 尽量保持返回的数据结构与现有结构一致（包含 `items` 列表，每项有 `title`, `url` 等字段），以便复用后续的存储和分析逻辑。

2.  **集成到主流程**:
    修改 `trendradar/__main__.py` 中的爬虫调用逻辑，将你的自定义 Fetcher 加入到数据采集流程中。

3.  **配置支持**:
    在 `config.yaml` 的 `platforms` 节点下添加你的新平台配置，并在 Fetcher 中读取这些配置。

---

## 3. 扩展通知渠道 (Notifications)

这是最容易扩展的部分。所有推送逻辑都在 `trendradar/notification/` 中。

1.  **实现发送函数**:
    在 `trendradar/notification/senders.py` 中添加一个新的发送函数，例如 `send_to_discord(...)`。
    _参考：_ 参考 `send_to_feishu` 或 `send_to_slack` 的实现，注意处理**消息分批**（避免超长）和**Markdown渲染**。

2.  **注册调度逻辑**:
    修改 `trendradar/notification/dispatcher.py`:
    - 在 `NotificationDispatcher` 类中添加 `_send_discord` 方法。
    - 在 `dispatch_all` 方法中添加调用逻辑，读取配置并决定是否发送。

3.  **添加配置项**:
    在 `config/config.yaml` 中添加相应的 Webhook URL 配置项（如 `DISCORD_WEBHOOK_URL`）。

---

## 4. 扩展 MCP 工具 (AI Tools)

如果你想让 AI (Claude/Cursor) 拥有某种新能力（例如"查询股票价格"或"分析特定数据库"），请扩展 MCP Server。

1.  **编写工具逻辑**:
    在 `mcp_server/tools/` 下新建工具文件（或在现有文件中添加）。
    _示例：_

    ```python
    # mcp_server/tools/finance.py
    class FinanceTools:
        def get_stock_price(self, symbol: str):
            # ... implementation ...
            return {"price": 100.0}
    ```

2.  **注册工具**:
    在 `mcp_server/server.py` 中：
    - 初始化你的工具类。
    - 使用 `@mcp.tool` 装饰器注册异步函数，并在其中调用你的工具类方法。
    - **重要**：务必为工具函数编写详细的 Docstring，这直接决定了 AI 能否正确理解和使用该工具。

---

## 5. AI 模型与提示词定制

项目使用 `litellm` 库，理论上支持所有主流大模型。

- **更换模型**: 直接修改 `config.yaml` 中的 `ai.model` 字段（如 `openai/gpt-4o`, `deepseek/deepseek-chat`）。
- **自定义分析逻辑**:
  - 提示词位于 `config/ai_analysis_prompt.txt`。修改此文件可以调整 AI 生成日报的语气、格式和关注点。
  - 核心分析代码在 `trendradar/ai/analyzer.py`。如果你需要改变 AI 分析的输入数据结构（例如增加评论数据），请修改此处的 `analyze_hotlist` 方法。

## 6. 调试与验证

- **本地调试**:
  使用 `python -m trendradar` 直接运行核心流程，配合 `config.yaml` 中的 `debug: true` 选项。
- **MCP 调试**:
  使用 `npx @modelcontextprotocol/inspector uv run trendradar-mcp` (需安装 Node.js 和 uv) 来启动 MCP 检查器，可在浏览器中直接测试所有 MCP 工具。

---

**建议总结**：

- 保持核心 (`trendradar`) 和服务 (`mcp_server`) 的分离。
- 优先通过 `config.yaml` 和 `timeline.yaml` 解决需求。
- 扩展功能时，遵循现有的"配置 -> 调度 -> 执行"模式。
