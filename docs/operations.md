# 營運手冊：安裝細節、設定、效能與測試

給要把相片清理助手跑起來、調校、備份或測試的人。系統行為的定義在 [`functional-spec.md`](functional-spec.md)；最短的啟動步驟在 [`README.zh-TW.md`](../README.zh-TW.md)。

## 1. 環境

- Python 3.10+、Node.js 18+，在 Windows（PowerShell）開發與測試。
- 後端：`uvicorn app.main:app --host 127.0.0.1 --port 8000`（不要綁定其他位址）。API 文件在 `http://127.0.0.1:8000/docs`。
- 前端：`npm run dev`（`http://127.0.0.1:5173`，`/api` 代理到 8000）；`npm run build` 會一併做 TypeScript 型別檢查。

## 2. 設定

### 2.1 環境變數

寫在 `.env`（從啟動目錄讀取，一般是 `backend/.env`；定義於 `app/core/config.py`）。

| 變數 | 預設值 | 說明 |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8000` | 只供 `/api/health` 回報；實際綁定以 uvicorn 參數為準 |
| `DEBUG` | `false` | SQLAlchemy echo |
| `DATA_DIR` | `backend/data` | 資料目錄 |
| `THUMBS_CACHE_DIR` | `backend/thumbs_cache` | 縮圖快取，建議放 SSD |
| `DATABASE_URL` / `SYNC_DATABASE_URL` | `sqlite(+aiosqlite):///backend/data/app.db` | 要搬移資料庫時**兩個都要改**；遷移前備份會依此路徑 |
| `SQLITE_BUSY_TIMEOUT_MS` | `30000` | 多執行緒寫入時等待鎖的時間 |
| `HASH_RUNNERS` / `BASIC_RUNNERS` / `CLIP_RUNNERS` / `VIDEO_RUNNERS` | CPU 核心數 // 2（1–16） | 各階段並行數；設定頁的值會覆寫 |
| `CLIP_BATCH_SIZE` | `0`（自動） | 見 4.2；設定頁的值會覆寫 |

### 2.2 執行時設定

偵測規則、門檻、CLIP 模型、運算裝置、各階段並行數存在 SQLite 的 `settings` 表，在「系統設定」頁調整。**預設值、範圍與選項只定義在 `backend/app/core/defaults.py`**；要查某個設定的預設值，看那個檔案或 `GET /api/settings/meta`，不要看文件。

## 3. GPU

- 「資料庫掃描」頁的「一鍵啟用 GPU 加速」會依顯示卡與驅動挑選 PyTorch 的 CUDA 版本並安裝（RTX 50 系列一律使用 `cu128`）。
- 若仍是 CPU 版 PyTorch，可手動安裝（RTX 50 系列如 RTX 5080 需要 CUDA 12.8 版 wheel）：

  ```powershell
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
  ```

- 分析時可選「自動 / 只用 GPU / 只用 CPU」。選「只用 GPU」但 CUDA 不可用時，工作會直接失敗並說明原因，不會退回 CPU。
- 已實機驗證 GTX 1650 Ti（fp32 通常比 fp16 快，程式會自動實測選擇；可用環境變數 `PC_CLIP_PRECISION` 強制指定）與 RTX 5080。
- 更換 CLIP 模型後，舊向量會標為「過期」，重跑 clip 階段即可更新。

## 4. 效能

### 4.1 一般建議

- 把 `THUMBS_CACHE_DIR` 放在本機 SSD。
- **先跑 basic 再跑 clip**：basic 會產生縮圖快取，clip 之後就不需要再讀 NAS 上的原檔。
- 調高執行緒數前，確認 SQLAlchemy 連線池（`database.py`）足夠；SQLite 同時只允許一個寫入者，所以並行數不是越高越好。
- hash 階段是 I/O 密集，NAS 上不宜設太高。

### 4.2 CLIP

- `clip_runners` 是**讀圖執行緒數**，GPU 推理固定只有一條流水線。執行緒太少時 GPU 會等圖；夠多之後瓶頸是 GPU，再加沒有幫助。可從 2–4 開始試，以分析時每秒張數為準。
- `clip_batch_size = 0` 時依顯存自動決定：顯存 ≥ 12GB 用 128、≥ 6GB 用 64、其餘用 32；CPU 用 16。顯存不足時會自動減半並重試同一批。
- 測速（不寫資料庫、不影響現有資料）：

  ```powershell
  cd backend
  python scripts/bench_clip.py <相片資料夾> --limit 2000
  ```

  輸出讀圖速度、純推理速度、峰值顯存與 10 萬張的預估時間。

