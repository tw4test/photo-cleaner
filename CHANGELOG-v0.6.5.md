# Photo Cleaner v0.6.5 – 審核工作台：大圖預覽

## 安裝
- 停止後端與前端 dev server，把壓縮檔解壓到專案根目錄並覆蓋。
- `cd backend` → `pytest tests\test_preview_service.py tests\test_preview_api.py -v`，再跑完整 `pytest`
- `cd frontend` → `npm run build`

## 新功能：預覽大圖
- 開啟方式：雙擊卡片、縮圖右上角的放大鏡（滑鼠移到卡片上才會出現）、或按空白鍵 / Enter（預覽最後一次點選的那張）。
- 預覽裡：
  - `←` `→` 沿著審核清單切換；快到清單尾端時自動載入下一頁；前後幾張會先載入。
  - `D` / `K` / `R`（或下方按鈕）直接決定這一張，成功後自動跳到下一張；被「群組至少保留一張」擋下時留在原地並說明原因。
    只動這一張，不會影響你已經選取的其他卡片；`Ctrl+Z` 在預覽裡也能復原。
  - 點圖或按 `F`：適合視窗 ↔ 100%（100% 時可捲動檢視細節）；換一張會回到適合視窗。
  - `I`：顯示 / 隱藏右側資訊欄（會記住）；`Esc` 關閉。
  - 資訊欄：檔名、路徑、大小、解析度、拍攝時間、偵測原因與分數、重複群組的所有成員（縮圖、大小、解析度、清晰度、保留 / 待刪標記）。
    點群組裡的相片可以切換預覽來比較畫質；成員不在目前清單時為「比對中」（只能看，不能操作），`←` `→` 仍從原本的清單位置繼續。
  - 關閉預覽後，清單會捲到你最後看的那一張。
- 大圖載入失敗時顯示後端的原因（檔案不存在 / 磁碟離線 / 無法解碼）。
- HEIC / TIFF / RAW / 影片會被縮小顯示（最長邊 2560px；影片只看第一幀），並有提示，不是原始解析度。

## 後端
- `GET /api/candidates/preview/{asset_id}`（新，`api/preview.py`）：
  - jpg / png / webp / gif / bmp：原檔直接回傳（完整畫質）。
  - 其他格式與影片：解碼後縮成最長邊 2560px 的 JPEG，放在記憶體 LRU（12 張）。
  - RAW + JPG：優先用伴生 JPG。
  - 路徑一律來自資料庫（只接受 asset id）；檔案不存在 404、無法解碼 422。
  - 帶 `?v=mtime` 時長快取，沒帶時 5 分鐘。
- `GET /api/candidates/group/{group_id}`（新）：群組成員（含各種狀態，Keeper 排最前面，其餘依清晰度、大小）。
- `services/preview_service.py`（新）、`api/preview.py`（新）。
- `api/candidates.py`：整檔取代，**既有程式碼沒有改動**，只在結尾加上 `router.include_router(preview.router)`。

## 前端
- `components/review/PreviewModal.tsx`（新）、`components/review/ReviewCard.tsx`（加放大鏡與雙擊）、
  `pages/Review.tsx`（預覽的狀態與操作）、`services/reviewApi.ts`（`group()`、`previewUrl()`、型別）。
- 通知與「復原」現在顯示在預覽的上層（預覽開著時提高位置，不會蓋住下方的操作按鈕）。

## 尚未處理
- 並排比較兩張（目前是在群組成員之間切換）、滾輪縮放 / 拖曳平移、影片播放。
- 「執行刪除」頁最後一道防線（`services/deletion.py`）仍會為每個群組擋下一張，尚未配合「螢幕截圖 / 文件不受 Keeper 保護」。
- README 還沒更新到 v0.6.5（快速鍵表、已知限制、API 表）。
