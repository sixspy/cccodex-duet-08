"""强制结悬账接口：结 / 查（只读）/ 列表 / 卡片 / 策略。"""
from fastapi import APIRouter, HTTPException

from app.modules.强制结悬账 import 服务

路由 = APIRouter()


def _包(f, *a):
    try:
        return f(*a)
    except 服务.结悬错 as e:
        raise HTTPException(e.码, e.消息)


@路由.get("/api/强制结悬账")
def api_列表():
    return _包(服务.列表)


@路由.get("/api/强制结悬账/查")
def api_查(业务键: str = ""):
    return _包(服务.查, 业务键)


@路由.post("/api/强制结悬账/查")
def api_查体(体: dict):
    return _包(服务.查, (体 or {}).get("业务键", ""))


@路由.post("/api/强制结悬账/结")
def api_结(体: dict):
    return _包(服务.结悬, 体)


@路由.get("/api/强制结悬账/卡片")
def api_卡片():
    return _包(服务.卡片)


@路由.get("/api/强制结悬账/策略")
def api_策略():
    return _包(服务.策略)


@路由.post("/api/强制结悬账/策略")
def api_改策略(体: dict):
    return _包(服务.改策略, 体)
