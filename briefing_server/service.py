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
from trendradar.ai import AIAnalyzer, AIAnalysisResult
from trendradar.core.analyzer import convert_keyword_stats_to_platform_stats, count_rss_frequency

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
    ) -> Dict[str, Any]:
        """
        生成简报（主入口）
        """
        logger.info(f"开始生成简报, 规则数: {len(rules)}")

        # 1. 解析规则 (内存操作)
        word_groups, filter_words, global_filters = parse_frequency_rules(rules)

        # 2. 并行获取数据 (本地缓存 + 实时 RSS)
        all_results, id_to_name, title_info, rss_items = await self._fetch_all_data(
            allowed_sources, custom_rss_urls
        )

        # 3. 统计逻辑 (复用 core 逻辑)
        stats, matched_count = self.ctx.count_frequency(
            all_results,
            word_groups,
            filter_words,
            id_to_name,
            title_info,
            new_titles=None,
            mode="daily",
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

        # 4. 准备 AI 分析
        if not matched_count and not rss_stats:
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

        platforms = list(id_to_name.values())
        keywords = [s["word"] for s in stats]

        # 初始化 AI 分析器
        ai_config = self.config.get("AI", {}).copy()
        if ai_model:
            ai_config["MODEL"] = ai_model
        
        analysis_config = self.config.get("AI_ANALYSIS", {})
        analyzer = AIAnalyzer(ai_config, analysis_config, self.ctx.get_time, debug=False)

        # 5. 执行 AI 分析
        if stream_ai:
            return self._stream_analysis(analyzer, stats, rss_stats, platforms, keywords)
        else:
            # 普通返回
            result = await asyncio.to_thread(
                analyzer.analyze,
                stats,
                rss_stats,
                "daily",
                "定制简报",
                platforms,
                keywords
            )
            
            return {
                "success": result.success,
                "markdown": result.core_trends, # 注意：analyze 返回的是 AIAnalysisResult 对象，其 content 字段可能叫 core_trends
                "stats": stats,
                "rss_stats": rss_stats,
                "error": result.error,
                "usage": None # AIAnalysisResult 中好像没有 usage 字段，暂忽略
            }

    async def _fetch_all_data(
        self, 
        allowed_sources: Optional[List[str]], 
        custom_rss_urls: Optional[List[str]]
    ) -> Tuple[Dict, Dict, Dict, List[Dict]]:
        """并行获取本地缓存数据和实时抓取自定义 RSS"""
        local_task = asyncio.to_thread(self._read_local_data, allowed_sources)
        rss_task = self._fetch_custom_rss(custom_rss_urls) if custom_rss_urls else asyncio.sleep(0)
        local_res, rss_res = await asyncio.gather(local_task, rss_task)
        all_results, id_to_name, title_info, local_rss = local_res
        final_rss_items = local_rss + (rss_res if isinstance(rss_res, list) else [])
        return all_results, id_to_name, title_info, final_rss_items

    def _read_local_data(self, allowed_sources: Optional[List[str]]) -> Tuple[Dict, Dict, Dict, List[Dict]]:
        """读取本地缓存并筛选"""
        all_results, id_to_name, title_info = self.ctx.read_today_titles(
            platform_ids=None,
            quiet=True
        )
        
        # 本地 RSS
        storage = self.ctx.get_storage_manager()
        today = self.ctx.format_date()
        local_rss_data = storage.get_rss_data(today)
        local_rss_items = []
        
        if local_rss_data and local_rss_data.items:
             for feed_id, items in local_rss_data.items.items():
                 if allowed_sources and feed_id not in allowed_sources:
                     continue
                 for item in items:
                     local_rss_items.append({
                         "title": item.title,
                         "url": item.url,
                         "feed_name": item.feed_name,
                         "feed_id": item.feed_id,
                         "published_at": item.published_at,
                         "summary": item.summary
                     })

        final_results = {}
        final_id_to_name = {}
        final_title_info = {}
        
        if all_results:
            for pid, data in all_results.items():
                if allowed_sources and pid not in allowed_sources:
                    continue
                final_results[pid] = data
                final_id_to_name[pid] = id_to_name.get(pid, pid)
                if pid in title_info:
                    final_title_info[pid] = title_info[pid]

        return final_results, final_id_to_name, final_title_info, local_rss_items

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

    async def _stream_analysis(self, analyzer: AIAnalyzer, stats, rss_stats, platforms, keywords):
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
            "daily",
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
            response = await asyncio.to_thread(
                analyzer.client.completion,
                messages=messages,
                stream=True
            )
            
            for chunk in response:
                if chunk and chunk.choices:
                    content = chunk.choices[0].delta.content
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
