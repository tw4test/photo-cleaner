# Photo Cleaner v0.6.1 – 設定頁重新設計

## 安裝
- 停止後端與前端 dev server。
- 把壓縮檔解壓到專案根目錄並覆蓋（只含有變更的檔案）。
- cd backend → pytest
- cd frontend → npm run build

## 變更
- `frontend/src/pages/Settings.tsx`：重寫。分成「AI 內容分類」「重複與連拍」「畫質」三個區塊，加上收合的「進階：分析效能」。
  - 新增：近似重複比對方式（near_dup_method）、clip_dup_threshold、連拍（開關 / 時間視窗 / 相似度）、clip_batch_size。
  - 隱藏（使用預設值）：刪除模式、保留者排序、跨路徑重複、近似群組上限、影片規則、CLIP 模型、運算裝置、auto_evaluate_on_save。
  - 未儲存變更提示、放棄變更、只送出有變更的項目、顯示後端回傳的真正錯誤、每個欄位可還原為預設值。
  - 模糊門檻旁提示「已改在 1024px 上計算，預設值尚未校準」。
- `frontend/src/services/settingsApi.ts`：新增（型別與設定 API；不修改 api.ts / types.ts）。
- `backend/app/api/settings.py`：儲存「依需要才評估」。
  - 只寫入有變更的項目；沒有變更就什麼都不做。
  - 只改效能類設定（runners、批次大小）→ 不評估、不佔任務鎖，分析進行中也能儲存。
  - 改了會影響評估的設定 → 評估（任務進行中仍回 409，且不寫入任何設定）。
  - `re_evaluate=false` → 只寫入，之後手動評估。
  - 還原預設：只有被還原的設定會影響評估時才評估。
  - 新增 `GET /api/settings/meta`：預設值、範圍、選項、不影響評估的設定鍵。
- `backend/app/core/settings_meta.py`：新增。`NON_EVALUATION_KEYS`：新增設定時預設視為「影響評估」。
- `backend/tests/test_settings_need_based.py`：新增。

## 行為變更
- PUT /api/settings 回傳多了 `changed`、`evaluated` 欄位。
- 值沒有變的 PUT 不再檢查任務、不再評估（原本一律評估）。
