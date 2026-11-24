"""强制结悬账：值班人工结悬的独立主链路。

编排位：双源入库 → 同键对齐 → 单边悬账 →（本模块）人工强制结悬 → 对拍结论。

收口形态（点强制结悬须择定，或由轨规策略「强制结收口形态」给定）：
- 保留：保留已结悬行加独立凭证——悬行状态已强制结仍可查，独立凭证钉结因，结论改读已结集。
- 删除：物理删除悬行只留切流账册——悬表无行，切流账册回读结因；读引用不同。

硬约束：
- 结态凭证结论重算同成败：结论只数未结，已结集由凭证/切流钉住，重跑自洽。
- 对齐跑数并发会半截：跑对齐与强制结共用「对齐结悬锁」互斥（线程锁 + BEGIN IMMEDIATE 双保险）。
- 不做互斥双成功：同键已有有效封印再点结悬 → 幂等命中，不二次出证；并发串行化，不得未结已结双态。
"""
from __future__ import annotations

import sqlite3
import threading
import uuid

from app.db import 连接
from app.seed import 此刻
from app.services import 目录

NAME = "强制结悬账"

形态集 = ("保留", "删除")
策略轨规键 = "强制结收口形态"

# 对齐跑数与强制结互斥：同进程线程锁；事务内再 BEGIN IMMEDIATE 拿库级写锁。
对齐结悬锁 = threading.Lock()


class 结悬错(Exception):
    """带 HTTP 码的结悬业务错。"""

    def __init__(self, 码: int, 消息: str):
        super().__init__(消息)
        self.码 = 码
        self.消息 = 消息


# 悬账单基线表（与 seed.建表 同构）：确保表在 seed 之前被调用时也能立表再加列。
_悬账单基线 = """
CREATE TABLE IF NOT EXISTS 悬账单 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  业务键 TEXT NOT NULL,
  悬边 TEXT NOT NULL,
  金额分 INTEGER NOT NULL,
  事件编号 TEXT NOT NULL DEFAULT '',
  原因 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
"""

_建表 = """
CREATE TABLE IF NOT EXISTS 结悬凭证 (
  凭证键 TEXT PRIMARY KEY,
  业务键 TEXT NOT NULL,
  结因 TEXT NOT NULL DEFAULT '',
  操作人 TEXT NOT NULL DEFAULT '',
  时刻 TEXT NOT NULL,
  原悬边 TEXT NOT NULL DEFAULT '',
  原金额分 INTEGER NOT NULL DEFAULT 0,
  原事件编号 TEXT NOT NULL DEFAULT '',
  有效 INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS 切流账册 (
  切流键 TEXT PRIMARY KEY,
  业务键 TEXT NOT NULL,
  结因 TEXT NOT NULL DEFAULT '',
  操作人 TEXT NOT NULL DEFAULT '',
  时刻 TEXT NOT NULL,
  原悬边 TEXT NOT NULL DEFAULT '',
  原金额分 INTEGER NOT NULL DEFAULT 0,
  原事件编号 TEXT NOT NULL DEFAULT '',
  有效 INTEGER NOT NULL DEFAULT 1
);
"""


