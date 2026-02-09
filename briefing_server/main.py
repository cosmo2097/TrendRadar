# coding=utf-8
"""
TrendRadar API 入口
基于 FastAPI 提供 RESTful 服务
"""
import logging
from typing import List, Optional, Dict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from trendradar import __version__
from briefing_server.service import BriefingService

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trendradar.api")

# 全局实例
service: Optional[BriefingService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理
    """
    # 启动时执行
    logger.info("Briefing Server starting...")
    
    # 初始化服务
    # service = BriefingService() # 服务实例在接口中按需创建或作为依赖注入
    
    logger.info("Briefing Server started successfully")
    
    yield
    
    # 关闭时执行
    logger.info("Briefing Server shutting down...")


app = FastAPI(
    title="TrendRadar API",
    version=__version__,
    description="TrendRadar 智能简报服务 API",
    lifespan=lifespan
)


# === 请求模型 ===

class BriefingRequest(BaseModel):
    """简报生成请求"""
    rules: List[str] = Field(..., description="频率词规则列表，支持 '关键词'、'+必须词'、'!过滤词' 等语法")
    allowed_sources: Optional[List[str]] = Field(None, description="允许的数据源 ID 列表（为空则不限制）")
    custom_rss_urls: Optional[List[str]] = Field(None, description="需要实时抓取的自定义 RSS URL")
    stream: bool = Field(True, description="是否使用流式响应 (SSE)")
    ai_model: Optional[str] = Field(None, description="指定 AI 模型 (可选)")


# === API 接口 ===

@app.post("/api/v1/briefing")
async def create_briefing(request: BriefingRequest):
    """
    生成 AI 简报
    
    - 支持流式响应 (stream=True, 默认)
    - 支持自定义规则和数据源
    """
    if not service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    try:
        # 调用服务
        if request.stream:
            # 流式响应
            # 必须 await generate_briefing 才能拿到内部返回的 async generator
            generator = await service.generate_briefing(
                rules=request.rules,
                allowed_sources=request.allowed_sources,
                custom_rss_urls=request.custom_rss_urls,
                ai_model=request.ai_model,
                stream_ai=True
            )
            return StreamingResponse(
                generator,
                media_type="text/event-stream"
            )
        else:
            # 普通响应
            result = await service.generate_briefing(
                rules=request.rules,
                allowed_sources=request.allowed_sources,
                custom_rss_urls=request.custom_rss_urls,
                ai_model=request.ai_model,
                stream_ai=False
            )
            return result

    except Exception as e:
        logger.error(f"API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "version": __version__}


if __name__ == "__main__":
    import uvicorn
    # 开发模式启动
    uvicorn.run("trendradar.api.main:app", host="0.0.0.0", port=8000, reload=True)
