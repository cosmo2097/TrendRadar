from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse


ROOT = Path(__file__).resolve().parents[2]
HTML_PATH = Path(__file__).resolve().with_name("hotspot-tracker-demo.html")
OPS_HTML_PATH = Path(__file__).resolve().with_name("hotspot-ops-board.html")
WATCH_HTML_PATH = Path(__file__).resolve().with_name("hotspot-watch-prototype.html")
DEFAULT_MCP_URL = "http://127.0.0.1:3333/mcp"
MCP_URL = os.getenv("TRENDPULSE_MCP_URL") or os.getenv("MCP_URL") or DEFAULT_MCP_URL

app = FastAPI(title="TrendPulse Prototype Service")


def _date_range(days: int) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    end_day = date.today()
    start_day = end_day - timedelta(days=max(days - 1, 0))
    span = [start_day + timedelta(days=offset) for offset in range((end_day - start_day).days + 1)]
    midpoint = max(1, len(span) // 2)
    period1 = {"start": span[0].isoformat(), "end": span[midpoint - 1].isoformat()}
    period2 = {"start": span[midpoint].isoformat(), "end": span[-1].isoformat()} if midpoint < len(span) else period1
    full = {"start": start_day.isoformat(), "end": end_day.isoformat()}
    return full, period1, period2


def _run_mcporter_sync(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="mcporter_", suffix=".json", delete=False) as handle:
            temp_path = handle.name

        cmd = [
            "npx",
            "-y",
            "mcporter",
            "call",
            "--http-url",
            MCP_URL,
            "--allow-http",
            "--output",
            "json",
            tool_name,
            "--args",
            json.dumps(args, ensure_ascii=False),
        ]

        with open(temp_path, "w", encoding="utf-8") as stdout_handle:
            completed = subprocess.run(
                cmd,
                cwd=str(ROOT),
                stdout=stdout_handle,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()
            raise HTTPException(status_code=502, detail=f"{tool_name} 调用失败: {detail}")

        stdout_text = Path(temp_path).read_text(encoding="utf-8", errors="ignore")
        outer = json.loads(stdout_text)
        inner_raw = outer.get("result")
        if not isinstance(inner_raw, str):
            raise HTTPException(status_code=502, detail=f"{tool_name} 返回结构异常")

        inner = json.loads(inner_raw)
        if inner.get("success") is False:
            error = inner.get("error", {})
            raise HTTPException(status_code=502, detail=f"{tool_name} 业务失败: {error.get('message', 'unknown error')}")
        return inner
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


async def _run_tool(tool_name: str, **kwargs: Any) -> dict[str, Any]:
    return await asyncio.to_thread(_run_mcporter_sync, tool_name, kwargs)


def _build_trend_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload.get("data", []):
        sample_titles = item.get("sample_titles", [])
        lead_title = sample_titles[0] if sample_titles else "当天没有代表性标题"
        note = "同日热点样本：" + " / ".join(sample_titles[:3]) if sample_titles else "这一天没有返回可展示的热点样本。"
        rows.append(
            {
                "date": item.get("date", ""),
                "count": int(item.get("count", 0)),
                "title": lead_title,
                "note": note,
            }
        )
    return rows


def _build_platform_rows(insights: dict[str, Any], search: dict[str, Any]) -> list[dict[str, Any]]:
    first_titles: dict[str, dict[str, Any]] = {}
    for item in search.get("data", []):
        platform_name = item.get("platform_name", item.get("platform", "未知平台"))
        first_titles.setdefault(platform_name, item)

    platform_stats = insights.get("platform_stats", {})
    rows = []
    for platform_name, stats in platform_stats.items():
        title_item = first_titles.get(platform_name, {})
        top_keywords = [kw.get("keyword", "") for kw in stats.get("top_keywords", [])[:2] if kw.get("keyword")]
        angle = f"特征词：{' / '.join(top_keywords)}" if top_keywords else "这个平台当前没有可提炼的高频特征词。"
        rows.append(
            {
                "name": platform_name,
                "mention": int(stats.get("topic_mentions", 0)),
                "coverage": round(float(stats.get("coverage_rate", 0.0)), 2),
                "hotspot": title_item.get("title", "当前没有对应的关键词热点标题"),
                "angle": angle,
            }
        )

    rows.sort(key=lambda item: (-item["mention"], -item["coverage"], item["name"]))
    return rows[:6]


def _build_event_rows(search: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in search.get("data", []):
        title = item.get("title", "").strip()
        if not title:
            continue
        entry = grouped.setdefault(
            title,
            {
                "title": title,
                "platforms": set(),
                "dates": set(),
                "best_rank": 999,
                "total_count": 0,
            },
        )
        entry["platforms"].add(item.get("platform_name", item.get("platform", "未知平台")))
        entry["dates"].add(item.get("date", ""))
        entry["best_rank"] = min(entry["best_rank"], int(item.get("rank", 999) or 999))
        entry["total_count"] += int(item.get("count", 1) or 1)

    events = []
    for item in grouped.values():
        sorted_dates = sorted(filter(None, item["dates"]))
        if sorted_dates:
            dates = sorted_dates[0] if len(sorted_dates) == 1 else f"{sorted_dates[0]} ~ {sorted_dates[-1]}"
        else:
            dates = "未知日期"
        platforms = sorted(item["platforms"])
        events.append(
            {
                "title": item["title"],
                "cross": len(platforms) > 1,
                "bestRank": item["best_rank"] if item["best_rank"] != 999 else 0,
                "totalCount": item["total_count"],
                "dates": dates,
                "platforms": platforms,
            }
        )

    events.sort(key=lambda item: (-len(item["platforms"]), item["bestRank"] or 999, -item["totalCount"]))
    return events[:6]


def _build_aggregate_rows(aggregate: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in aggregate.get("data", [])[:8]:
        platforms = item.get("platforms", [])
        dates = sorted(item.get("dates", []))
        date_label = dates[0] if len(dates) == 1 else f"{dates[0]} ~ {dates[-1]}" if dates else "未知日期"
        rows.append(
            {
                "title": item.get("representative_title", "未知事件"),
                "platformCount": int(item.get("platform_count", len(platforms))),
                "platforms": platforms,
                "bestRank": int(item.get("best_rank", 0) or 0),
                "totalCount": int(item.get("total_count", 0) or 0),
                "dates": date_label,
                "isCrossPlatform": bool(item.get("is_cross_platform", False)),
            }
        )
    return rows


def _parse_platforms(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    parts = [item.strip() for item in raw.split(",")]
    cleaned = [item for item in parts if item]
    return cleaned or None


def _build_date_range(start: str | None, end: str | None) -> dict[str, str] | None:
    if start and end:
        return {"start": start, "end": end}
    if start:
        return {"start": start, "end": start}
    if end:
        return {"start": end, "end": end}
    return None


def _extract_link(item: dict[str, Any]) -> str:
    return str(item.get("url") or item.get("mobileUrl") or "")


def _build_library_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        rows.append(
            {
                "title": item.get("title", "未命名热点"),
                "platform": item.get("platform", ""),
                "platform_name": item.get("platform_name", item.get("platform", "未知信源")),
                "date": item.get("date", ""),
                "timestamp": item.get("timestamp", ""),
                "rank": int(item.get("rank", 0) or 0),
                "count": int(item.get("count", 0) or 0),
                "ranks": [int(rank) for rank in item.get("ranks", []) if int(rank or 0) > 0],
                "url": _extract_link(item),
            }
        )
    return rows


def _annotate_library_trends(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows

    for row in rows:
        ranks = row.get("ranks", [])
        sample_count = int(row.get("count", 0) or 0)
        if not ranks:
            rank = int(row.get("rank", 0) or 0)
            ranks = [rank] if rank > 0 else []
        row["rank_history"] = [
            {"step": index + 1, "rank": rank}
            for index, rank in enumerate(ranks)
        ]

        if len(ranks) <= 1:
            if sample_count <= 1:
                row["trend_direction"] = "new"
                row["trend_label"] = "新上榜"
                row["trend_delta"] = None
            else:
                row["trend_direction"] = "flat"
                row["trend_label"] = "样本不足"
                row["trend_delta"] = None
        else:
            first_rank = int(ranks[0] or 0)
            last_rank = int(ranks[-1] or 0)
            if first_rank <= 0 or last_rank <= 0:
                row["trend_direction"] = "flat"
                row["trend_label"] = "持平"
                row["trend_delta"] = None
            else:
                delta = first_rank - last_rank
                row["trend_delta"] = delta
                if delta > 0:
                    row["trend_direction"] = "up"
                    row["trend_label"] = "上升"
                elif delta < 0:
                    row["trend_direction"] = "down"
                    row["trend_label"] = "下降"
                else:
                    row["trend_direction"] = "flat"
                    row["trend_label"] = "持平"

        row["is_new"] = row.get("trend_direction") == "new"
        row["history_points"] = len(ranks)

    return rows


def _aggregate_primary_url(item: dict[str, Any]) -> str:
    urls = item.get("urls", [])
    if isinstance(urls, list) and urls:
        first = urls[0] or {}
        return str(first.get("url") or first.get("mobileUrl") or "")
    return ""


def _filter_aggregate_rows(rows: list[dict[str, Any]], keyword: str) -> list[dict[str, Any]]:
    if not keyword:
        return rows
    lowered = keyword.lower()
    return [
        item
        for item in rows
        if lowered in str(item.get("representative_title", "")).lower()
    ]


def _build_focus_cards_from_aggregate(
    rows: list[dict[str, Any]],
    mode: str,
    top_n: int,
    summary: dict[str, Any],
    keyword: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    filtered_rows = _filter_aggregate_rows(rows, keyword)
    cross_platform_rows = [
        item
        for item in (filtered_rows or rows)
        if int(item.get("platform_count", len(item.get("platforms", []))) or 0) > 1
    ]
    source_rows = cross_platform_rows

    sortable_rows = sorted(
        source_rows,
        key=lambda item: (
            -float(item.get("aggregate_weight", 0) or 0),
            -int(item.get("platform_count", 0) or 0),
            int(item.get("best_rank", 999) or 999),
            -int(item.get("total_count", 0) or 0),
            str(item.get("representative_title", "")),
        ),
    )

    cards: list[dict[str, Any]] = []
    for item in sortable_rows[:top_n]:
        rank = int(item.get("best_rank", 0) or 0)
        platform_count = int(item.get("platform_count", len(item.get("platforms", []))) or 0)
        platforms = item.get("platforms", [])
        headline = item.get("representative_title", "未命名热点事件")
        cards.append(
            {
                "keyword": keyword or ("跨平台传播" if platform_count > 1 else "单平台热点"),
                "topic_count": int(item.get("total_count", 0) or 0),
                "rank": rank,
                "headline": headline,
                "platform_name": " / ".join(platforms[:3]) if platforms else "热点库",
                "url": _aggregate_primary_url(item),
                "reason": (
                    f"{'当前视角' if mode == 'current' else '今日累计'}下，这个事件的聚合权重为 "
                    f"{float(item.get('aggregate_weight', 0) or 0):.1f}，覆盖 {platform_count} 个平台，"
                    f"传播强度 {int(item.get('total_count', 0) or 0)}"
                    f"{f'，最佳排名 #{rank}' if rank else ''}。"
                ),
                "platform_count": platform_count,
                "dates": item.get("dates", []),
                "aggregate_weight": round(float(item.get("aggregate_weight", 0) or 0), 2),
            }
        )

    total_rows = 0
    for key in ("aggregated_count", "total", "returned"):
        if summary.get(key) is not None:
            total_rows = int(summary.get(key, 0) or 0)
            if total_rows:
                break

    snapshot = {
        "mode": mode,
        "latest_total": int(summary.get("original_count", total_rows) or total_rows),
        "latest_returned": len(source_rows),
        "trending_keywords": len(cards),
        "aggregated_events": total_rows or len(source_rows),
        "cross_platform_events": len(source_rows),
    }
    return snapshot, cards


def _build_ops_alerts(
    keyword: str,
    trend_rows: list[dict[str, Any]],
    aggregate_rows: list[dict[str, Any]],
    compare: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    counts = [row.get("count", 0) for row in trend_rows]
    avg_count = sum(counts) / len(counts) if counts else 0

    if trend_rows:
        peak = max(trend_rows, key=lambda row: row.get("count", 0))
        if peak.get("count", 0) >= avg_count * 1.3 and peak.get("count", 0) > 0:
            alerts.append(
                {
                    "level": "warning",
                    "title": f"{keyword} 在 {peak['date']} 出现峰值脉冲",
                    "detail": f"当日命中 {peak['count']} 条，明显高于均值 {avg_count:.1f}。代表热点是「{peak['title']}」。",
                }
            )

    for prev, curr in zip(trend_rows, trend_rows[1:]):
        prev_count = prev.get("count", 0)
        curr_count = curr.get("count", 0)
        if prev_count > 0 and curr_count <= prev_count * 0.5:
            alerts.append(
                {
                    "level": "critical",
                    "title": f"{keyword} 在 {curr['date']} 出现异常回落",
                    "detail": f"从前一天的 {prev_count} 条降到 {curr_count} 条，跌幅超过 50%。",
                }
            )
            break

    cross_event = next((row for row in aggregate_rows if row.get("platformCount", 0) >= 4), None)
    if cross_event:
        alerts.append(
            {
                "level": "info",
                "title": "跨平台传播事件值得盯防",
                "detail": f"「{cross_event['title']}」已覆盖 {cross_event['platformCount']} 个平台，最佳排名 #{cross_event['bestRank']}。",
            }
        )

    overview = compare.get("data", {}).get("overview", {})
    change_percent = overview.get("count_change_percent", "0%")
    count_change = int(overview.get("count_change", 0) or 0)
    if count_change > 0:
        alerts.append(
            {
                "level": "info",
                "title": "后半程声量整体抬升",
                "detail": f"对比前半段，总新闻量变化 {change_percent}，说明该周期里有新热点接棒。",
            }
        )

    return alerts[:4]


@app.get("/")
async def serve_demo() -> FileResponse:
    return FileResponse(HTML_PATH)


@app.get("/ops-board")
async def serve_ops_board() -> FileResponse:
    return FileResponse(OPS_HTML_PATH)


@app.get("/hotspot-watch")
async def serve_hotspot_watch() -> FileResponse:
    return FileResponse(WATCH_HTML_PATH)


@app.get("/api/hotspot-tracker")
async def hotspot_tracker(
    keyword: str = Query("AI", min_length=1),
    days: int = Query(7, ge=1, le=30),
) -> dict[str, Any]:
    full_range, period1, period2 = _date_range(days)

    trend_task = _run_tool(
        "analyze_topic_trend",
        topic=keyword,
        analysis_type="trend",
        date_range=full_range,
    )
    insights_task = _run_tool(
        "analyze_data_insights",
        insight_type="platform_compare",
        topic=keyword,
        date_range=full_range,
    )
    search_task = _run_tool(
        "search_news",
        query=keyword,
        date_range=full_range,
        limit=60,
        sort_by="weight",
    )
    aggregate_task = _run_tool(
        "aggregate_news",
        date_range=full_range,
        limit=20,
    )
    compare_task = _run_tool(
        "compare_periods",
        period1=period1,
        period2=period2,
        compare_type="overview",
        top_n=10,
    )

    trend, insights, search, aggregate, compare = await asyncio.gather(
        trend_task, insights_task, search_task, aggregate_task, compare_task
    )

    return {
        "success": True,
        "keyword": keyword,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date_range": full_range,
        "metrics": {
            "total_news": int(aggregate.get("summary", {}).get("original_count", 0)),
            "deduplication_rate": aggregate.get("summary", {}).get("deduplication_rate", "0%"),
            "topic_hits": int(trend.get("summary", {}).get("total_mentions", 0)),
            "period_change": compare.get("data", {}).get("overview", {}).get("count_change_percent", "0%"),
        },
        "trendData": _build_trend_rows(trend),
        "platformData": _build_platform_rows(insights, search),
        "eventData": _build_event_rows(search),
    }


@app.get("/api/ops-board")
async def ops_board(
    keyword: str = Query("AI", min_length=1),
    days: int = Query(7, ge=1, le=30),
) -> dict[str, Any]:
    full_range, period1, period2 = _date_range(days)

    trend_task = _run_tool(
        "analyze_topic_trend",
        topic=keyword,
        analysis_type="trend",
        date_range=full_range,
    )
    insights_task = _run_tool(
        "analyze_data_insights",
        insight_type="platform_compare",
        topic=keyword,
        date_range=full_range,
    )
    aggregate_task = _run_tool(
        "aggregate_news",
        date_range=full_range,
        limit=20,
    )
    compare_task = _run_tool(
        "compare_periods",
        period1=period1,
        period2=period2,
        compare_type="overview",
        top_n=10,
    )

    trend, insights, aggregate, compare = await asyncio.gather(
        trend_task, insights_task, aggregate_task, compare_task
    )

    trend_rows = _build_trend_rows(trend)
    aggregate_rows = _build_aggregate_rows(aggregate)
    platform_rows = _build_platform_rows(insights, {"data": []})
    alerts = _build_ops_alerts(keyword, trend_rows, aggregate_rows, compare)

    return {
        "success": True,
        "keyword": keyword,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date_range": full_range,
        "metrics": {
            "total_news": int(aggregate.get("summary", {}).get("original_count", 0)),
            "aggregated_news": int(aggregate.get("summary", {}).get("aggregated_count", 0)),
            "deduplication_rate": aggregate.get("summary", {}).get("deduplication_rate", "0%"),
            "topic_hits": int(trend.get("summary", {}).get("total_mentions", 0)),
            "peak_count": int(trend.get("summary", {}).get("peak_count", 0)),
            "peak_time": trend.get("summary", {}).get("peak_time", ""),
            "period_change": compare.get("data", {}).get("overview", {}).get("count_change_percent", "0%"),
        },
        "trendData": trend_rows,
        "platformData": platform_rows,
        "propagationData": aggregate_rows,
        "alerts": alerts,
    }


@app.get("/api/hotspot-watch")
async def hotspot_watch(
    keyword: str = Query("", description="关键词，可选"),
    platforms: str = Query("", description="逗号分隔的平台 ID"),
    start_date: str = Query("", description="开始日期 YYYY-MM-DD"),
    end_date: str = Query("", description="结束日期 YYYY-MM-DD"),
    focus_mode: str = Query("current", pattern="^(current|daily)$"),
    limit: int = Query(40, ge=1, le=100),
) -> dict[str, Any]:
    platform_list = _parse_platforms(platforms)
    explicit_range = _build_date_range(start_date or None, end_date or None)
    cleaned_keyword = keyword.strip()
    focus_range = explicit_range or {
        "start": date.today().isoformat(),
        "end": date.today().isoformat(),
    }

    if cleaned_keyword:
        library_payload = await _run_tool(
            "search_news",
            query=cleaned_keyword,
            date_range=explicit_range,
            platforms=platform_list,
            limit=limit,
            sort_by="weight",
            include_url=True,
        )
        library_summary = library_payload.get("summary", {})
        library_rows = _annotate_library_trends(_build_library_rows(library_payload.get("data", [])))
        query_mode = "keyword_search"
    else:
        library_payload = await _run_tool(
            "get_news_by_date",
            date_range=focus_range,
            platforms=platform_list,
            limit=limit,
            include_url=True,
        )
        library_summary = library_payload.get("summary", {})
        library_rows = _annotate_library_trends(_build_library_rows(library_payload.get("data", [])))
        query_mode = "date_browse"

    aggregate_payload = await _run_tool(
        "aggregate_news",
        date_range=focus_range,
        platforms=platform_list,
        limit=max(limit, 20),
        include_url=True,
    )

    focus_snapshot, focus_cards = _build_focus_cards_from_aggregate(
        rows=aggregate_payload.get("data", []),
        mode=focus_mode,
        top_n=max(len(aggregate_payload.get("data", [])), 1),
        summary=aggregate_payload.get("summary", {}),
        keyword=cleaned_keyword,
    )

    return {
        "success": True,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mcp_url": MCP_URL,
        "filters": {
            "keyword": cleaned_keyword,
            "platforms": platform_list or [],
            "start_date": start_date,
            "end_date": end_date,
            "focus_mode": focus_mode,
            "limit": limit,
        },
        "focus": {
            "snapshot": focus_snapshot,
            "cards": focus_cards,
        },
        "library": {
            "query_mode": query_mode,
            "summary": library_summary,
            "rows": library_rows,
        },
    }
