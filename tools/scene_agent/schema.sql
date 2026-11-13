-- 场景 Agent 索引库结构（SQLite）
-- components   : 106,094 个组件的元数据与程序文本
-- components_fts : FTS5 全文索引（names 列权重高，body 即程序全文）
-- rebuild_cache  : 重建结果缓存（GLB 路径 + 包围盒），按程序指纹失效
-- scenes        : 每次生成的场景记录

CREATE TABLE IF NOT EXISTS components (
    id              TEXT PRIMARY KEY,
    source          TEXT,
    source_group    TEXT,
    family          TEXT,
    entry           TEXT,
    name            TEXT,            -- 主名称：entry / assembly 名 / 首个函数名
    program         TEXT NOT NULL,
    program_sha256  TEXT,
    sand3d_version  TEXT,
    usda_path       TEXT,
    n_joints        INTEGER,
    f_score         REAL,
    license         TEXT,
    attribution     TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS components_fts USING fts5(
    id UNINDEXED,
    names,                -- family / entry / 函数名 / assembly 名
    body,                 -- 程序全文
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS rebuild_cache (
    id              TEXT PRIMARY KEY,
    program_sha256  TEXT,
    sand3d_version  TEXT,
    glb_path        TEXT,
    blend_path      TEXT,
    bbox_json       TEXT,           -- {"w","d","h","min":[x,y,z],"max":[x,y,z]}，单位 mm
    status          TEXT,           -- ok / error
    error           TEXT,
    seconds         REAL,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS scenes (
    scene_id        TEXT PRIMARY KEY,
    prompt          TEXT,
    spec_json       TEXT,
    blend_path      TEXT,
    preview_path    TEXT,
    created_at      TEXT
);

-- CLIP 图像向量（dim=512，vec 为 float16 小端，已 L2 归一化）
CREATE TABLE IF NOT EXISTS clip_vectors (
    id              TEXT PRIMARY KEY,
    dim             INTEGER NOT NULL,
    vec             BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS clip_meta (
    key             TEXT PRIMARY KEY,
    value           TEXT
);

CREATE INDEX IF NOT EXISTS idx_components_family ON components(family);
CREATE INDEX IF NOT EXISTS idx_components_source ON components(source);
