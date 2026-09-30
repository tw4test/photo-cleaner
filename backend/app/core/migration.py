"""
資料庫版本管理與遷移模組。
使用 SQLite PRAGMA user_version 追蹤版本，啟動時自動執行遷移。

版本歷史：
  v1 → 初始版本（P1 版本）
  v2 → 掃描/分析拆分（spec-update-scan-analysis.md）
       - files 新增 hash_state
       - 新增 clip_features 表
       - features 移除 clip_embedding / zs_scores，新增 analyzed_at
       - 新增 analysis_failures 表
       - assets.status 新增 'missing' 值（僅文件，DB 無 CHECK constraint）
"""

import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CURRENT_VERSION = 2


def get_db_version(conn) -> int:
    """讀取目前的資料庫 user_version。"""
    row = conn.execute("PRAGMA user_version").fetchone()
    return row[0] if row else 0


def set_db_version(conn, version: int):
    # PRAGMA user_version 不支援 ? 參數綁定，直接格式化
    conn.execute(f"PRAGMA user_version = {version}")


def _backup_db(db_path: Path):
    """在遷移前備份資料庫。"""
    if not db_path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.parent / f"{db_path.name}.bak.{ts}"
    shutil.copy2(db_path, backup_path)
    logger.info(f"資料庫備份完成：{backup_path}")


def _get_current_clip_model(conn) -> tuple[str, str]:
    """從 settings 表取得目前設定的 CLIP 模型名稱。"""
    try:
        import json
        row_model = conn.execute(
            "SELECT value FROM settings WHERE key='clip_model'"
        ).fetchone()
        row_pretrained = conn.execute(
            "SELECT value FROM settings WHERE key='clip_pretrained'"
        ).fetchone()
        model = json.loads(row_model[0]) if row_model else "ViT-B-16"
        pretrained = json.loads(row_pretrained[0]) if row_pretrained else "openai"
        return model, pretrained
    except Exception:
        return "ViT-B-16", "openai"


def _column_exists(conn, table: str, column: str) -> bool:
    """檢查欄位是否存在（遷移冪等性）。"""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _migrate_v1_to_v2(conn, db_path: Optional[Path] = None):
    """
    v1 → v2 遷移：
    1. 備份資料庫
    2. files 新增 hash_state
    3. 建立 clip_features 表
    4. 搬移 features.clip_embedding / zs_scores → clip_features
    5. features 新增 analyzed_at
    6. 建立 analysis_failures 表
    """
    logger.info("開始執行資料庫遷移 v1 → v2 ...")

    # 1. 備份
    if db_path:
        _backup_db(db_path)

    # 2. files.hash_state
    if not _column_exists(conn, "files", "hash_state"):
        conn.execute("ALTER TABLE files ADD COLUMN hash_state TEXT NOT NULL DEFAULT 'pending'")
        logger.info("已新增 files.hash_state 欄位")

    # 把已有 sha256 的主檔設為 done
    conn.execute(
        "UPDATE files SET hash_state='done' "
        "WHERE sha256 IS NOT NULL AND role='primary'"
    )

    # 3. clip_features 表
    if not _table_exists(conn, "clip_features"):
        conn.execute("""
            CREATE TABLE clip_features (
                asset_id  INTEGER PRIMARY KEY
                          REFERENCES assets(id) ON DELETE CASCADE,
                model     TEXT NOT NULL,
                pretrained TEXT NOT NULL,
                dim       INTEGER NOT NULL,
                embedding BLOB,
                zs        TEXT NOT NULL DEFAULT '{}',
                analyzed_at DATETIME
            )
        """)
        logger.info("已建立 clip_features 表")

    # 4. 搬移現有 CLIP 資料（若 features 表有 clip_embedding 欄位）
    existing_clip_col = _column_exists(conn, "features", "clip_embedding")
    if existing_clip_col:
        model, pretrained = _get_current_clip_model(conn)
        conn.execute("""
            INSERT OR IGNORE INTO clip_features
                (asset_id, model, pretrained, dim, embedding, zs, analyzed_at)
            SELECT
                asset_id,
                ?,
                ?,
                512,
                clip_embedding,
                COALESCE(zs_scores, '{}'),
                datetime('now')
            FROM features
            WHERE clip_embedding IS NOT NULL
        """, (model, pretrained))
        logger.info(f"已搬移現有 CLIP 向量至 clip_features（model={model}）")

        # 5. 重建 features 表（移除 clip_embedding / zs_scores）
        _rebuild_features_table(conn)

    # 5b. 若 features 表已無 clip_embedding，仍需確認有 analyzed_at 欄位
    if not _column_exists(conn, "features", "analyzed_at"):
        conn.execute("ALTER TABLE features ADD COLUMN analyzed_at DATETIME")
        logger.info("已新增 features.analyzed_at 欄位")

    # 6. analysis_failures 表
    if not _table_exists(conn, "analysis_failures"):
        conn.execute("""
            CREATE TABLE analysis_failures (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id    INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                stage       TEXT NOT NULL,
                error_msg   TEXT,
                occurred_at DATETIME NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_analysis_failures_asset_id ON analysis_failures(asset_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_analysis_failures_stage ON analysis_failures(stage)")
        logger.info("已建立 analysis_failures 表")

    logger.info("資料庫遷移 v1 → v2 完成。")


def _rebuild_features_table(conn):
    """
    SQLite 不支援直接 DROP COLUMN（3.35 以前版本），
    使用「重建表」方式移除 clip_embedding 與 zs_scores。
    """
    conn.execute("ALTER TABLE features RENAME TO features_old")
    conn.execute("""
        CREATE TABLE features (
            asset_id     INTEGER PRIMARY KEY
                         REFERENCES assets(id) ON DELETE CASCADE,
            phash        TEXT,
            blur_score   REAL,
            brightness   REAL,
            width        INTEGER,
            height       INTEGER,
            taken_at     DATETIME,
            exif         TEXT NOT NULL DEFAULT '{}',
            decode_status TEXT NOT NULL DEFAULT 'ok',
            analyzed_at  DATETIME
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_features_phash ON features(phash)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_features_blur_score ON features(blur_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_features_taken_at ON features(taken_at)")
    conn.execute("""
        INSERT INTO features
            (asset_id, phash, blur_score, brightness, width, height,
             taken_at, exif, decode_status)
        SELECT
            asset_id, phash, blur_score, brightness, width, height,
            taken_at,
            COALESCE(exif, '{}'),
            COALESCE(decode_status, 'ok')
        FROM features_old
    """)
    conn.execute("DROP TABLE features_old")
    logger.info("已重建 features 表（已移除 clip_embedding / zs_scores）")


def run_migrations(sync_engine, db_path: Optional[Path] = None):
    """
    在應用程式啟動時呼叫，自動執行所有待執行的遷移。
    必須使用 SQLAlchemy raw connection（非 ORM Session）以執行 DDL。
    """
    with sync_engine.connect() as conn:
        raw = conn.connection  # 取得底層 sqlite3 connection
        version = get_db_version(raw)
        logger.info(f"目前資料庫版本：v{version}，目標版本：v{CURRENT_VERSION}")

        if version < 2:
            _migrate_v1_to_v2(raw, db_path)
            set_db_version(raw, 2)
            raw.commit()

        if version == CURRENT_VERSION:
            logger.debug("資料庫版本已是最新，無需遷移。")
