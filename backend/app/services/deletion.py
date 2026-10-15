import os
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from sqlalchemy.orm import Session
import send2trash

from app.models.schema import Asset, File, Candidate, ActionLog
from app.models.enums import DeletionMode, ActionType, AssetStatus

MTIME_TOLERANCE_S = 2.0


class DeletionService:
    """
    安全刪除執行器（鐵則 1, 2, 3, 4）：
    1. 只有 Process 模組能調用本服務執行刪檔。
    2. 預設使用 send2trash 移入資源回收筒。
    3. 刪除前最後一道 Keeper 檢查：任何群組至少保留一張仍存在的相片（v0.5）。
    4. 刪除前校驗：檔案必須存在，且大小「與」修改時間都要與資料庫一致；任一不符即不刪（v0.5 修正）。
    5. 配對檔案一併刪除（Live Photo HEIC/JPG + MOV、RAW + JPG）。
    6. 單一資產失敗不中斷整體執行，並完整記錄至 actions_log。
    """

    @staticmethod
    def _pre_check(files: List[File]) -> Optional[str]:
        for f in files:
            p = Path(f.path)
            if not p.exists():
                return f"檔案不存在：{f.path}"
            try:
                stat = p.stat()
            except OSError as e:
                return f"無法讀取檔案屬性：{e}"
            if stat.st_size != f.size or abs(stat.st_mtime - f.mtime) > MTIME_TOLERANCE_S:
                return f"檔案已在外部被修改（大小或修改時間不一致），為安全起見不刪除：{f.path}"
        return None

    def execute_deletion(
        self,
        session: Session,
        asset_ids: Optional[List[int]] = None,
        mode: str = DeletionMode.TRASH.value
    ) -> Dict[str, Any]:
        from app.services.rules_engine import rules_engine

        if mode not in (DeletionMode.TRASH.value, DeletionMode.PERMANENT.value):
            raise ValueError(f"不支援的刪除模式：{mode}")
        batch_id = uuid.uuid4().hex[:12]
        query = session.query(Asset).filter(Asset.status == AssetStatus.QUEUED.value)
        if asset_ids:
            query = query.filter(Asset.id.in_(asset_ids))
        assets_to_delete = query.all()

        # 鐵則 3：最後一道 Keeper 防線
        protected = rules_engine.protect_deletion_batch(session, [a.id for a in assets_to_delete])
        protected_items = []
        if protected:
            for a in assets_to_delete:
                if a.id in protected:
                    a.status = AssetStatus.CANDIDATE.value
                    protected_items.append({
                        "asset_id": a.id, "primary_path": a.primary_path,
                        "reason": "群組中最後一張仍保留的相片，已自動移出待刪清單",
                    })
            session.query(Candidate).filter(Candidate.asset_id.in_(protected)).update(
                {"state": "candidate"}, synchronize_session=False)
            assets_to_delete = [a for a in assets_to_delete if a.id not in protected]

        success_count = 0
        failed_count = 0
        deleted_size_bytes = 0
        failed_items = []
        for asset in assets_to_delete:
            files = session.query(File).filter(File.asset_id == asset.id).all()
            error_reason = self._pre_check(files)
            if error_reason:
                failed_count += 1
                failed_items.append({"asset_id": asset.id, "primary_path": asset.primary_path,
                                     "reason": error_reason})
                continue
            asset_delete_ok = True
            for f in files:
                try:
                    if mode == DeletionMode.PERMANENT.value:
                        os.remove(f.path)
                    else:
                        send2trash.send2trash(f.path)
                    session.add(ActionLog(
                        batch_id=batch_id, asset_id=asset.id, action=ActionType.DELETE.value,
                        mode=mode, original_path=f.path, new_path=None, at=datetime.utcnow(),
                    ))
                except Exception as e:
                    asset_delete_ok = False
                    error_reason = f"刪除失敗 ({f.path})：{e}"
                    break
            if asset_delete_ok:
                asset.status = AssetStatus.DELETED.value
                deleted_size_bytes += asset.size_total
                success_count += 1
                session.query(Candidate).filter(Candidate.asset_id == asset.id).update(
                    {"state": "deleted"}, synchronize_session=False
                )
            else:
                failed_count += 1
                failed_items.append({"asset_id": asset.id, "primary_path": asset.primary_path,
                                     "reason": error_reason})
        session.commit()
        return {
            "batch_id": batch_id,
            "total_requested": len(assets_to_delete) + len(protected_items),
            "success_count": success_count,
            "failed_count": failed_count,
            "protected_count": len(protected_items),
            "deleted_size_bytes": deleted_size_bytes,
            "failed_items": failed_items,
            "protected_items": protected_items,
        }


deletion_service = DeletionService()
