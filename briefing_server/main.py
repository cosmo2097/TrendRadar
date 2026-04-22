# coding=utf-8
"""
TrendRadar API 入口
基于 FastAPI 提供 RESTful 服务
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import AsyncGenerator, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from trendradar import __version__
from briefing_server.service import BriefingService


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trendradar.api")

service: Optional[BriefingService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Briefing Server starting...")

    global service
    service = BriefingService()

    logger.info("Briefing Server started successfully")
    yield
    logger.info("Briefing Server shutting down...")


app = FastAPI(
    title="TrendRadar API",
    version=__version__,
    description="TrendRadar 智能简报服务 API",
    lifespan=lifespan,
)


class BriefingRequest(BaseModel):
    """简报生成请求"""

    rules: List[str] = Field(default=[], description="频率词规则列表，支持 '关键词'、'+必须词'、'!过滤词' 等语法")
    preset: Optional[str] = Field(None, description="预设组名称 (与 rules 二选一)")
    allowed_sources: Optional[List[str]] = Field(None, description="允许的数据源 ID 列表（为空则不限制）")
    custom_rss_urls: Optional[List[str]] = Field(None, description="需要实时抓取的自定义 RSS URL")
    stream: bool = Field(True, description="是否使用流式响应 (SSE)")
    ai_model: Optional[str] = Field(None, description="指定 AI 模型 (可选)")
    date_range: str = Field("daily", description="日期范围: daily, weekly 或 YYYY-MM-DD (最大7天)")


class SearchRequest(BaseModel):
    """新闻搜索请求"""

    query: Optional[str] = Field(None, description="搜索关键词")
    start_date: Optional[str] = Field(None, description="开始日期 (YYYY-MM-DD), 默认为3天前")
    end_date: Optional[str] = Field(None, description="结束日期 (YYYY-MM-DD), 默认为今天")
    platforms: Optional[List[str]] = Field(None, description="指定平台 ID 列表")
    preset: Optional[str] = Field(None, description="预设组名称")
    format: Optional[str] = Field("timeline", description="返回格式: group (按源分组) 或 timeline (按时间排序)")


class AnalysisRequest(BaseModel):
    """AI 分析请求"""

    stats: List[Dict] = Field(..., description="热榜统计数据")
    rss_stats: List[Dict] = Field(..., description="RSS 统计数据")
    ai_model: Optional[str] = Field(None, description="指定 AI 模型 (可选)")
    stream: bool = Field(True, description="是否使用流式响应 (SSE)")
    date_range: str = Field("daily", description="日期范围: daily, weekly 或 YYYY-MM-DD (最大7天)")


def _to_sse_events(generator: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
    """将纯文本 async generator 包装为标准 SSE 帧。"""

    async def _wrapped():
        async for chunk in generator:
            text = str(chunk) if chunk is not None else ""
            lines = text.splitlines() or [""]
            for line in lines:
                yield f"data: {line}\n"
            yield "\n"
        yield "event: done\ndata: [DONE]\n\n"

    return _wrapped()


@app.post("/api/v1/briefing")
async def create_briefing(request: BriefingRequest):
    """生成 AI 简报"""
    if not service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    try:
        if not request.rules and not request.preset:
            raise HTTPException(status_code=400, detail="Must provide either 'rules' or 'preset'")

        if request.stream:
            generator = await service.generate_briefing(
                rules=request.rules,
                allowed_sources=request.allowed_sources,
                custom_rss_urls=request.custom_rss_urls,
                ai_model=request.ai_model,
                date_range=request.date_range,
                preset=request.preset,
                stream_ai=True,
            )
            return StreamingResponse(_to_sse_events(generator), media_type="text/event-stream")

        result = await service.generate_briefing(
            rules=request.rules,
            allowed_sources=request.allowed_sources,
            custom_rss_urls=request.custom_rss_urls,
            ai_model=request.ai_model,
            date_range=request.date_range,
            preset=request.preset,
            stream_ai=False,
        )
        return result
    except Exception as e:
        logger.error(f"API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/search")
async def search_news(request: SearchRequest):
    """搜索新闻，默认查询最近 3 天数据。"""
    if not service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    today = datetime.now().strftime("%Y-%m-%d")
    three_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    start_date = request.start_date or three_days_ago
    end_date = request.end_date or today

    try:
        return await service.search_news(
            query=request.query,
            start_date=start_date,
            end_date=end_date,
            platform_ids=request.platforms,
            preset=request.preset,
            result_format=request.format,
        )
    except Exception as e:
        logger.error(f"Search API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/briefing/data")
async def get_briefing_data(request: BriefingRequest):
    """获取简报数据（不含 AI 分析）。"""
    if not service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    try:
        return await service.fetch_briefing_data(
            rules=request.rules,
            allowed_sources=request.allowed_sources,
            custom_rss_urls=request.custom_rss_urls,
            date_range=request.date_range,
            preset=request.preset,
        )
    except Exception as e:
        logger.error(f"Data API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/briefing/analyze")
async def analyze_briefing(request: AnalysisRequest):
    """对数据进行 AI 分析。"""
    if not service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    try:
        if request.stream:
            generator = await service.analyze_briefing_data(
                stats=request.stats,
                rss_stats=request.rss_stats,
                ai_model=request.ai_model,
                stream_ai=True,
                date_range=request.date_range,
            )
            return StreamingResponse(_to_sse_events(generator), media_type="text/event-stream")

        return await service.analyze_briefing_data(
            stats=request.stats,
            rss_stats=request.rss_stats,
            ai_model=request.ai_model,
            stream_ai=False,
            date_range=request.date_range,
        )
    except Exception as e:
        logger.error(f"Analysis API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/presets")
async def get_presets():
    """获取全局预设关键词组列表。"""
    if not service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    try:
        from trendradar.core.frequency import load_frequency_words

        groups, _, _ = load_frequency_words()
        presets = [
            {"name": g["display_name"], "keywords": g["group_key"]}
            for g in groups
            if g.get("display_name")
        ]
        return {"presets": presets}
    except Exception as e:
        logger.error(f"Error loading presets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/reload")
async def reload_config():
    """热重载配置。"""
    if not service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    try:
        return service.reload_config()
    except Exception as e:
        logger.error(f"Reload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/sources")
async def get_sources():
    """获取所有可用数据源列表。"""
    if not service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    try:
        config = service.config
        platforms = config.get("PLATFORMS", [])
        rss_feeds = config.get("RSS", {}).get("FEEDS", [])
        return {"platforms": platforms, "rss_feeds": rss_feeds}
    except Exception as e:
        logger.error(f"Sources API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """健康检查。"""
    return {"status": "ok", "version": __version__}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("briefing_server.main:app", host="0.0.0.0", port=8000, reload=True)
