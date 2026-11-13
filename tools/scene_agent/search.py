"""中文关键词检索组件。

思路：数据集里的文本是英文（family / entry / 函数名 / 程序正文），
所以先用别名表把中文关键词翻译成英文检索词，再交给 FTS5，
names 列（函数名、assembly 名、family）权重远高于 body（程序正文）。

用法：
    from tools.scene_agent.search import search
    search("沙发", limit=10)
"""
import re
import sqlite3

from . import DB_PATH

# 中文别名 -> 英文检索词（可多词，任一命中即可）
ALIASES = {
    "沙发": ["sofa", "couch", "settee", "loveseat"],
    "茶几": ["coffee table", "tea table", "side table"],
    "边几": ["side table", "end table"],
    "桌子": ["table"],
    "餐桌": ["dining table"],
    "书桌": ["desk", "writing table"],
    "椅子": ["chair", "seat"],
    "扶手椅": ["armchair", "arm chair"],
    "凳子": ["stool"],
    "长凳": ["bench"],
    "床": ["bed"],
    "衣柜": ["wardrobe", "closet", "armoire"],
    "柜子": ["cabinet", "cupboard", "dresser"],
    "书架": ["shelf", "bookshelf", "bookcase"],
    "落地灯": ["floor lamp", "standing lamp", "lamp"],
    "台灯": ["desk lamp", "table lamp", "lamp"],
    "吊灯": ["ceiling light", "chandelier", "pendant", "lamp"],
    "灯": ["lamp", "light", "lighting"],
    "地毯": ["rug", "carpet", "mat"],
    "窗帘": ["curtain", "drape", "blind"],
    "植物": ["plant", "potted plant", "tree"],
    "盆栽": ["plant", "pot", "planter"],
    "花盆": ["pot", "planter", "vase"],
    "镜子": ["mirror"],
    "电视": ["television", "tv", "monitor"],
    "挂画": ["picture", "frame", "painting"],
    "画": ["picture", "frame", "painting"],
    "枕头": ["pillow", "cushion"],
    "屏风": ["screen", "partition"],
    "门": ["door", "gate"],
    "窗": ["window"],
    "收纳箱": ["box", "container", "bin"],
    "花瓶": ["vase"],
    "洗手台": ["sink", "basin"],
    "马桶": ["toilet"],
    "浴缸": ["bathtub", "tub"],
    "厨房": ["kitchen", "counter"],
    "灶台": ["stove", "cooktop"],
    "冰箱": ["refrigerator", "fridge"],
}

# 英文同义词补全，避免「sofa」只命中 sofa
SYNONYMS = {
    "sofa": ["couch", "settee"],
    "couch": ["sofa"],
    "lamp": ["light", "lighting"],
    "table": ["desk"],
}

TOKEN = re.compile(r"[A-Za-z0-9_]+")
CJK = re.compile(r"[\u4e00-\u9fff]+")


def connect():
    """只读打开索引库。"""
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def expand(query):
    """把查询扩成英文检索词列表；多词短语整体优先，再退回单词。

    例："coffee table" -> ["coffee table", "coffee", "table"]
        "落地灯"        -> ["floor lamp", "standing lamp", "lamp", "light", ...]
    """
    query = (query or "").strip()
    tokens = [t.lower() for t in TOKEN.findall(query)]
    terms = []
    # 英文多词：先保留整体短语，避免 "coffee" 把咖啡杯也召回
    phrase = " ".join(tokens)
    if len(tokens) > 1:
        terms.append(phrase)
    # 中文：最长匹配优先，命中后把该片段从串里去掉，避免「落地灯」又被「灯」重复扩一次
    rest = query
    for cn in sorted(ALIASES, key=len, reverse=True):
        if cn in rest:
            terms.extend(ALIASES[cn])
            rest = rest.replace(cn, " ")
    terms.extend(tokens)
    out = []
    for t in terms:
        if t and t not in out:
            out.append(t)
        for s in SYNONYMS.get(t, []):
            if s not in out:
                out.append(s)
    return out


def build_fts(query_terms):
    """拼 FTS5 MATCH 表达式：单词走前缀匹配，多词短语按顺序匹配。"""
    parts = []
    for t in query_terms:
        safe = t.replace('"', " ").strip()
        if not safe:
            continue
        parts.append(f'"{safe}"*')
    return " OR ".join(parts)


def _rows(con, match, limit, source, family, exclude):
    """执行一次 FTS 查询。"""
    sql = (
        "SELECT c.id, c.name, c.family, c.source, c.license, c.entry, c.n_joints, r.bbox_json,"
        "       bm25(components_fts, 20.0, 1.0) AS score"
        " FROM components_fts f JOIN components c ON c.id = f.id"
        " LEFT JOIN rebuild_cache r ON r.id = c.id AND r.status = 'ok'"
        " WHERE components_fts MATCH ?"
    )
    params = [match]
    if source:
        sql += " AND c.source = ?"
        params.append(source)
    if family:
        sql += " AND c.family = ?"
        params.append(family)
    if exclude:
        sql += " AND c.id NOT IN (%s)" % ",".join("?" * len(exclude))
        params.extend(exclude)
    sql += " ORDER BY score LIMIT ?"
    params.append(int(limit))
    return con.execute(sql, params).fetchall()


def search(query, limit=10, source=None, family=None, db=None):
    """检索组件：先按检索词顺序查名称字段（越具体的词越优先），不够再查程序正文。"""
    terms = expand(query)
    if not terms:
        return []
    con = db or connect()
    try:
        rows, seen = [], set()
        for term in terms:
            if len(rows) >= limit:
                break
            for row in _rows(con, f'names : ("{term}"*)', limit, source, family, None):
                if row["id"] not in seen:
                    seen.add(row["id"])
                    rows.append(row)
        if len(rows) < limit:
            match = build_fts(terms)
            rows += _rows(con, f"body : ({match})", limit - len(rows), source, family, list(seen))
    finally:
        if db is None:
            con.close()
    return [dict(r) for r in rows[:limit]]


def stats(db=None):
    """索引概况，用于自检。"""
    con = db or connect()
    try:
        total = con.execute("SELECT COUNT(*) FROM components").fetchone()[0]
        fts = con.execute("SELECT COUNT(*) FROM components_fts").fetchone()[0]
        fams = con.execute(
            "SELECT family, COUNT(*) n FROM components GROUP BY family ORDER BY n DESC LIMIT 5").fetchall()
        return {"components": total, "fts": fts, "top_families": [tuple(r) for r in fams]}
    finally:
        if db is None:
            con.close()


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "沙发"
    print("检索词:", expand(q))
    for hit in search(q, limit=10):
        print(f"{hit['score']:8.3f}  {hit['id'][:48]:48s} {hit['name']:<24s} {hit['family']}")
    print(stats())
