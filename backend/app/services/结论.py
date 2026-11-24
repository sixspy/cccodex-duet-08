"""对拍结论：核对对齐差与悬账是否自洽。

与强制结悬账同票：悬账笔数只数未结；已结集（保留凭证/删除切流）由强制结悬账钉结因。
重算同成败——结态凭证在，结论重跑结论一致；未结已结双态、凭证/切流与悬表对不上都算问题。
"""
from __future__ import annotations
from app.db import 连接
from app.seed import 此刻
from app.modules.强制结悬账 import 服务 as 结悬服务

def 跑一次() -> dict:
    结悬服务.确保表()
    # 与对齐/强制结同锁：并发下结论读到的悬账+封印是同一快照，重算同成败
    with 结悬服务.对齐结悬锁:
        return _跑一次体()

def _跑一次体() -> dict:
    conn = 连接()
    try:
        左 = conn.execute("SELECT COUNT(*) AS c FROM 左轨流水").fetchone()["c"]
        右 = conn.execute("SELECT COUNT(*) AS c FROM 右轨流水").fetchone()["c"]
        对齐 = conn.execute(
            "SELECT COUNT(*) AS c FROM 对齐差 WHERE 差型='对齐'"
        ).fetchone()["c"]
        未结 = conn.execute(
            "SELECT COUNT(*) AS c FROM 悬账单 WHERE 状态='未结'"
        ).fetchone()["c"]
        差总 = conn.execute("SELECT COUNT(*) AS c FROM 对齐差").fetchone()["c"]
        封 = 结悬服务.有效封印(conn)
        凭数 = sum(1 for v in 封.values() if v["形态"] == "保留")
        切数 = sum(1 for v in 封.values() if v["形态"] == "删除")
        已结 = 凭数 + 切数

        问题 = []
        未结键: set[str] = set()
        悬表键: set[str] = set()
        # 未结悬账业务键应能在对齐差找到左缺/右缺；已强制结留档行须有有效凭证
        for r in conn.execute("SELECT * FROM 悬账单").fetchall():
            悬表键.add(r["业务键"])
            if r["状态"] == "已强制结":
                封r = 封.get(r["业务键"])
                if not 封r or 封r["形态"] != "保留":
                    问题.append(f"已结行{r['业务键']}缺有效凭证")
                continue
            未结键.add(r["业务键"])
            行 = conn.execute(
                "SELECT 差型 FROM 对齐差 WHERE 业务键=?", (r["业务键"],)
            ).fetchone()
            if not 行:
                问题.append(f"悬账{r['业务键']}无对齐差行")
            elif r["悬边"] == "左" and 行["差型"] not in ("右缺", "金额不符"):
                问题.append(f"左悬{r['业务键']}差型={行['差型']}")
            elif r["悬边"] == "右" and 行["差型"] not in ("左缺", "金额不符"):
                问题.append(f"右悬{r['业务键']}差型={行['差型']}")

        # 封印与悬表同票核查：不得未结已结双态；形态与悬表存否须一致
        for k, v in 封.items():
            if k in 未结键:
                问题.append(f"{k}未结已结双态")
            if v["形态"] == "删除" and k in 悬表键:
                问题.append(f"{k}切流后悬表仍有行")
            if v["形态"] == "保留" and k not in 悬表键:
                问题.append(f"{k}凭证在而悬行失")

        通过 = len(问题) == 0 and (左 + 右 == 0 or 差总 > 0 or (左 > 0 and 右 > 0 and 对齐 >= 0))
        if 左 == 0 and 右 == 0:
            通过 = True
            说明 = "双轨皆空，结论空跑通过"
        elif 问题:
            通过 = False
            说明 = "问题:" + ";".join(问题[:6])
        else:
            说明 = f"左{左}/右{右}/对齐{对齐}/未结{未结}/已结{已结}(凭{凭数}切{切数})/差行{差总} 自洽"

        conn.execute(
            """INSERT INTO 对拍结论(时刻,左笔数,右笔数,对齐笔数,悬账笔数,是否通过,说明)
               VALUES (?,?,?,?,?,?,?)""",
            (此刻(), 左, 右, 对齐, 未结, 1 if 通过 else 0, 说明),
        )
        conn.commit()
        return {
            "是否通过": 通过,
            "左笔数": 左,
            "右笔数": 右,
            "对齐笔数": 对齐,
            "悬账笔数": 未结,
            "未结笔数": 未结,
            "已结笔数": 已结,
            "凭证数": 凭数,
            "切流数": 切数,
            "说明": 说明,
            "问题": 问题,
        }
    finally:
        conn.close()