## 5. NAS

- `send2trash` 在網路磁碟 / UNC 路徑上通常**不會**進 Windows 資源回收筒，檔案可能直接被刪除。是否會進 NAS 自己的回收筒，取決於該分享資料夾的設定。**請先用一張測試檔案確認**。
- NAS 上的安全刪除（隔離資料夾 + 還原、配對檔刪到一半的處理）尚未完成；在此之前不建議用「執行刪除」頁刪除 NAS 上的相片。
- 會讀取 NAS 原檔的時機：
  - **basic** 階段：每張相片只解碼一次，同時寫縮圖與計算特徵；
  - **hash** 階段：只讀大小重複的檔案；
  - **預覽大圖**：只在你開啟預覽時，讀那一張；瀏覽器能直接顯示的格式（JPG / PNG / WebP…）直接回傳原檔。
- **clip** 階段讀本地縮圖快取，不讀 NAS。

## 6. 資料與備份

- 資料庫在 `backend/data/app.db`（預設位置）。它存的是掃描與分析結果、**你的審核決定（待刪 / 已排除）**與清理歷史。刪掉它不會影響相片本身，但這些決定與歷史會消失，需要重新掃描與分析。
- 資料庫結構升級（`core/migration.py`）會在遷移前先在同一個資料夾備份；全新資料庫不備份。
- 縮圖快取（`thumbs_cache/`）可以隨時刪除，之後用「補算縮圖」重建。
- 復原紀錄只存在記憶體，後端重新啟動後失效。

## 7. 測試

```powershell
cd backend
pytest                                          # 真實 CLIP 測試預設略過（顯示為 skipped）
$env:PC_REAL_CLIP="1"; pytest -m real_clip -s   # 真實模型煙霧測試（會下載約 600MB）

cd ..\frontend
npm run build                                   # 含 TypeScript 型別檢查
```

- 後端測試使用暫存資料庫與假 CLIP 後端，**不會**動到 `backend/data/app.db`。
- **功能測試**用已知答案的測試相片庫：

  ```powershell
  cd backend
  python scripts/make_test_library.py E:\PCTest                       # 合成模式（不需要任何相片）
  python scripts/make_test_library.py E:\PCTest --mixed E:\MyPhotos   # 混合模式：複製你的相片（建議 600 張以上），原檔不會被改動
  $env:PC_FUNC_LIB="E:\PCTest"; pytest tests\test_functional.py -v
  ```

  測試會暫時修改或刪除測試相片庫中的少數檔案，結束時自動還原；它也會把 Live Photo 配對檔送進 Windows 資源回收筒再還原（回收筒內的副本可以清空）。每次執行會在測試相片庫根目錄寫入 `functional_report.json`。
- 合成的測試相片庫也適合拿來截圖，不會把個人相片放進文件或 repo。

## 8. 疑難排解

| 現象 | 原因與處理 |
|---|---|
| 分析選「只用 GPU」直接失敗 | CUDA 不可用（常見是裝了 CPU 版 PyTorch）。用「一鍵啟用 GPU 加速」，或手動安裝對應的 CUDA 版（見第 3 節） |
| 設定儲存回 409 | 有任務進行中，而你變更的設定會影響評估。等任務完成，或選「只儲存」稍後再評估；只改執行緒數 / 批次大小的話不會遇到 |
| 啟動任務回 400（目前已有任務進行中） | 任一時間只允許一個任務，等目前任務完成或取消 |
| 「復原」按鈕失效或回 404 | 復原紀錄在記憶體，後端重新啟動後消失；或該筆已經復原過 |
| 預覽顯示「無法顯示這張相片」 | 檔案已被移動或刪除，或所在的磁碟（NAS）離線；後端會回報具體原因 |
| 審核頁數量和首頁不同 | 「全部」預設隱藏建議保留的相片；打開「顯示建議保留的相片」會多出那部分，標題會註明 |
| 加入待刪後「執行刪除」仍留下一張 | 已知差距（規格書 G-3）：刪除前的最後一道防線尚未配合螢幕截圖 / 文件的保護豁免 |
