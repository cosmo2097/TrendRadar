# coding=utf-8
"""
TrendRadar API 服务层
提供 BriefingService 用于生成 AI 简报
"""
import asyncio
import json
import logging
import re
import sqlite3
from pathlib import Path
from difflib import SequenceMatcher
from typing import List, Dict, Optional, Any, Tuple, Union
from datetime import datetime

from trendradar.core import load_config
from trendradar.context import AppContext
from trendradar.crawler import DataFetcher
from trendradar.crawler.rss import RSSFetcher, RSSFeedConfig
# from trendradar.core.frequency import parse_frequency_rules # Removed
from briefing_server.utils import parse_frequency_rules # Use local util
from briefing_server.data import get_titles_by_date_range, get_rss_by_date_range # Import local data agg
from trendradar.core.frequency import load_frequency_words, matches_word_groups # Import frequency loader
from trendradar.ai import AIAnalyzer
from trendradar.core.analyzer import count_rss_frequency
from datetime import timedelta
from litellm import completion # Import litellm directly for streaming
from trendradar.utils.time import is_within_days, DEFAULT_TIMEZONE

try:
    # 优先复用 MCP 的日期解析能力；独立部署 briefing_server 时该模块可能不存在。
    from mcp_server.utils.validators import validate_date_range as _mcp_validate_date_range
except ImportError:
    _mcp_validate_date_range = None


# 配置日志
logger = logging.getLogger("trendradar.api")


def _validate_date_range_compat(date_range: Optional[Union[Dict[str, str], str]]) -> Optional[Tuple[datetime, datetime]]:
    """兼容版日期范围校验：优先 MCP，缺失时使用本地轻量解析。"""
    if _mcp_validate_date_range is not None:
        return _mcp_validate_date_range(date_range)

    if date_range is None:
        return None

    if isinstance(date_range, dict):
        start_str = str(date_range.get("start", "")).strip()
        end_str = str(date_range.get("end", "")).strip()
        if not start_str or not end_str:
            raise ValueError("date_range must contain start and end")
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        end_dt = datetime.strptime(end_str, "%Y-%m-%d")
        if start_dt > end_dt:
            raise ValueError("start date cannot be later than end date")
        return start_dt, end_dt

    if isinstance(date_range, str):
        raw = date_range.strip()
        if not raw:
            raise ValueError("date_range cannot be empty")

        if raw.startswith("{") and raw.endswith("}"):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as err:
                raise ValueError(f"invalid date_range JSON: {err}") from err
            if not isinstance(parsed, dict):
                raise ValueError("date_range JSON must be an object")
            return _validate_date_range_compat(parsed)

        today = datetime.now().date()
        normalized = raw.lower()
        if normalized in {"today", "今天"}:
            dt = datetime.combine(today, datetime.min.time())
            return dt, dt
        if normalized in {"yesterday", "昨天"}:
            dt = datetime.combine(today - timedelta(days=1), datetime.min.time())
            return dt, dt
        if normalized in {"last_week", "最近7天"}:
            end = datetime.combine(today, datetime.min.time())
            start = datetime.combine(today - timedelta(days=6), datetime.min.time())
            return start, end
        if normalized in {"last_month", "最近30天"}:
            end = datetime.combine(today, datetime.min.time())
            start = datetime.combine(today - timedelta(days=29), datetime.min.time())
            return start, end

        single = datetime.strptime(raw, "%Y-%m-%d")
        return single, single

    raise ValueError("date_range must be a dict or string")


