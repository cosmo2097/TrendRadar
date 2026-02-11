# coding=utf-8
"""
Briefing Server 数据处理模块
"""
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import re

def get_titles_by_date_range(
    storage_manager,
    start_date: str,
    end_date: str,
    current_platform_ids: Optional[List[str]] = None,
    query: Optional[str] = None,
    include_regex: Optional[str] = None,
    quiet: bool = False,
) -> Tuple[Dict, Dict, Dict]:
    """
    获取指定日期范围内的所有新闻标题
    
    :param query: 必须包含的搜索词 (AND)
    :param include_regex: 必须匹配的正则表达式 (AND logic with query)
    """
    aggregated_results = {}  # {source_id: {title: {ranks: [], ...}}}
    aggregated_title_info = {} # {source_id: {title: {...}}}
    final_id_to_name = {}

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            if not quiet:
                print(f"[Briefing] Processing {date_str}...")
            
            try:
                # 获取当天原始数据
                news_data = storage_manager.get_today_all_data(date_str)
                if not news_data or not news_data.items:
                    current += timedelta(days=1)
                    continue

                # Merge platform names
                final_id_to_name.update(news_data.id_to_name)

                for source_id, news_list in news_data.items.items():
                    if current_platform_ids is not None and source_id not in current_platform_ids:
                        continue

                    if source_id not in aggregated_results:
                        aggregated_results[source_id] = {}
                        aggregated_title_info[source_id] = {}

                    for item in news_list:
                        title = item.title
                        
                        # 关键词过滤 (Query: AND)
                        if query:
                            # 简单的大小写不敏感匹配
                            if query.lower() not in title.lower():
                                continue
                                
                        # 正则过滤 (Include Regex: AND)
                        if include_regex:
                            if not re.search(include_regex, title, re.IGNORECASE):
                                continue
                        
                        # 聚合逻辑
                        if title in aggregated_title_info[source_id]:
                            # 已存在，合并数据
                            existing = aggregated_title_info[source_id][title]
                            
                            # 合并 ranks
                            new_ranks = item.ranks or [item.rank]
                            existing["ranks"].extend([r for r in new_ranks if r not in existing["ranks"]])
                            
                            # 更新时间
                            item_first = item.first_time or item.crawl_time
                            item_last = item.last_time or item.crawl_time
                            if item_first < existing["first_time"]:
                                existing["first_time"] = item_first
                            if item_last > existing["last_time"]:
                                existing["last_time"] = item_last
                                
                            # 累加 count
                            existing["count"] += item.count
                            
                            # 合并 rank_timeline
                            if item.rank_timeline:
                                existing["rank_timeline"].extend(item.rank_timeline)
                                
                        else:
                            # 新增
                            ranks = item.ranks or [item.rank]
                            info = {
                                "first_time": item.first_time or item.crawl_time,
                                "last_time": item.last_time or item.crawl_time,
                                "count": item.count,
                                "ranks": ranks,
                                "url": item.url or "",
                                "mobileUrl": item.mobile_url or "",
                                "rank_timeline": item.rank_timeline or [],
                            }
                            aggregated_title_info[source_id][title] = info
                            # results 结构只包含简要信息用于展示
                            aggregated_results[source_id][title] = {
                                "ranks": ranks,
                                "url": info["url"],
                                "mobileUrl": info["mobileUrl"],
                            }

            except Exception as e:
                # 某天数据读取失败不应中断整个流程
                print(f"[Briefing] Error reading data for {date_str}: {e}")

            current += timedelta(days=1)
            
        return aggregated_results, final_id_to_name, aggregated_title_info
        
    except Exception as e:
        print(f"[Briefing] Date processing error: {e}")
        return {}, {}, {}


def get_rss_by_date_range(
    storage_manager,
    start_date: str,
    end_date: str,
    allowed_feed_ids: Optional[List[str]] = None,
    query: Optional[str] = None,
    include_regex: Optional[str] = None,
    quiet: bool = False,
) -> List[Dict]:
    """
    读取指定日期范围内的所有 RSS 条目
    """
    rss_items = []
    
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            # quiet check can be here but logging is fine for now
            
            try:
                rss_data = storage_manager.get_rss_data(date_str)
                if rss_data and rss_data.items:
                    for feed_id, items in rss_data.items.items():
                        if allowed_feed_ids and feed_id not in allowed_feed_ids:
                            continue
                            
                        for item in items:
                            # 关键词过滤 (Query: AND)
                            if query:
                                if query.lower() not in item.title.lower():
                                    continue
                                    
                            # 正则过滤 (Include Regex: AND)
                            if include_regex:
                                if not re.search(include_regex, item.title, re.IGNORECASE):
                                    continue
                                    
                            rss_items.append({
                                "title": item.title,
                                "url": item.url,
                                "feed_name": item.feed_name or feed_id,
                                "feed_id": feed_id,
                                "published_at": item.published_at,
                                "summary": item.summary
                            })
            except Exception as e:
                pass # Ignore errors for missing days
                
            current += timedelta(days=1)
            
        return rss_items
        
    except Exception as e:
        print(f"[Briefing] RSS Date processing error: {e}")
        return []
