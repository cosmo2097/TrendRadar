# coding=utf-8
"""
TrendRadar API 服务层
提供 BriefingService 用于生成 AI 简报
"""
import asyncio
import logging
from typing import List, Dict, Optional, Any, Tuple
from datetime import datetime

from trendradar.core import load_config
from trendradar.context import AppContext
from trendradar.crawler import DataFetcher
from trendradar.crawler.rss import RSSFetcher, RSSFeedConfig
# from trendradar.core.frequency import parse_frequency_rules # Removed
from briefing_server.utils import parse_frequency_rules # Use local util
from briefing_server.data import get_titles_by_date_range # Import local data agg
from trendradar.ai import AIAnalyzer, AIAnalysisResult
from trendradar.core.analyzer import convert_keyword_stats_to_platform_stats, count_rss_frequency
from datetime import timedelta
from litellm import completion # Import litellm directly for streaming


# 配置日志
logger = logging.getLogger("trendradar.api")


class BriefingService:
    """简报服务：负责协调数据抓取、统计和 AI 分析"""

    def __init__(self, config: Optional[Dict] = None):
        """初始化服务，加载配置和单例组件"""
        if config is None:
            config = load_config()
        self.config = config
        self.ctx = AppContext(config)
        self.data_fetcher = DataFetcher(self.ctx.config["DEFAULT_PROXY"] if self.ctx.config["USE_PROXY"] else None)

    async def generate_briefing(
        self,
        rules: List[str],
        allowed_sources: Optional[List[str]] = None,
        custom_rss_urls: Optional[List[str]] = None,
        ai_model: Optional[str] = None,
        enable_search: bool = False,
        stream_ai: bool = False,
        date_range: str = "daily",
        preset: Optional[str] = None,
    ) -> Dict[str, Any]:


        """
        生成简报（主入口，保留用于兼容或直接调用）
        """
        # 1. 获取数据
        data_result = await self.fetch_briefing_data(
            rules=rules, 
            allowed_sources=allowed_sources, 
            custom_rss_urls=custom_rss_urls, 
            date_range=date_range,
            preset=preset
        )

        
        # 2. AI 分析
        return await self.analyze_briefing_data(
            data_result["stats"],
            data_result["rss_stats"],
            ai_model,
            stream_ai,
            date_range
        )


    async def fetch_briefing_data(
        self,
        rules: List[str],
        allowed_sources: Optional[List[str]] = None,
        custom_rss_urls: Optional[List[str]] = None,
        date_range: str = "daily",
        preset: Optional[str] = None,
    ) -> Dict[str, Any]:


        """仅获取简报数据（不进行 AI 分析）"""
        logger.info(f"开始获取简报数据, 规则数: {len(rules)}")
        
        # 1. 解析规则 (内存操作)
        if preset:
            # 加载预设
            from trendradar.core.frequency import load_frequency_words
            all_groups, _, parsed_global = load_frequency_words()
            
            # 查找名称匹配的群组
            target_group = next((g for g in all_groups if g.get("display_name") == preset), None)
            
            if not target_group:
                raise ValueError(f"Preset '{preset}' not found")
            
            word_groups = [target_group]
            filter_words = []
            global_filters = parsed_global
            logger.info(f"使用预设 '{preset}', 关键词组数: {len(word_groups)}")
            
        else:
            # 使用自定义规则
            word_groups, filter_words, global_filters = parse_frequency_rules(rules)


        # 2. 并行获取数据 (本地缓存 + 实时 RSS)
        # 2. 并行获取数据 (本地缓存 + 实时 RSS)
        # 根据 date_range 计算日期
        # 使用 ctx.get_time() 获取带时区的当前时间，确保与数据生成时区一致
        now = self.ctx.get_time()
        
        if date_range == "weekly":
            # 最近 7 天 (含今天)
            end_date = now
            start_date = now - timedelta(days=6)
        elif date_range == "monthly":
            # 最近 30 天 (含今天)
            end_date = now
            start_date = now - timedelta(days=29)
        elif date_range == "daily":
            # 今天
            start_date = end_date = now
        else:
            # 尝试解析具体日期 (YYYY-MM-DD)
            try:
                target = datetime.strptime(date_range, "%Y-%m-%d")
                start_date = end_date = target
            except ValueError:
                # 解析失败，默认今天
                logger.warning(f"Invalid date_range: {date_range}, fallback to daily")
                start_date = end_date = now

            
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        
        # 传递日期范围给 _fetch_all_data
        all_results, id_to_name, title_info, rss_items = await self._fetch_all_data(
            allowed_sources, custom_rss_urls, start_str, end_str
        )



        # 3. 统计逻辑 (复用 core 逻辑)
        stats, matched_count = self.ctx.count_frequency(
            all_results,
            word_groups,
            filter_words,
            id_to_name,
            title_info,
            new_titles=None,
            mode="daily" if date_range == "daily" else "range",
            global_filters=global_filters,
            quiet=True
        )

        
        rss_stats, _ = count_rss_frequency(
            rss_items,
            word_groups,
            filter_words,
            global_filters,
            quiet=True
        )

        return {
            "success": True,
            "stats": stats,
            "rss_stats": rss_stats,
            "platforms": list(id_to_name.values())
        }

    async def analyze_briefing_data(
        self,
        stats: List[Dict],
        rss_stats: List[Dict],
        ai_model: Optional[str] = None,
        stream_ai: bool = False,
        date_range: str = "daily",
    ) -> Any:

        """对已有数据进行 AI 分析"""
        
        # 4. 准备 AI 分析
        if not stats and not rss_stats:
            no_content_result = {
                "success": True,
                "markdown": "今日无相关动态。",
                "stats": [],
                "rss_stats": []
            }
            if stream_ai:
                async def _empty_generator():
                    yield "今日无相关动态。"
                return _empty_generator()
            return no_content_result

        # 提取关键词用于 Prompt
        keywords = [s["word"] for s in stats]
        # 简单推断平台列表（从 stats 中获取 unique source_name，或者需要前端传回？
        # 为了简化，这里我们重新扫描 stats 中的 source_name，或者接受参数。
        # 由于 fetch_briefing_data 返回了 platforms，但在 analyze 接口中可能只传了 stats。
        # 我们可以从 stats 和 rss_stats 中提取 source_name。
        platforms = set()
        for s in stats:
            for t in s.get("titles", []):
                if "source_name" in t:
                    platforms.add(t["source_name"])
        for s in rss_stats:
            for t in s.get("titles", []):
                if "source_name" in t:
                    platforms.add(t["source_name"])
        platforms = list(platforms)

        # 初始化 AI 分析器
        ai_config = self.config.get("AI", {}).copy()
        if ai_model:
            ai_config["MODEL"] = ai_model
        
        analysis_config = self.config.get("AI_ANALYSIS", {})
        analyzer = AIAnalyzer(ai_config, analysis_config, self.ctx.get_time, debug=False)

        # 5. 执行 AI 分析
        if stream_ai:
            return self._stream_analysis(analyzer, stats, rss_stats, platforms, keywords, date_range)
        else:
            # 普通返回
            result = await asyncio.to_thread(
                analyzer.analyze,
                stats,
                rss_stats,
                date_range,

                "定制简报",
                platforms,
                keywords
            )
            
            return {
                "success": result.success,
                "markdown": result.core_trends,
                "stats": stats,
                "rss_stats": rss_stats,
                "error": result.error,
                "usage": None
            }

    async def _fetch_all_data(
        self,
        allowed_sources: Optional[List[str]] = None,
        custom_rss_urls: Optional[List[str]] = None,
        start_date: str = None,
        end_date: str = None,
    ) -> Tuple[Dict, Dict, Dict, List[Dict]]:
        """
        获取所有数据（新闻 + RSS），支持并行抓取自定义 RSS
        """
        # 1. 定义获取本地新闻的任务
        async def _fetch_local_news():
            if start_date and end_date:
                return await asyncio.to_thread(
                    get_titles_by_date_range,
                    self.ctx.get_storage_manager(),
                    start_date,

                    end_date,
                    allowed_sources,
                    True
                )
            else:
                return await asyncio.to_thread(
                    self.ctx.read_today_titles,
                    allowed_sources,
                    True
                )

        # 2. 定义获取本地 RSS 的任务
        async def _fetch_local_rss():
            # 目前 RSS 暂只支持读取今天的数据 (TODO: 支持范围)
            # 或者我们可以简单地循环读取每天的 RSS，但这里先保持简单
            storage = self.ctx.get_storage_manager()
            today = self.ctx.format_date()
            local_rss_data = await asyncio.to_thread(storage.get_rss_data, today)
            
            items = []
            if local_rss_data and local_rss_data.items:
                 for feed_id, feed_items in local_rss_data.items.items():
                     if allowed_sources and feed_id not in allowed_sources:
                         continue
                     for item in feed_items:
                         items.append({
                             "title": item.title,
                             "url": item.url,
                             "feed_name": local_rss_data.id_to_name.get(feed_id, feed_id),
                             "feed_id": feed_id,
                             "published_at": item.published_at,
                             "summary": item.summary
                         })
            return items

        # 3. 创建并发任务
        t1 = asyncio.create_task(_fetch_local_news())
        t2 = asyncio.create_task(_fetch_local_rss())
        t3 = self._fetch_custom_rss(custom_rss_urls) if custom_rss_urls else None

        # 4. 等待结果
        news_res = await t1
        local_rss_items = await t2
        custom_rss_items = await t3 if t3 else []

        # Unpack news results
        all_results, id_to_name, title_info = news_res
        
        # Merge RSS items
        final_rss_items = local_rss_items + custom_rss_items
        
        return all_results, id_to_name, title_info, final_rss_items



    async def _fetch_custom_rss(self, urls: List[str]) -> List[Dict]:
        """实时抓取自定义 RSS"""
        if not urls:
            return []
            
        feeds = [
            RSSFeedConfig(id=f"custom_{i}", name=f"Custom Feed {i}", url=url)
            for i, url in enumerate(urls)
        ]
        
        fetcher = RSSFetcher(
            feeds=feeds, 
            request_interval=0,
            timeout=10,
            use_proxy=self.ctx.config["USE_PROXY"],
            proxy_url=self.ctx.config["DEFAULT_PROXY"]
        )
        
        rss_data = await asyncio.to_thread(fetcher.fetch_all)
        
        items = []
        for feed_id, feed_items in rss_data.items.items():
            for item in feed_items:
                items.append({
                     "title": item.title,
                     "url": item.url,
                     "feed_name": f"Custom Feed ({item.feed_id})",
                     "feed_id": feed_id,
                     "published_at": item.published_at,
                     "summary": item.summary
                })
        return items

    async def _stream_analysis(self, analyzer: AIAnalyzer, stats, rss_stats, platforms, keywords, date_range="daily"):

        """流式生成器适配器"""
        # 1. 准备新闻内容 (复用 analyzer 逻辑, 访问私有方法)
        # 注意：这里我们假设 AIAnalyzer 没有修改，保留了 _prepare_news_content
        try:
            news_content, rss_content, hotlist_total, rss_total, total_count = analyzer._prepare_news_content(
                stats, rss_stats
            )
        except AttributeError:
             yield "Error: TrendRadar Core version mismatch."
             return
        
        if not news_content and not rss_content:
            yield "今日无相关动态。"
            return

        # 2. 构建 Prompt (本地实现，不依赖 analyzer 改动)
        user_prompt = self._construct_user_prompt(
            analyzer,
            date_range,

            "定制简报",
            platforms,
            keywords,
            hotlist_total,
            rss_total,
            news_content,
            rss_content,
            None
        )
        
        # 3. 构造完整消息
        messages = []
        if analyzer.system_prompt:
             messages.append({"role": "system", "content": analyzer.system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        
        # 4. 调用 LLM stream
        try:
            # 直接使用 litellm.completion 支持流式
            # 构建参数 (参考 AIClient.chat)
            client = analyzer.client
            params = {
                "model": client.model,
                "messages": messages,
                "temperature": client.temperature,
                "timeout": client.timeout,
                "stream": True
            }
            if client.api_key:
                params["api_key"] = client.api_key
            if client.api_base:
                params["api_base"] = client.api_base
                
            response = await asyncio.to_thread(
                completion,
                **params
            )
            
            for chunk in response:
                if chunk and chunk.choices:
                    delta = chunk.choices[0].delta
                    content = delta.content
                    if content:
                        yield content
                        await asyncio.sleep(0)
                        
        except Exception as e:
            logger.error(f"Stream analysis failed: {e}")
            yield f"\n[AI 生成失败: {str(e)}]"

    def _construct_user_prompt(
        self,
        analyzer: AIAnalyzer,
        report_mode: str,
        report_type: str,
        platforms: List[str],
        keywords: List[str],
        hotlist_total: int,
        rss_total: int,
        news_content: str,
        rss_content: str,
        standalone_data: Optional[Dict] = None
    ) -> str:
        """构建用户提示词 (复制自 AIAnalyzer 逻辑，避免修改 Core)"""
        current_time = analyzer.get_time_func().strftime("%Y-%m-%d %H:%M:%S")

        # 使用安全的字符串替换
        user_prompt = analyzer.user_prompt_template
        user_prompt = user_prompt.replace("{report_mode}", report_mode)
        user_prompt = user_prompt.replace("{report_type}", report_type)
        user_prompt = user_prompt.replace("{current_time}", current_time)
        user_prompt = user_prompt.replace("{news_count}", str(hotlist_total))
        user_prompt = user_prompt.replace("{rss_count}", str(rss_total))
        user_prompt = user_prompt.replace("{platforms}", ", ".join(platforms) if platforms else "多平台")
        user_prompt = user_prompt.replace("{keywords}", ", ".join(keywords[:20]) if keywords else "无")
        user_prompt = user_prompt.replace("{news_content}", news_content)
        user_prompt = user_prompt.replace("{rss_content}", rss_content)
        user_prompt = user_prompt.replace("{language}", analyzer.language)

        # 构建独立展示区内容 (暂时为空，因为 BriefingService 暂不处理 standalone)
        user_prompt = user_prompt.replace("{standalone_content}", "")
        
        return user_prompt