class BriefingService:
    """简报服务：负责协调数据抓取、统计和 AI 分析"""

    def __init__(self, config: Optional[Dict] = None):
        """初始化服务，加载配置和单例组件"""
        if config is None:
            config = load_config()
        self.config = config
        self.ctx = AppContext(config)
        self.data_fetcher = DataFetcher(self.ctx.config["DEFAULT_PROXY"] if self.ctx.config["USE_PROXY"] else None)

    def reload_config(self) -> Dict[str, Any]:
        """重新加载配置"""
        logger.info("Reloading configuration...")
        self.config = load_config()
        self.ctx = AppContext(self.config)
        # 重新初始化 data_fetcher
        self.data_fetcher = DataFetcher(self.ctx.config["DEFAULT_PROXY"] if self.ctx.config["USE_PROXY"] else None)
        logger.info("Configuration reloaded successfully")
        return {"status": "success", "message": "Configuration reloaded"}

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
            start_str = start_date.strftime("%Y-%m-%d")
            end_str = end_date.strftime("%Y-%m-%d")
        elif date_range == "daily":
            # 今天 (使用 None 触发 _fetch_all_data 的优化路径)
            start_str = None
            end_str = None
        else:
            # 尝试解析具体日期 (YYYY-MM-DD)
            try:
                target = datetime.strptime(date_range, "%Y-%m-%d")
                start_date = end_date = target
                start_str = start_date.strftime("%Y-%m-%d")
                end_str = end_date.strftime("%Y-%m-%d")
            except ValueError:
                # 可能是自定义范围 "YYYY-MM-DD,YYYY-MM-DD" 或者解析失败
                # 这里我们假设 API 层已经验证了格式，或者 date_range 本身就是一个日期
                # 如果 date_range 不匹配以上关键字，暂且认为是无效或不支持，fallback to daily
                logger.warning(f"Invalid or unsupported date_range: {date_range}, fallback to daily")
                start_str = None
                end_str = None

        
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



        # 4. 补充 published_at 字段 (因 analyzer.py 不可修改，在此处注入)
        # 4.1 Hotlist: 使用 first_time 作为 published_at
        for stat in stats:
            for title_data in stat.get("titles", []):
                title_data["published_at"] = title_data.get("first_time", "")
                
        # 4.2 RSS: 从原始 rss_items 中映射 published_at
        rss_url_map = {item.get("url"): item.get("published_at") for item in rss_items if item.get("url")}
        for stat in rss_stats:
            for title_data in stat.get("titles", []):
                url = title_data.get("url")
                if url and url in rss_url_map:
                    title_data["published_at"] = rss_url_map[url]
                else:
                    title_data["published_at"] = ""

        return {
            "success": True,
            "stats": stats,
            "rss_stats": rss_stats,
            "platforms": list(id_to_name.values())
        }

    async def search_news(
        self,
        query: Optional[str] = None,
        date_range: Optional[Union[Dict[str, str], str]] = None,
        platforms: Optional[List[str]] = None,
        preset: Optional[str] = None,
        search_mode: str = "keyword",
        sort_by: str = "relevance",
        limit: int = 50,
        threshold: float = 0.6,
        include_url: bool = False,
        include_rss: bool = False,
        rss_limit: int = 20,
    ) -> Dict[str, Any]:
        """搜索新闻（参数与输出结构对齐 MCP search_news）。"""
        search_mode = (search_mode or "keyword").lower()
        if search_mode not in {"keyword", "fuzzy", "entity"}:
            return {"success": False, "error": {"code": "INVALID_SEARCH_MODE", "message": "search_mode must be one of: keyword, fuzzy, entity"}}

        sort_by = (sort_by or "relevance").lower()
        if sort_by not in {"relevance", "weight", "date"}:
            return {"success": False, "error": {"code": "INVALID_SORT_BY", "message": "sort_by must be one of: relevance, weight, date"}}

        if not isinstance(limit, int) or limit <= 0 or limit > 1000:
            return {"success": False, "error": {"code": "INVALID_LIMIT", "message": "limit must be an integer between 1 and 1000"}}

        if not isinstance(rss_limit, int) or rss_limit <= 0 or rss_limit > 1000:
            return {"success": False, "error": {"code": "INVALID_RSS_LIMIT", "message": "rss_limit must be an integer between 1 and 1000"}}

        if not isinstance(threshold, (int, float)) or threshold < 0 or threshold > 1:
            return {"success": False, "error": {"code": "INVALID_THRESHOLD", "message": "threshold must be between 0 and 1"}}

        normalized_query = (query or "").strip()
        if not normalized_query and not preset:
            return {"success": False, "error": {"code": "MISSING_QUERY", "message": "query is required when preset is not provided"}}

        preset_group: Optional[Dict[str, Any]] = None
        preset_filter_words: List[Any] = []
        preset_global_filters: List[str] = []
        if preset:
            groups, filter_words, global_filters = load_frequency_words()
            for group in groups:
                if group.get("display_name") == preset:
                    preset_group = group
                    preset_filter_words = filter_words or []
                    preset_global_filters = global_filters or []
                    break
            if not preset_group:
                return {
                    "success": False,
                    "error": {
                        "code": "PRESET_NOT_FOUND",
                        "message": f"Preset '{preset}' not found",
                    },
                }

        if date_range is not None:
            try:
                parsed_date_range = _validate_date_range_compat(date_range)
                if not parsed_date_range:
                    raise ValueError("date_range is invalid")
                start_dt, end_dt = parsed_date_range
                resolved_start = start_dt.strftime("%Y-%m-%d")
                resolved_end = end_dt.strftime("%Y-%m-%d")
            except Exception as err:
                return {"success": False, "error": {"code": "INVALID_DATE_RANGE", "message": str(err)}}
        else:
            try:
                # MCP 默认仅搜索热榜，日期默认使用热榜最新可用日期。
                resolved_start, resolved_end = self._resolve_search_dates(None, None, "hotlist")
            except ValueError as err:
                return {"success": False, "error": {"code": "INVALID_DATE_RANGE", "message": str(err)}}

        # 不做 query 下推，避免提前过滤掉同义词候选（如“人工智能”->“AI”）。
        pushdown_query = None
        all_results, id_to_name, title_info, rss_items = await self._fetch_all_data(
            allowed_sources=platforms,
            start_date=resolved_start,
            end_date=resolved_end,
            query=pushdown_query,
            include_regex=None,
        )

        matches: List[Dict[str, Any]] = []

        for source_id, titles in all_results.items():
            source_name = id_to_name.get(source_id, source_id)
            source_info = title_info.get(source_id, {})
            for title, payload in titles.items():
                matched, score = self._match_title(
                    title, normalized_query, search_mode, float(threshold)
                )
                if not matched:
                    continue
                if preset_group and not matches_word_groups(
                    title,
                    [preset_group],
                    preset_filter_words,
                    preset_global_filters,
                ):
                    continue

                info = source_info.get(title, {})
                ranks = info.get("ranks", payload.get("ranks", [])) or []
                rank = ranks[0] if ranks else 999
                count = max(1, int(info.get("count", len(ranks) or 1)))
                item_date = info.get("last_date") or resolved_end
                published_at = self._normalize_published_at(info.get("last_time"), item_date)
                item = {
                    "title": title,
                    "platform": source_id,
                    "platform_name": source_name,
                    "published_at": published_at,
                    "date": item_date,
                    "ranks": ranks,
                    "count": count,
                    "rank": rank,
                    "summary": "",
                    "similarity_score": round(score, 4),
                    "weight_score": count * 1000 + (1000 - min(rank, 1000)),
                }
                if include_url:
                    item["url"] = info.get("url", "") or info.get("mobileUrl", "")
                    item["mobileUrl"] = info.get("mobileUrl", "")
                matches.append(item)

        if sort_by == "date":
            matches.sort(key=lambda x: self._parse_sort_time(x.get("published_at")), reverse=True)
        elif sort_by == "weight":
            matches.sort(key=lambda x: x.get("weight_score", 0), reverse=True)
        else:
            matches.sort(
                key=lambda x: (
                    x.get("similarity_score", 0),
                    x.get("weight_score", 0),
                    -x.get("rank", 999),
                ),
                reverse=True,
            )

        total_found = len(matches)
        limited = matches[:limit]

        try:
            current = self.ctx.get_time().date()
        except Exception:
            current = datetime.now().date()
        if resolved_start == resolved_end and resolved_start == current.strftime("%Y-%m-%d"):
            time_range_desc = "今天"
        elif resolved_start == resolved_end:
            time_range_desc = resolved_start
        else:
            time_range_desc = f"{resolved_start} 至 {resolved_end}"

        if not matches:
            earliest, latest = self._get_available_date_range("hotlist")
            if earliest and latest:
                available = f"{earliest.strftime('%Y-%m-%d')} 至 {latest.strftime('%Y-%m-%d')}"
                msg = f"未找到匹配的新闻（查询范围: {time_range_desc}，可用数据: {available}）"
            else:
                msg = f"未找到匹配的新闻（{time_range_desc}）"
            return {
                "success": True,
                "results": [],
                "total": 0,
                "query": normalized_query,
                "search_mode": search_mode,
                "time_range": time_range_desc,
                "message": msg,
            }

        result: Dict[str, Any] = {
            "success": True,
            "summary": {
                "description": f"新闻搜索结果（{search_mode}模式）",
                "total_found": total_found,
                "returned": len(limited),
                "requested_limit": limit,
                "search_mode": search_mode,
                "query": normalized_query,
                "platforms": platforms or "所有平台",
                "time_range": time_range_desc,
                "sort_by": sort_by,
            },
            "data": limited,
        }

        if search_mode == "fuzzy":
            result["summary"]["threshold"] = threshold
            if total_found < limit:
                result["note"] = f"模糊搜索模式下，相似度阈值 {threshold} 仅匹配到 {total_found} 条结果"

        if include_rss:
            rss_matches: List[Dict[str, Any]] = []
            for rss in rss_items:
                title = rss.get("title", "")
                matched, score = self._match_title(
                    title, normalized_query, search_mode, float(threshold)
                )
                if not matched:
                    continue
                if preset_group and not matches_word_groups(
                    title,
                    [preset_group],
                    preset_filter_words,
                    preset_global_filters,
                ):
                    continue

                item_date = rss.get("date") or resolved_end
                published_at = self._normalize_published_at(rss.get("published_at", ""), item_date)
                rss_item: Dict[str, Any] = {
                    "title": title,
                    "feed_name": rss.get("feed_name", rss.get("feed_id", "")),
                    "feed_id": rss.get("feed_id", ""),
                    "published_at": published_at,
                    "date": item_date,
                    "summary": rss.get("summary", ""),
                    "similarity_score": round(score, 4),
                }
                if include_url:
                    rss_item["url"] = rss.get("url", "")
                rss_matches.append(rss_item)

            if sort_by == "date":
                rss_matches.sort(key=lambda x: self._parse_sort_time(x.get("published_at")), reverse=True)
            elif sort_by == "relevance":
                rss_matches.sort(key=lambda x: x.get("similarity_score", 0), reverse=True)

            rss_results = rss_matches[:rss_limit]
            result["rss"] = rss_results
            result["rss_total"] = len(rss_matches)
            result["summary"]["include_rss"] = True
            result["summary"]["rss_found"] = len(rss_matches)
            result["summary"]["rss_returned"] = len(rss_results)

        return result

    def _match_title(
        self,
        title: str,
        query: str,
        search_mode: str,
        threshold: float,
    ) -> Tuple[bool, float]:
        if not query:
            return True, 1.0

        title_lower = title.lower()
        query_lower = query.lower()
        terms = self._expand_query_terms(query_lower)

        if search_mode == "keyword":
            if query_lower in title_lower:
                return True, 1.0
            for term in terms:
                if term in title_lower:
                    return True, 0.95
            return False, 0.0

        if search_mode == "fuzzy":
            score = max(SequenceMatcher(None, term, title_lower).ratio() for term in terms)
            return score >= threshold, score

        # entity 模式: 先用子串匹配，其次按词项命中率估算相关度
        for term in terms:
            if term in title_lower:
                return True, 1.0

        tokens = []
        for term in terms:
            tokens.extend([token for token in re.split(r"\s+", term) if token])
        if not tokens:
            return False, 0.0

        hit = sum(1 for token in tokens if token in title_lower)
        score = hit / len(tokens)
        return score > 0, score

    def _expand_query_terms(self, query_lower: str) -> List[str]:
        terms = {query_lower}
        # 支持在 query 内直接传多个关键词：空格、逗号、顿号、分号、斜杠、竖线。
        split_terms = [t.strip().lower() for t in re.split(r"[\s,，、;；/|｜]+", query_lower) if t and t.strip()]
        terms.update(split_terms)
        return [t.strip().lower() for t in terms if t and t.strip()]

    def _parse_sort_time(self, value: Optional[str]) -> datetime:
        if not value:
            return datetime.min
        try:
            from dateutil import parser as date_parser

            dt = date_parser.parse(value)
            if dt.tzinfo is not None:
                return dt.astimezone(self.ctx.get_time().tzinfo).replace(tzinfo=None)
            return dt
        except Exception:
            try:
                return datetime.strptime(value, "%Y-%m-%d")
            except Exception:
                return datetime.min

    def _normalize_published_at(self, value: Optional[str], data_date: str) -> str:
        """将 published_at 统一为 YYYY-MM-DDTHH:MM:SS。"""
        v = str(value or "").strip()
        if not v:
            return f"{data_date}T00:00:00"

        # 已带完整日期前缀（例如 2026-04-22T12:30:00 / 2026-04-22 12:30:00）
        m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?", v)
        if m:
            sec = m.group(4) or "00"
            return f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{sec}"

        # 仅日期
        m = re.match(r"^(\d{4}-\d{2}-\d{2})$", v)
        if m:
            return f"{m.group(1)}T00:00:00"

        # 热榜常见时间格式：HH-MM / HH:MM
        m = re.match(r"^(\d{1,2})[-:](\d{2})$", v)
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{data_date}T{hh:02d}:{mm:02d}:00"

        # 兜底解析（只在字符串中含年份时启用）
        if re.search(r"\b\d{4}\b", v):
            try:
                from dateutil import parser as date_parser

                dt = date_parser.parse(v)
                return dt.strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                pass

        return f"{data_date}T00:00:00"

    def _available_dates(self, db_type: str) -> List[datetime]:
        base = Path(__file__).resolve().parent.parent / "output" / db_type
        if not base.exists():
            return []
        table_name = "news_items" if db_type == "news" else "rss_items"
        dates: List[datetime] = []
        for file in base.glob("*.db"):
            try:
                file_date = datetime.strptime(file.stem, "%Y-%m-%d")
            except ValueError:
                continue
            # 跳过“空库”日期，避免默认查询落到无数据的最新日期。
            try:
                with sqlite3.connect(str(file)) as conn:
                    cursor = conn.execute(f"SELECT 1 FROM {table_name} LIMIT 1")
                    if cursor.fetchone() is None:
                        continue
            except Exception:
                continue
            dates.append(file_date)
        dates.sort()
        return dates

    def _get_available_date_range(self, source_type: str = "all") -> Tuple[Optional[datetime], Optional[datetime]]:
        source_type = (source_type or "all").lower()
        if source_type == "hotlist":
            dates = self._available_dates("news")
        elif source_type == "rss":
            dates = self._available_dates("rss")
        else:
            dates = sorted(self._available_dates("news") + self._available_dates("rss"))

        if not dates:
            return None, None
        return dates[0], dates[-1]

    def _resolve_search_dates(self, start_date: Optional[str], end_date: Optional[str], source_type: str) -> Tuple[str, str]:
        now_date = self.ctx.get_time().date()
        earliest, latest = self._get_available_date_range(source_type)

        if not start_date and not end_date:
            if latest is None:
                raise ValueError("output 目录下没有可用的新闻数据")
            target = latest.strftime("%Y-%m-%d")
            return target, target

        if start_date and not end_date:
            end_date = start_date
        elif end_date and not start_date:
            start_date = end_date

        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        except Exception:
            raise ValueError("日期格式错误，请使用 YYYY-MM-DD")

        if start_dt > end_dt:
            raise ValueError("开始日期不能晚于结束日期")

        if start_dt.date() > now_date or end_dt.date() > now_date:
            raise ValueError(f"不允许查询未来日期（当前日期: {now_date.strftime('%Y-%m-%d')}）")

        return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")

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
        query: str = None,
        include_regex: str = None,
    ) -> Tuple[Dict, Dict, Dict, List[Dict]]:
        """
        获取所有数据（新闻 + RSS），支持并行抓取自定义 RSS
        """
        # 限制查询范围最大为 7 天
        if start_date and end_date:
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                # 7天跨度: end - start = 6 days difference (inclusive count = 7)
                if (end_dt - start_dt).days > 6:
                    logger.warning(f"Date range too large ({start_date} to {end_date}), clamping to last 7 days")
                    start_dt = end_dt - timedelta(days=6)
                    start_date = start_dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # 1. 定义获取本地新闻的任务
        async def _fetch_local_news():
            if start_date and end_date:
                return await asyncio.to_thread(
                    get_titles_by_date_range,
                    self.ctx.get_storage_manager(),
                    start_date,
                    end_date,
                    allowed_sources,
                    query,  # query param
                    include_regex, # include_regex param
                    True    # quiet
                )
            else:
                # read_today_titles 不支持 query，暂时只在 range 模式支持搜索
                # 如果 query 存在但没有 range，应该报错或者默认 range?
                # search_news 接口会强制要求 range，所以这里安全
                return await asyncio.to_thread(
                    self.ctx.read_today_titles,
                    allowed_sources,
                    True
                )

        # 2. 定义获取本地 RSS 的任务
        async def _fetch_local_rss():
            storage = self.ctx.get_storage_manager()
            
            if start_date and end_date:
                # 使用范围查询
                items = await asyncio.to_thread(
                    get_rss_by_date_range,
                    storage,
                    start_date,
                    end_date,
                    allowed_sources, # allowed_feed_ids
                    query,  # query param
                    include_regex, # include_regex param
                    True    # quiet
                )
                # 应用新鲜度过滤
                return self._apply_freshness_filter(items, start_date, end_date)
            else:
                # 仅读取今天 (兼容旧逻辑)
                today = self.ctx.format_date()
                rss_data = await asyncio.to_thread(storage.get_rss_data, today)
                
                items = []
                if rss_data and rss_data.items:
                    for feed_id, feed_items in rss_data.items.items():
                        if allowed_sources and feed_id not in allowed_sources:
                            continue
                            
                        for item in feed_items:
                            # 简单的 query 过滤
                            if query and query.lower() not in item.title.lower():
                                continue

                            # 正则过滤
                            if include_regex:
                                import re
                                if not re.search(include_regex, item.title, re.IGNORECASE):
                                    continue
                                
                            items.append({
                                 "title": item.title,
                                 "url": item.url,
                                 "feed_name": item.feed_name or feed_id,
                                 "feed_id": feed_id,
                                 "date": today,
                                 "published_at": item.published_at,
                                 "summary": item.summary
                            })
                # 应用新鲜度过滤
                return self._apply_freshness_filter(items)



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

    def _apply_freshness_filter(self, items: List[Dict], start_date: str = None, end_date: str = None) -> List[Dict]:
        """
        应用新鲜度/时间范围过滤
        
        策略:
        1. 如果提供了 start_date (即 Range Query):
           - 忽略配置中的 max_age_days
           - 严格按照 [start_date, end_date] 范围过滤 published_at
           - 如果 published_at 为空，则保留（假设 crawl_time 符合要求）
           
        2. 如果未提供 start_date (即 Daily/Current):
           - 使用配置中的 max_age_days 进行相对时间过滤 (IsFresh)
        """
        # 1. 范围过滤模式
        if start_date:
            try:
                # 构造时间范围 (从 start_date 00:00 到 end_date 23:59:59)
                # 注意：这里做简单的字符串比较或转换，需注意时区。
                # 简单起见，我们解析为 datetime 进行比较
                from dateutil import parser
                import pytz
                
                tz = pytz.timezone(self.ctx.config.get("TIMEZONE", DEFAULT_TIMEZONE))
                
                # 解析开始时间
                s_dt = datetime.strptime(start_date, "%Y-%m-%d")
                s_dt = tz.localize(s_dt)
                
                # 解析结束时间 (默认为开始时间，如果未提供)
                e_str = end_date if end_date else start_date
                e_dt = datetime.strptime(e_str, "%Y-%m-%d")
                e_dt = tz.localize(e_dt) + timedelta(days=1) # +1天作为开区间上限，或包含当天的23:59
                
                filtered_items = []
                for item in items:
                    p_at = item.get("published_at")
                    if not p_at:
                        # 无发布时间，保留 (依赖 crawl_time 筛选)
                        filtered_items.append(item)
                        continue
                        
                    try:
                        p_dt = parser.parse(p_at)
                        if p_dt.tzinfo is None:
                            p_dt = tz.localize(p_dt)
                        else:
                            p_dt = p_dt.astimezone(tz)
                            
                        # 比较范围: start <= pub < end+1d
                        if s_dt <= p_dt < e_dt:
                            filtered_items.append(item)
                    except Exception:
                        # 解析失败，保留
                        filtered_items.append(item)
                
                return filtered_items
                
            except Exception as e:
                logger.error(f"Date range filter error: {e}")
                return items

        # 2. 相对新鲜度模式 (Daily/Current)
        rss_config = self.ctx.rss_config
        freshness_config = rss_config.get("FRESHNESS_FILTER", {})
        freshness_enabled = freshness_config.get("ENABLED", True)
        default_max_age_days = freshness_config.get("MAX_AGE_DAYS", 3)
        timezone = self.ctx.config.get("TIMEZONE", DEFAULT_TIMEZONE)

        if not freshness_enabled:
            return items

        # 构建 feed_id -> max_age_days 的映射
        feed_max_age_map = {}
        for feed_cfg in self.ctx.rss_feeds:
            feed_id = feed_cfg.get("id", "")
            max_age = feed_cfg.get("max_age_days")
            if max_age is not None:
                try:
                    feed_max_age_map[feed_id] = int(max_age)
                except (ValueError, TypeError):
                    pass
        
        filtered_items = []
        for item in items:
            feed_id = item.get("feed_id", "")
            # 确定此 feed 的 max_age_days
            max_days = feed_max_age_map.get(feed_id)
            if max_days is None:
                max_days = default_max_age_days
            
            # 新鲜度过滤
            if max_days > 0:
                published_at = item.get("published_at")
                if published_at and not is_within_days(published_at, max_days, timezone):
                    continue
            
            filtered_items.append(item)
            
        return filtered_items



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
            # 兜底：如果上游移除了私有方法，退化为非流式分析后一次性输出
            logger.warning("AIAnalyzer._prepare_news_content not found, fallback to non-stream analyze")
            result = await asyncio.to_thread(
                analyzer.analyze,
                stats,
                rss_stats,
                date_range,
                "定制简报",
                platforms,
                keywords,
            )
            content = (result.core_trends or "").strip()
            if content:
                yield content
            elif getattr(result, "skipped", False):
                yield "今日无相关动态。"
            elif result.error:
                yield f"[AI 生成失败: {result.error}]"
            else:
                yield "今日无相关动态。"
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
