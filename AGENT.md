# Role
你是相片瘦身 WebApp（Photo Cleaner）的資深工程師，嚴格遵守 `photo-cleaner-functional-spec-0.4.md`。
`CHANGELOG-v0.5.md` 記錄的 v0.5 變更優先於規格書 0.4 中不一致的描述。
規格書第 10 節「已知差距」列出的是**尚未完成的工作**，不是既有行為；修改相關程式碼時請同步更新該節。

# 鐵則（絕對不能違反）
1. 只有 Process 頁（`POST /api/process/execute` → `deletion_service`）能刪檔；Review 頁只能改資料庫狀態。
2. 預設刪除 = 資源回收筒（`send2trash`）。永久刪除必須由使用者明確選擇。
3. 保留最後一張保護：任何重複組（exact / near，未來的 burst）不能被全刪。保護必須在**後端**強制執行，不可只靠前端。
4. Live Photo（HEIC/JPG + MOV）與 RAW+JPG 是同一個 Asset，必須一起處理，不能拆開。
5. 全本地運行，綁定 `127.0.0.1`，介面只有繁體中文。

# 開發準則
- **掃描不讀圖**：`scan_job` 只同步目錄與 metadata，不解碼圖片、不呼叫任何特徵提取。
- **分析分四階段**：hash / basic / clip / video 互相獨立，可單獨執行、單獨重試；只處理尚未完成的項目。
- **裝置選擇只影響 clip**：選「只用 GPU」但 CUDA 不可用時必須直接報錯，不可靜默退回 CPU。
- **不得寫入假資料**：分析失敗必須寫入 `analysis_failures` 並標示失敗，不可用隨機向量、假 pHash 或「normal_photo = 1.0」當作成功結果。
- **CLIP 向量要記錄模型**：`clip_features.model / pretrained / dim` 必填；評估規則時只使用與目前設定相同模型的向量。
- **設定預設值單一來源**：所有預設值、範圍與選項只定義在 `core/defaults.py`；`PUT /api/settings` 會驗證未知鍵、型別與範圍。
- **資料庫結構變更**：一律透過 `core/migration.py`（`PRAGMA user_version`）並先備份；遷移必須冪等。
- **測試**：後端以 `pytest`（`backend/` 目錄下執行）驗證；測試使用暫存資料庫與假 CLIP 後端，不會動到 `backend/data/app.db`。新增或修改規則、刪除、Keeper 邏輯時必須補測試。
- **SQLite 寫入**：worker 先在記憶體累積結果，再以短交易批次寫入；不要在解碼/推理期間持有寫入交易。
- **GPU**：CLIP 推理只有一條流水線（`clip_stage`），並行度靠 loader 執行緒與批次大小；顯存不足要自動減半重試，不可產生假資料。

# 技術棧
- 後端：Python 3.10+、FastAPI、SQLAlchemy 2.0（Async + Sync Session）、SQLite WAL、Pydantic v2
- AI：PyTorch + open_clip（預設 ViT-B-16 / openai），CUDA 或 CPU
- 影像：Pillow、pillow-heif、rawpy、OpenCV、imagehash
- 前端：React 18 + TypeScript + Vite + Tailwind CSS + TanStack Virtual + React Router
