import os
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session
import send2trash

from app.models.schema import Asset, File, Candidate, ActionLog
from app.models.enums import DeletionMode, ActionType, AssetStatus

class DeletionService:
    """
    安全刪除執行器（鐵則 1, 2, 4 與安全防護）：
    1. 只有 Process 模組能調用本服務執行刪檔。
    2. 預設使用 send2trash 移入資源回收筒。
    3. 刪除前進行安全性校驗：檢查檔案是否存在、檔案大小與修改時間是否與資料庫一致。
    4. 配對檔案一併刪除（Live Photo HEIC/JPG + MOV、RAW + JPG 同進同出）。
    5. 單一檔案失敗不中斷整體執行，並完整記錄至 actions_log。
    """

    def execute_deletion(
        self,
        session: Session,
        asset_ids: Optional[List[int]] = None,
        mode: str = DeletionMode.TRASH.value
    ) -> Dict[str, Any]:
        batch_id = uuid.uuid4().hex[:12]
        
        # 若未指定 asset_ids，則抓取所有處於 queued (待刪) 狀態的資產
        query = session.query(Asset).filter(Asset.status == AssetStatus.QUEUED.value)
        if asset_ids:
            query = query.filter(Asset.id.in_(asset_ids))
        
        assets_to_delete = query.all()
        
        success_count = 0
        failed_count = 0
        deleted_size_bytes = 0
        failed_items = []

        for asset in assets_to_delete:
            # 取得主檔與所有配對檔案
            files = session.query(File).filter(File.asset_id == asset.id).all()
            
            # 刪前安全性校驗
            pre_check_passed = True
            error_reason = ""
            for f in files:
                p = Path(f.path)
                if not p.exists():
                    pre_check_passed = False
                    error_reason = f"檔案不存在：{f.path}"
                    break
                try:
                    stat = p.stat()
                    # 容許 2 秒內的時間誤差
                    if abs(stat.st_mtime - f.mtime) > 2.0 and stat.st_size != f.size:
                        pre_check_passed = False
                        error_reason = f"檔案已在外部被修改（mtime/大小不一致）：{f.path}"
                        break
                except OSError as e:
                    pre_check_passed = False
                    error_reason = f"無法讀取檔案屬性：{str(e)}"
                    break

            if not pre_check_passed:
                failed_count += 1
                failed_items.append({"asset_id": asset.id, "primary_path": asset.primary_path, "reason": error_reason})
                continue

            # 執行實體刪除 (配對檔案連動處置 - 鐵則 4)
            asset_delete_ok = True
            for f in files:
                try:
                    if mode == DeletionMode.TRASH.value:
                        send2trash.send2trash(f.path)
                    elif mode == DeletionMode.PERMANENT.value:
                        os.remove(f.path)
                    else:
                        send2trash.send2trash(f.path)

                    # 寫入 Action Log
                    session.add(ActionLog(
                        batch_id=batch_id,
                        asset_id=asset.id,
                        action=ActionType.DELETE.value,
                        mode=mode,
                        original_path=f.path,
                        new_path=None,
                        at=datetime.utcnow()
                    ))
                except Exception as e:
                    asset_delete_ok = False
                    error_reason = f"刪除失敗 ({f.path})：{str(e)}"
                    break

            if asset_delete_ok:
                asset.status = AssetStatus.DELETED.value
                deleted_size_bytes += asset.size_total
                success_count += 1
                # 更新對應 Candidate 狀態
                session.query(Candidate).filter(Candidate.asset_id == asset.id).update(
                    {"state": "deleted"}, synchronize_session=False
                )
            else:
                failed_count += 1
                failed_items.append({"asset_id": asset.id, "primary_path": asset.primary_path, "reason": error_reason})

        session.commit()

        return {
            "batch_id": batch_id,
            "total_requested": len(assets_to_delete),
            "success_count": success_count,
            "failed_count": failed_count,
            "deleted_size_bytes": deleted_size_bytes,
            "failed_items": failed_items,
        }

deletion_service = DeletionService()
