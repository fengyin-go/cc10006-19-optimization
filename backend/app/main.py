"""数据湖仓一体管理平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.services.audit import AuditService
from app.store import store

app = FastAPI(title="数据湖仓一体管理平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in ROUTERS:
    app.include_router(module.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。

    审计模块的待处理量直接取行上的实时 pending 标记，因此归档 / 忽略动作
    执行后，这里的「待处理」与「异常量」会立刻同步下降，无需刷新单独页面。
    """
    payload = store.overview()
    audit_stats = AuditService().stats()
    payload["audit"] = audit_stats
    payload["cards"].extend([
        {"label": "审计待办", "value": audit_stats["pending"]},
        {"label": "审计质量告警", "value": audit_stats["qualityAlerts"]},
        {"label": "待处理影响行数", "value": audit_stats["impactRows"]},
    ])
    return payload
