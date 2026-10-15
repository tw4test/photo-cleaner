# AGENT.md — 相片清理助手（Photo Cleaner）

## 角色

你是相片清理助手（Photo Cleaner）的資深工程師。系統**應該**怎麼運作以 [`docs/functional-spec.md`](docs/functional-spec.md) 為準；各版本改了什麼見 [`CHANGELOG.md`](CHANGELOG.md)；部署、設定與調校見 [`docs/operations.md`](docs/operations.md)。
規格書第 10 節「已知差距」列出的是**尚未完成的工作**，不是既有行為；修改相關程式碼時請同步更新該節。

## 先讀什麼

| 你要做的事 | 先讀規格書 |
|---|---|
| 刪除、Keeper、群組保護 | SF-01 至 SF-06、PR-、RV-30、RV-31 |
| 掃描 / 分析 / CLIP | SC-、AN- |
| 規則與評估 | EV-（尤其 EV-09 評估指紋） |
| 審核頁、預覽 | RV-、PV-、UI- |
| 設定頁與設定 API | ST-（尤其 ST-04） |
| 資料表 / 遷移 | 第 5 節、架構決策 #9 |

## 鐵則（絕對不能違反；細節與編號見規格書第 7 節）

- **SF-01** 只有 Process 頁（`POST /api/process/execute` → `deletion_service`）能刪檔；審核、預覽、設定、歷史只能改資料庫狀態。
- **SF-02** 預設刪除 = 資源回收筒（`send2trash`）。永久刪除必須由使用者明確選擇並輸入確認文字；`quarantine` 尚未實作，後端拒絕。
- **SF-03** 保留最後一張：任何重複群組（exact / near / burst）不能被全刪，保護必須在**後端**強制執行，不可只靠前端。**唯一例外**：螢幕截圖與文件（RV-31），只在請求明確帶 `waive_protection_for` 時生效。
- **SF-04** Live Photo（HEIC/JPG + MOV）與 RAW + JPG 是同一個 Asset，必須一起處理，不能拆開。
- **SF-05** 刪除前即時校驗檔案存在、大小與修改時間。
- **SF-06** 分析失敗寫入 `analysis_failures`，不可用隨機向量、假 pHash 或 `normal_photo = 1.0` 當作成功。
- **SF-10** 全本地運行，綁定 `127.0.0.1`，介面只有繁體中文。

## 開發準則

- **掃描不讀圖**：`scan_job` 只同步目錄與 metadata，不解碼圖片、不呼叫任何特徵提取。
- **分析分四階段**：hash / basic / clip / video 互相獨立，可單獨執行、單獨重試；只處理尚未完成的項目。
- **裝置選擇只影響 clip**：選「只用 GPU」但 CUDA 不可用時必須直接報錯，不可靜默退回 CPU。
- **CLIP 向量要記錄模型**：`clip_features.model / pretrained / dim` 必填；評估只使用與目前設定相同模型的向量。
- **設定預設值單一來源**：所有預設值、範圍與選項只定義在 `backend/app/core/defaults.py`；`PUT /api/settings` 驗證未知鍵、型別與範圍。前端不重複定義，從 `/api/settings/meta` 取得。
- **資料庫結構變更**：一律透過 `core/migration.py`（`PRAGMA user_version`）並先備份；遷移必須冪等。
- **SQLite 寫入**：worker 先在記憶體累積結果，再以短交易批次寫入；不要在解碼 / 推理期間持有寫入交易；以資產 id 做 `IN` 查詢時每 500 筆分塊。
- **GPU**：CLIP 推理只有一條流水線（`clip_stage`），並行度靠 loader 執行緒與批次大小；顯存不足要自動減半重試，不可產生假資料。
- **審核分頁在後端**：前端不一次載入全部候選；狀態變更直接更新畫面，不重新載入整份清單。

## 常見陷阱

- **新增設定時**要決定它是否影響評估。預設視為「影響」；若確定不影響，要同時更新 `core/settings_meta.NON_EVALUATION_KEYS` 與 `services/evaluation_state._IGNORED_SETTINGS`（兩份目前不一致：`compute_device`，見 G-8）。
- 螢幕截圖 / 文件的保護豁免**只在加入待刪時生效**；`services/deletion.py` 刪除前的最後一道防線尚未配合（G-3）。修改任一邊時請一起看。
- `api/candidates.py` 把 `api/preview.py` 的 router 掛在自己底下（預覽與群組成員）。測試路由是否存在時，用 `app.openapi()["paths"]`，不要遍歷 `app.routes`（新版 FastAPI 的 `include_router` 物件沒有 `.path`）。
- 前端頁面**自己負責捲動**（`h-full min-h-0 overflow-y-auto`），不要假設 `AppShell` 的 `<main>` 會替頁面捲動。
- `backend/app/stubs/` 的名稱沿用歷史命名，內容是**真實實作**：`clip_stub`（open_clip）、`raw_stub`（rawpy）、`video_probe_stub`（OpenCV）、`vector_stub`（餘弦相似度索引）。
- 測試用暫存資料庫與假 CLIP 後端，不會動到 `backend/data/app.db`；不要在測試裡讀寫真實資料目錄。
- 在 Windows 開發：路徑比對不分大小寫，指令用 PowerShell。

## 技術棧

- 後端：Python 3.10+、FastAPI、SQLAlchemy 2.0（Async + Sync Session）、SQLite WAL、Pydantic v2
- AI：PyTorch + open_clip（預設 ViT-B-16 / openai），CUDA 或 CPU
- 影像：Pillow、pillow-heif、rawpy、OpenCV、imagehash
- 前端：React 18 + TypeScript + Vite + Tailwind CSS + TanStack Virtual + React Router

## 指令

```powershell
cd backend;  pytest                      # 後端測試（真實 CLIP 測試預設略過）
cd frontend; npm run build               # 含 TypeScript 型別檢查；改前端後必跑
```

功能測試、真實模型測試、效能測速見 [`docs/operations.md`](docs/operations.md)。新增或修改規則、刪除、Keeper 邏輯時**必須補測試**。

## 目錄（層級）

```
backend/app/{api,core,models,services,stubs}   API 路由 / 設定與資料庫 / 資料表 / 業務邏輯 / 影像與模型後端
backend/{tests,scripts}                         pytest / 測速與測試相片庫腳本
frontend/src/{pages,components,hooks,services}  頁面 / 元件 / hooks / API 與型別
docs/                                           功能規格、營運手冊、舊文件封存（archive/）、截圖（images/）
```

## 改完之後

1. 行為有變動 → 更新規格書對應的需求編號（沒有編號就新增一條），不要只改程式。
2. 在 `CHANGELOG.md` 最上面加一筆（版本、日期、重點）。
3. 新增 / 完成已知差距 → 更新規格書第 10 節。
4. 改了名稱、快速鍵或使用者看得到的流程 → 檢查 `README.md`、`README.zh-TW.md`。
5. 不要把預設值、版本號、測試數量寫進文件；它們各有唯一的來源（`defaults.py`、`CHANGELOG.md`、測試輸出）。
