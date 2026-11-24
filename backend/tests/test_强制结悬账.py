"""强制结悬账：双形态收口、只读接口、结论同票、互斥不双态。"""
import os
import threading
from pathlib import Path

_tmp = Path(__file__).resolve().parent / "_tmp"
_tmp.mkdir(exist_ok=True)
os.environ["数据目录"] = str(_tmp)
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

from app import seed
from app.services import 左装载, 右装载, 对齐, 结论, 目录
from app.modules.强制结悬账 import 服务 as 结悬服务


def setup_function():
    db = _tmp / "对拍.db"
    if db.exists():
        db.unlink()
    seed.初始化库()
    结悬服务.确保表()


def _灌左悬(键="K悬", 金额=100):
    """左轨有键、右轨无键 → 对齐后仅该键左悬。"""
    左装载.装一批({"事件": [
        {"事件编号": "e左-" + 键, "业务键": 键, "金额分": 金额, "源键": "渠甲",
         "事件时": "2026-11-23T10:00:00Z"},
    ]})
    对齐.跑对齐()


def _未结键():
    return {r["业务键"] for r in 目录.悬列表() if r.get("状态", "未结") == "未结"}


def test_保留形态_悬表仍在_结论重跑自洽():
    _灌左悬("K保")
    assert "K保" in _未结键()

    r = 结悬服务.结悬({"业务键": "K保", "形态": "保留", "结因": "值班夜结", "操作人": "小甲"})
    assert r["成"] is True and r["形态"] == "保留" and r["悬表是否仍在"] is True
    assert r["凭证或切流键"].startswith("结凭-")

    # 保留形态：悬表仍在，状态已强制结，凭证钉结因
    行 = [x for x in 目录.悬列表() if x["业务键"] == "K保"]
    assert len(行) == 1 and 行[0]["状态"] == "已强制结" and 行[0]["凭证键"] == r["凭证或切流键"]

    # 只读接口：业务键、悬表是否仍在、凭证或切流键、valid
    q = 结悬服务.查("K保")
    assert q["悬表是否仍在"] is True and q["valid"] is True
    assert q["凭证或切流键"] == r["凭证或切流键"] and q["形态"] == "保留"
    assert q["结因"] == "值班夜结" and q["原悬边"] == "左"

    # 结论改读已结集：不再算未结；重算同成败
    g1 = 结论.跑一次()
    assert g1["是否通过"] is True and g1["悬账笔数"] == 0 and g1["已结笔数"] == 1
    # 对齐重跑：留档行不动、不重悬、不双态
    r2 = 对齐.跑对齐()
    assert r2["已结跳过"] >= 1
    行2 = [x for x in 目录.悬列表() if x["业务键"] == "K保"]
    assert len(行2) == 1 and 行2[0]["状态"] == "已强制结"
    assert "K保" not in _未结键()
    g2 = 结论.跑一次()
    assert g2["是否通过"] is True and g2["悬账笔数"] == 0


def test_删除形态_悬表无行有切流():
    _灌左悬("K删")
    r = 结悬服务.结悬({"业务键": "K删", "形态": "删除", "结因": "审计口径"})
    assert r["成"] is True and r["悬表是否仍在"] is False
    assert r["凭证或切流键"].startswith("切流-")

    # 删除形态：悬表无行，切流账册回读结因
    assert all(x["业务键"] != "K删" for x in 目录.悬列表())
    q = 结悬服务.查("K删")
    assert q["悬表是否仍在"] is False and q["valid"] is True
    assert q["形态"] == "删除" and q["凭证或切流键"] == r["凭证或切流键"]
    assert q["结因"] == "审计口径"

    g1 = 结论.跑一次()
    assert g1["是否通过"] is True and g1["悬账笔数"] == 0 and g1["切流数"] == 1
    # 对齐重跑：封印键不重悬；结论重算同成败
    对齐.跑对齐()
    assert all(x["业务键"] != "K删" for x in 目录.悬列表())
    g2 = 结论.跑一次()
    assert g2["是否通过"] is True and g2["悬账笔数"] == 0


def test_结后查悬表命中可区分():
    _灌左悬("K区")
    结悬服务.结悬({"业务键": "K区", "形态": "删除"})
    删 = 结悬服务.查("K区")
    无 = 结悬服务.查("K未曾有")
    # 已结已删 vs 从未有悬：悬表都不在，但封印与 valid 可区分
    assert 删["悬表是否仍在"] is False and 删["valid"] is True and 删["凭证或切流键"]
    assert 无["悬表是否仍在"] is False and 无["valid"] is False and 无["凭证或切流键"] is None