def _确保列(conn: sqlite3.Connection, 表: str, 列: str, 定义: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {表} ADD COLUMN {列} {定义}")
    except sqlite3.OperationalError:
        pass  # 列已存在


def 确保表() -> None:
    """幂等建表/加列：模块表 + 悬账单的 状态/凭证键 两列（基线行默认未结）。"""
    conn = 连接()
    try:
        conn.executescript(_悬账单基线)
        conn.executescript(_建表)
        _确保列(conn, "悬账单", "状态", "TEXT NOT NULL DEFAULT '未结'")
        _确保列(conn, "悬账单", "凭证键", "TEXT NOT NULL DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


def 有效封印(conn: sqlite3.Connection) -> dict:
    """业务键 → 有效封印 {形态,键,结因,操作人,时刻,原悬边,原金额分,原事件编号}。"""
    封: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT * FROM 结悬凭证 WHERE 有效=1 ORDER BY 时刻, 凭证键"
    ).fetchall():
        封[r["业务键"]] = {
            "形态": "保留", "键": r["凭证键"], "结因": r["结因"], "操作人": r["操作人"],
            "时刻": r["时刻"], "原悬边": r["原悬边"], "原金额分": r["原金额分"],
            "原事件编号": r["原事件编号"],
        }
    for r in conn.execute(
        "SELECT * FROM 切流账册 WHERE 有效=1 ORDER BY 时刻, 切流键"
    ).fetchall():
        封[r["业务键"]] = {
            "形态": "删除", "键": r["切流键"], "结因": r["结因"], "操作人": r["操作人"],
            "时刻": r["时刻"], "原悬边": r["原悬边"], "原金额分": r["原金额分"],
            "原事件编号": r["原事件编号"],
        }
    return 封


def _读策略() -> str:
    return str(目录.轨规().get(策略轨规键, "") or "").strip()


def 结悬(体: dict) -> dict:
    """点强制结悬：须择定收口形态（保留|删除），与对齐跑数互斥。"""
    确保表()
    if not isinstance(体, dict):
        raise 结悬错(400, "体须为对象")
    键 = str(体.get("业务键") or "").strip()
    if not 键:
        raise 结悬错(400, "缺业务键")
    形态 = str(体.get("形态") or 体.get("收口形态") or "").strip()
    if 形态 not in 形态集:
        策略 = _读策略()
        if 策略 in 形态集:
            形态 = 策略  # 轨规策略即择定
        else:
            raise 结悬错(400, "点强制结悬须择定收口形态:保留|删除")
    结因 = str(体.get("结因") or "").strip() or "值班强制结悬"
    操作人 = str(体.get("操作人") or "值班").strip() or "值班"

    with 对齐结悬锁:
        conn = 连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            旧 = 有效封印(conn).get(键)
            if 旧:
                仍在 = conn.execute(
                    "SELECT 1 FROM 悬账单 WHERE 业务键=?", (键,)
                ).fetchone() is not None
                conn.commit()
                return {
                    "成": True, "幂等": True, "业务键": 键, "形态": 旧["形态"],
                    "凭证或切流键": 旧["键"], "悬表是否仍在": 仍在, "valid": True,
                    "说明": "已有有效封印，幂等命中不二次出证",
                }
            行 = conn.execute(
                "SELECT * FROM 悬账单 WHERE 业务键=? AND 状态='未结' ORDER BY 编号 DESC",
                (键,),
            ).fetchall()
            if not 行:
                raise 结悬错(404, f"业务键{键}无未结悬账，不可强制结")
            代 = 行[0]
            时刻 = 此刻()
            if 形态 == "保留":
                凭 = "结凭-" + uuid.uuid4().hex[:10]
                conn.execute(
                    "UPDATE 悬账单 SET 状态='已强制结', 凭证键=? WHERE 业务键=? AND 状态='未结'",
                    (凭, 键),
                )
                conn.execute(
                    """INSERT INTO 结悬凭证(凭证键,业务键,结因,操作人,时刻,原悬边,原金额分,原事件编号,有效)
                       VALUES (?,?,?,?,?,?,?,?,1)""",
                    (凭, 键, 结因, 操作人, 时刻, 代["悬边"], 代["金额分"], 代["事件编号"]),
                )
                键名 = 凭
            else:
                切 = "切流-" + uuid.uuid4().hex[:10]
                conn.execute(
                    "DELETE FROM 悬账单 WHERE 业务键=? AND 状态='未结'", (键,)
                )
                conn.execute(
                    """INSERT INTO 切流账册(切流键,业务键,结因,操作人,时刻,原悬边,原金额分,原事件编号,有效)
                       VALUES (?,?,?,?,?,?,?,?,1)""",
                    (切, 键, 结因, 操作人, 时刻, 代["悬边"], 代["金额分"], 代["事件编号"]),
                )
                键名 = 切
            conn.commit()
            return {
                "成": True, "幂等": False, "业务键": 键, "形态": 形态,
                "凭证或切流键": 键名, "悬表是否仍在": 形态 == "保留", "valid": True,
                "结因": 结因, "操作人": 操作人, "时刻": 时刻, "结行数": len(行),
            }
        except 结悬错:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def 查(业务键: str) -> dict:
    """强制结只读接口：业务键、悬表是否仍在、凭证或切流键、valid。"""
    确保表()
    键 = str(业务键 or "").strip()
    if not 键:
        raise 结悬错(400, "缺业务键")
    conn = 连接()
    try:
        行 = conn.execute(
            "SELECT 状态,凭证键,悬边,金额分,事件编号,原因,时刻 FROM 悬账单 WHERE 业务键=? ORDER BY 编号 DESC",
            (键,),
        ).fetchall()
        封 = 有效封印(conn).get(键)
        仍在 = len(行) > 0
        状态 = 行[0]["状态"] if 行 else None
        out = {
            "业务键": 键,
            "悬表是否仍在": 仍在,
            "悬表状态": 状态,
            "形态": 封["形态"] if 封 else None,
            "凭证或切流键": 封["键"] if 封 else None,
            "valid": bool(封),
            "结因": 封["结因"] if 封 else None,
            "操作人": 封["操作人"] if 封 else None,
            "时刻": 封["时刻"] if 封 else None,
            "原悬边": 封["原悬边"] if 封 else None,
            "原金额分": 封["原金额分"] if 封 else None,
            "原事件编号": 封["原事件编号"] if 封 else None,
        }
        # 审计哨兵：封印与悬表对不上时 valid 拉 False，便于对拍结论同票核查。
        if 封 and 封["形态"] == "保留" and not 仍在:
            out["valid"] = False
            out["异常"] = "凭证在而悬行失"
        if 封 and 封["形态"] == "删除" and 仍在 and 状态 == "未结":
            out["valid"] = False
            out["异常"] = "切流后悬表仍有未结行"
        return out
    finally:
        conn.close()


def 列表() -> list[dict]:
    """凭证 + 切流合并视图（列表面板）。"""
    确保表()
    conn = 连接()
    try:
        out: list[dict] = []
        for r in conn.execute(
            "SELECT * FROM 结悬凭证 ORDER BY 时刻 DESC, 凭证键 DESC"
        ).fetchall():
            仍在 = conn.execute(
                "SELECT 1 FROM 悬账单 WHERE 业务键=?", (r["业务键"],)
            ).fetchone() is not None
            out.append({
                "类别": "凭证", "形态": "保留", "键": r["凭证键"], "业务键": r["业务键"],
                "结因": r["结因"], "操作人": r["操作人"], "时刻": r["时刻"],
                "原悬边": r["原悬边"], "原金额分": r["原金额分"], "原事件编号": r["原事件编号"],
                "有效": r["有效"], "悬表是否仍在": 仍在,
            })
        for r in conn.execute(
            "SELECT * FROM 切流账册 ORDER BY 时刻 DESC, 切流键 DESC"
        ).fetchall():
            仍在 = conn.execute(
                "SELECT 1 FROM 悬账单 WHERE 业务键=?", (r["业务键"],)
            ).fetchone() is not None
            out.append({
                "类别": "切流", "形态": "删除", "键": r["切流键"], "业务键": r["业务键"],
                "结因": r["结因"], "操作人": r["操作人"], "时刻": r["时刻"],
                "原悬边": r["原悬边"], "原金额分": r["原金额分"], "原事件编号": r["原事件编号"],
                "有效": r["有效"], "悬表是否仍在": 仍在,
            })
        out.sort(key=lambda x: (x["时刻"], x["键"]), reverse=True)
        return out
    finally:
        conn.close()


def 卡片() -> dict:
    """卡片面板：未结 / 已结留档 / 凭证 / 切流 / 已结集。"""
    确保表()
    conn = 连接()
    try:
        未结 = conn.execute(
            "SELECT COUNT(*) AS c FROM 悬账单 WHERE 状态='未结'"
        ).fetchone()["c"]
        留档 = conn.execute(
            "SELECT COUNT(*) AS c FROM 悬账单 WHERE 状态='已强制结'"
        ).fetchone()["c"]
        凭 = conn.execute(
            "SELECT COUNT(*) AS c FROM 结悬凭证 WHERE 有效=1"
        ).fetchone()["c"]
        切 = conn.execute(
            "SELECT COUNT(*) AS c FROM 切流账册 WHERE 有效=1"
        ).fetchone()["c"]
        return {
            "未结": 未结,
            "已强制结留档": 留档,
            "凭证数": 凭,
            "切流数": 切,
            "已结集": 凭 + 切,
            "策略": _读策略() or None,
        }
    finally:
        conn.close()


def 策略() -> dict:
    当前 = _读策略()
    return {
        "策略": 当前 or None,
        "可选": list(形态集),
        "轨规键": 策略轨规键,
        "说明": {
            "保留": "保留已结悬行加独立凭证：悬行状态已强制结仍可查，独立凭证钉结因，结论改读已结集",
            "删除": "物理删除悬行只留切流账册：悬表无行，切流账册回读结因；读引用不同",
        },
    }


def 改策略(体: dict) -> dict:
    值 = str((体 or {}).get("策略") or "").strip()
    if 值 and 值 not in 形态集:
        raise 结悬错(400, "策略须为 保留|删除 或空")
    目录.改轨规({策略轨规键: 值})
    return 策略()
