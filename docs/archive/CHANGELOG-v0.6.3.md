# Photo Cleaner v0.6.3 – 審核工作台（Phase 1 + 2）

## 安裝
- 先確認 v0.6.1 的 `frontend/src/services/settingsApi.ts` 已在專案裡（Review 用它的 ApiError）。
- 停止後端與前端 dev server，把壓縮檔解壓到專案根目錄並覆蓋。
- `cd backend` → `pytest tests\test_review_api.py tests\test_review_logic.py -v`，再跑完整 `pytest`
- `cd frontend` → `npm run build`

## 後端
- `api/candidates.py`（整檔取代；舊的 GET /api/candidates、POST /evaluate 行為不變）
  - 新 `GET /api/candidates/summary?state=`：各狀態與各分類的張數 / 容量 / Keeper 數（不重複資產）。
  - 新 `GET /api/candidates/page`：分頁（category、state、sort=score|size|path|group、min_score、keepers=show|hide|only、offset、limit≤1000）。
  - `POST /api/candidates/batch-status`：多回傳 `undo_token`、`updated_asset_ids`；新增選填 `waive_protection_for`（'screenshot' | 'document'）。
    狀態變更改為每 500 筆一塊（避免 SQLite 變數上限），小於 500 筆時行為與以前相同。
  - 新 `POST /api/candidates/bulk-status`：依條件批次。`dry_run=true` 回傳張數、容量、隨機抽樣；實際執行必須帶 `expected_count`，不符回 409，什麼都不寫。永遠不動 Keeper。
  - 新 `POST /api/candidates/undo`：復原一次狀態變更（只還原仍處於變更後狀態的相片）。復原紀錄存在記憶體，後端重新啟動後失效。
- `services/review_service.py`、`services/review_logic.py`：新增（資料庫存取 / 純邏輯）。
- 螢幕截圖、文件不受「群組至少保留一張」保護（使用者決定）：
  - 只在請求帶 `waive_protection_for`、或在 bulk 的 category 為 screenshot / document 時生效，且只對有該規則候選記錄的相片生效。
  - 豁免的相片先寫入，其餘再交給 `rules_engine.filter_queue_requests`，所以非豁免的成員仍會被擋下「最後一張」。

## 前端
- `pages/Review.tsx`：重寫。
  - 一次只載入 200 張，捲到底自動載入下一頁（100K 筆也只有少量 DOM）。
  - 側邊欄顯示每個分類的張數與容量；新增「連拍」「影片」分類；狀態頁籤顯示張數。
  - 狀態變更直接更新畫面（不重新載入、不丟失捲動位置），底部「復原」（Ctrl+Z，12 秒內）。
  - Shift 範圍選取、Ctrl+A、D / K / R / Esc；選取列顯示張數與可釋放容量。
  - 「選取符合條件的全部 N 張」→ 預覽（張數、容量、隨機 12 張縮圖）→ 確認 → 執行；選取超過 200 張也會先確認。
  - 排序、顯示建議保留的相片、AI 分類的分數門檻、卡片大小（3–8 欄）。
  - 卡片顯示分數、解析度、拍攝日期、群組大小，完整原因放在 tooltip。
- `components/review/`：`ReviewCard.tsx`、`ConfirmDialog.tsx`、`reviewMeta.ts`；`services/reviewApi.ts`。

## 行為變更
- 預設只有重複類別（完全重複 / 近似重複 / 連拍）顯示 Keeper；「全部候選」預設隱藏，數量才會和側邊欄、首頁一致。
- 清單顯示 Keeper 時，標題會註明「含 N 張建議保留」。
- Phase 3（群組並排檢視、切換 Keeper、大圖預覽、方向鍵游標）尚未做。

## 尚未處理
- 「執行刪除」頁最後一道防線（services/deletion.py）仍會在刪除前為每個群組擋下一張並移回候選；
  豁免只在加入待刪時生效。需要看 deletion.py 才能一併調整。