def test_点强制结须择定形态_策略兜底():
    _灌左悬("K择")
    try:
        结悬服务.结悬({"业务键": "K择"})
        assert False, "未择形态不应放行"
    except 结悬服务.结悬错 as e:
        assert e.码 == 400 and "择定" in e.消息
    # 轨规策略即择定：未传形态时按策略收口
    结悬服务.改策略({"策略": "保留"})
    r = 结悬服务.结悬({"业务键": "K择"})
    assert r["形态"] == "保留"
    try:
        结悬服务.改策略({"策略": "乱"})
        assert False
    except 结悬服务.结悬错:
        pass


def test_幂等不二次出证():
    _灌左悬("K幂")
    r1 = 结悬服务.结悬({"业务键": "K幂", "形态": "保留"})
    r2 = 结悬服务.结悬({"业务键": "K幂", "形态": "删除"})  # 已结再结：幂等命中
    assert r2["幂等"] is True and r2["凭证或切流键"] == r1["凭证或切流键"]
    assert len([x for x in 结悬服务.列表() if x["业务键"] == "K幂"]) == 1
    g = 结论.跑一次()
    assert g["是否通过"] is True


def test_无未结悬不可结():
    try:
        结悬服务.结悬({"业务键": "K无", "形态": "保留"})
        assert False
    except 结悬服务.结悬错 as e:
        assert e.码 == 404


def test_强制结与对齐并发不双态():
    _灌左悬("K并")
    错 = []

    def t结():
        try:
            结悬服务.结悬({"业务键": "K并", "形态": "保留"})
        except Exception as e:  # noqa: BLE001
            错.append(("结", e))

    def t齐():
        try:
            对齐.跑对齐()
        except Exception as e:  # noqa: BLE001
            错.append(("齐", e))

    for _ in range(5):
        线 = [threading.Thread(target=t结), threading.Thread(target=t齐)]
        for t in 线:
            t.start()
        for t in 线:
            t.join()
    assert not 错, f"并发出错:{错}"
    # 互斥串行后：K并 已结且不在未结集，结论自洽
    q = 结悬服务.查("K并")
    assert q["valid"] is True and q["悬表是否仍在"] is True
    assert "K并" not in _未结键()
    g = 结论.跑一次()
    assert g["是否通过"] is True, g["说明"]


def test_双源入库对齐后人工结悬编排():
    """全链编排：双源入库 → 对齐 → 人工结悬 → 结论同票。"""
    左装载.装一批({"事件": [
        {"事件编号": "p1", "业务键": "KA", "金额分": 100, "源键": "渠甲", "事件时": "2026-11-23T10:00:00Z"},
        {"事件编号": "p2", "业务键": "KB", "金额分": 200, "源键": "渠甲", "事件时": "2026-11-23T10:00:01Z"},
    ]})
    右装载.装一批({"事件": [
        {"事件编号": "c1", "业务键": "KA", "金额分": 100, "源键": "核乙", "事件时": "2026-11-23T10:00:00Z"},
    ]})
    r = 对齐.跑对齐()
    assert r["对齐"] == 1 and r["悬账"] == 1
    结悬服务.结悬({"业务键": "KB", "形态": "删除", "结因": "人工核销"})
    g = 结论.跑一次()
    assert g["是否通过"] is True and g["悬账笔数"] == 0 and g["已结笔数"] == 1
    卡 = 结悬服务.卡片()
    assert 卡["未结"] == 0 and 卡["已结集"] == 1 and 卡["切流数"] == 1
    列 = 结悬服务.列表()
    assert len(列) == 1 and 列[0]["类别"] == "切流" and 列[0]["悬表是否仍在"] is False


def test_接口层_查结列表策略():
    from fastapi.testclient import TestClient
    from app.main import app

    _灌左悬("K接")
    with TestClient(app) as c:
        r = c.post("/api/强制结悬账/结", json={"业务键": "K接", "形态": "保留", "结因": "接口结"})
        assert r.status_code == 200 and r.json()["成"] is True
        q = c.get("/api/强制结悬账/查", params={"业务键": "K接"})
        assert q.status_code == 200
        体 = q.json()
        assert 体["悬表是否仍在"] is True and 体["valid"] is True
        assert 体["凭证或切流键"].startswith("结凭-")
        q2 = c.post("/api/强制结悬账/查", json={"业务键": "K接"})
        assert q2.status_code == 200 and q2.json()["valid"] is True
        bad = c.post("/api/强制结悬账/结", json={"业务键": "K别"})
        assert bad.status_code == 400  # 须择定收口形态
        miss = c.post("/api/强制结悬账/结", json={"业务键": "K别", "形态": "保留"})
        assert miss.status_code == 404  # 无未结悬账
        列 = c.get("/api/强制结悬账")
        assert 列.status_code == 200 and len(列.json()) == 1
        卡 = c.get("/api/强制结悬账/卡片")
        assert 卡.json()["凭证数"] == 1
        s = c.post("/api/强制结悬账/策略", json={"策略": "删除"})
        assert s.status_code == 200 and s.json()["策略"] == "删除"
        assert 目录.轨规().get("强制结收口形态") == "删除"
