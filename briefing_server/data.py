# coding=utf-8
"""
Briefing Server 数据处理模块
"""
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

def get_titles_by_date_range(
    storage_manager,
    start_date: str,
    end_date: str,
    current_platform_ids: Optional[List[str]] = None,
    quiet: bool = False,
) -> Tuple[Dict, Dict, Dict]:
    """
    读取指定日期范围内的所有标题，并进行聚合

    Args:
        storage_manager: 存储管理器实例
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        current_platform_ids: 过滤平台ID
        quiet: 是否静默

    Returns:
        Tuple[Dict, Dict, Dict]: (aggregated_results, id_to_name, title_info)
    """
    aggregated_results = {}
    final_id_to_name = {}
    aggregated_title_info = {}
    
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            if not quiet:
                print(f"[Briefing] Reading data for {date_str}...")
            
            try:
                # 获取当天原始数据
                news_data = storage_manager.get_today_all_data(date_str)
                if not news_data or not news_data.items:
                    current += timedelta(days=1)
                    continue

                for source_id, news_list in news_data.items.items():
                    if current_platform_ids is not None and source_id not in current_platform_ids:
                        continue

                    source_name = news_data.id_to_name.get(source_id, source_id)
                    final_id_to_name[source_id] = source_name

                    if source_id not in aggregated_results:
                        aggregated_results[source_id] = {}
                        aggregated_title_info[source_id] = {}

                    for item in news_list:
                        title = item.title
                        
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
