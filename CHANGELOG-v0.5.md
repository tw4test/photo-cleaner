# Photo Cleaner v0.5 – 變更紀錄（2026-10-14）

對應 `photo-cleaner-fix-plan.md` 的 Phase 0–3，以及部分 Phase 5、6。
問題編號（C1、H2、M3…）對應 `photo-cleaner-code-review.md`；G-x 對應規格書 0.4 第 10 節。
本檔記錄的行為優先於規格書 0.4 中不一致的描述。

## 安裝方式
1. 停止後端。
2. 把壓縮檔解壓到專案根目錄並覆蓋（只含有變更的檔案）。
3. 執行 `pip install -r requirements.txt`（沒有新增套件）。
4. 若 `backend/data/app.db` 是舊版分析留下的，**建議直接刪掉**（之前沒有實際分析過，重新掃描即可）。
5. `cd backend` → `pytest`（72 項通過、1 項真實模型測試預設略過）。

---

## 1. 資料安全（刪除相關）
| 項目 | 變更 | 檔案 |
|---|---|---|
| C1 | 所有規則排除 `missing` 資產；遺失的檔案不可能成為 Keeper | `services/rules_engine.py` |
| C2 | Keeper 保護改為**以資產為單位**檢查它所屬的每個群組，三種繞過方式都已封住：先排 Keeper 再排其他、用代表卡片（例如 blur）的 ID、重新評估後整組待刪 | `services/rules_engine.py`、`api/candidates.py` |
| C2 | 刪除前最後一道防線：若這批刪除會讓某群組一張不剩，自動擋下一張（優先 Keeper）並移回候選，回應中列出 `protected_items` | `services/deletion.py` |
| C3 | 刪前校驗：大小**或**修改時間任一不符就不刪（原本兩者都不符才擋） | `services/deletion.py` |
| C6 | 永久刪除必須帶 `confirm_text: "永久刪除"`；分析或評估進行中不能刪除 | `api/process.py` |
| — | `quarantine` 模式在後端明確拒絕（尚未實作，見下方「尚未處理」） | `services/deletion.py` |

## 2. 不再寫入假資料
| 項目 | 變更 |
|---|---|
| H1 | CLIP 任何錯誤都寫入 `analysis_failures`，不再產生隨機向量 + `normal_photo=1.0` |
| H2 | 壞圖不再以檔名 MD5 冒充 pHash；`decode_status='error'`、特徵清空、寫入失敗紀錄，統計顯示為「失敗」，可用「重試失敗」 |
| M11 | OpenCV 打不開的影片記為失敗，不再寫入欄位全空的 `video_info` |
| M12 | clip / video 階段遇到檔案不存在時記為失敗（原本靜默略過、永遠待處理） |
| M1 | 移除 `~/.cache/clip/*.pt` 自製載入（以錯誤架構 + `strict=False` 靜默載入） |

## 3. GPU 批次 CLIP 管線（主要目標）
- 新 `services/clip_stage.py`：loader 執行緒（數量 = `clip_runners`）讀 320px 縮圖並前處理 → 單一推理迴圈以 fp16 批次 forward → 背景 writer 執行緒每批一次 bulk upsert。
- 批次大小：設定 `clip_batch_size`（0 = 自動：≥12GB 顯存 128、≥6GB 64、其餘 32；CPU 16）。
- 顯存不足（OOM）自動把批次減半並重試同一批；非 OOM 的批次錯誤會逐張重跑，只讓有問題的那張失敗。
- M2：前處理改為「補邊成正方形」，不再中心裁切，長截圖的狀態列會保留。
- 進度列顯示實際裝置與批次大小；結果包含 `images_per_sec`。
- 新 `stubs/clip_stub.py`：可替換後端（測試用假模型，不需 PyTorch）、文字特徵只算一次、`zs` 一律用英文鍵 `screenshot/document/meme/mistouch/normal_photo`。
- 新 `scripts/bench_clip.py`：不寫資料庫的測速工具，輸出讀圖速度、純推理速度、峰值顯存與 10 萬張預估時間。

## 4. basic 階段：每張只讀一次
- 原本同一張相片會讀兩次（縮圖一次、特徵一次）。現在解碼一次（JPEG 用 draft 直接低解析度解碼），依 EXIF 轉正後同時寫縮圖與計算特徵；RAW+JPG 直接用伴生 JPG。
- pHash / 模糊 / 亮度改在「最長邊 1024px」上計算，門檻在不同相機解析度之間一致，速度也快很多。**`blur_threshold` 的意義因此改變**，Phase 4 校準時會重新決定預設值。
- 拍攝時間改讀 EXIF 子 IFD 的 `DateTimeOriginal` + `SubsecTimeOriginal`（原本只讀到 `DateTime` 修改時間）；讀不到時用檔案 mtime 並標記 `_taken_at_source: "mtime"`（連拍規則不採用）。
- 結果先在記憶體累積，每 50 筆以短交易寫入，解碼期間不持有 SQLite 寫入鎖，多個 worker 才真正並行。

## 5. 近似重複 / 連拍（新規則引擎）
- 新 `services/similarity.py`：
  - CLIP 餘弦相似度分塊計算（有 CUDA 時在 GPU 上）。
  - pHash 以 multi-index hashing 找配對（10 萬張約 2 秒），不再 O(n²)。
  - union-find 正確合併群組（原本 A~B、C~D、B~C 會產生重疊群組）。
  - 群組超過 `near_dup_max_group`（預設 50）時逐步收緊門檻拆小。
- 新設定 `near_dup_method`：`clip` / `phash` / `both` / `either`（預設 `either`：沒有 CLIP 向量的相片仍可用 pHash 找）。新設定 `clip_dup_threshold`（預設 0.95）。
- G-1：連拍規則實作：拍攝時間在視窗內（預設 2 秒）且 CLIP 相似度 ≥ 0.85，保留最清晰的一張。
- G-2：影片的完全重複改用 `video_same_size` 規則；`video_tiny` 規則實作（預設關閉）。
- 每個資產只屬於一個群組（完全重複 → 近似 → 連拍，依序排除）。
- Keeper 排序：近似 = 路徑優先度 → 清晰度 → 解析度 → 大小；連拍 = 清晰度優先。

## 6. 任務控制與伺服器
| 項目 | 變更 |
|---|---|
| H5 | 「檢查是否有任務 + 開始」在 endpoint 內原子完成，連點兩次只會啟動一個；取消只設旗標，工作真的停下來才能啟動下一個 |
| H5 | SSE 改為 thread-safe（`call_soon_threadsafe`），每個訂閱者只保留最新狀態，節流約每秒 5 次（含 CUDA 安裝進度） |
| H4 | 縮圖、儀表板、設定、候選狀態、刪除、分析狀態等 endpoint 改為 `def`（在 threadpool 執行），不再阻塞 event loop / SSE |
| H4 | 設定頁儲存與重設的重新評估，改經由任務系統（有其他任務時回傳 409） |
| H6 | 只接受本機 Host（防 DNS rebinding）；拒絕其他網站來源的寫入請求；`install-cuda` 必須帶 JSON body |
| M5/M6 | 設定預設值集中在 `core/defaults.py`；`PUT /api/settings` 驗證未知鍵、型別、範圍與選項，全部通過才寫入 |
| M7/M8 | 兩個 engine 都套用 WAL / 外鍵 / `busy_timeout=30s`；連線池加大 |
| M10 | 掃描每個根目錄完成即 commit（同時修正兩個掃描並行時的 UNIQUE 衝突） |
| M13 | CUDA 安裝：新增 cu124 / cu126 / cu128，依 GPU 與驅動自動推薦；RTX 50 系列一律改用 cu128；後端未載入 torch 時以子程序查詢版本，避免 Windows 鎖住 DLL；若 torch 已載入則要求先重啟後端 |
| M14 | 縮圖只在寫入時建立目錄；配對檔中優先用圖片（不會選到 MOV）當縮圖來源 |
| M15 / G-17 | 可釋放容量、累計已清理容量改為依不重複資產計算 |
| G-16 | 遷移前備份改依實際資料庫 URL 定位；全新資料庫不備份 |
| — | `POST /api/scan/{root_id}` 改用路徑參數，與前端 `api.ts` 一致（原本會 404） |
| — | `/api/health` 的 `gpu_mode` 改回報實際的 `cuda` / `cpu` |
| — | `enums.py` 補上 `AssetStatus.MISSING`、`CandidateState.DELETED` |

## 7. 測試
- `tests/conftest.py`：**測試改用暫存資料庫與縮圖快取**（舊版會清空 `backend/data/app.db`）；自動注入假 CLIP 後端。
- 新 `tests/test_v05_fixes.py`（33 項）：Keeper 保護四種情境、刪前校驗、壞圖/壞影片/CLIP 失敗、OOM 減半、逐張錯誤隔離、近似串連與拆分、方法切換、連拍、5,000 張近似測速、任務互斥與取消、Origin/Host 防護、設定驗證、儀表板容量、API 端到端（掃描 → 分析 → 評估）。
- 新 `tests/test_real_clip.py`：真實 open_clip 煙霧測試，`$env:PC_REAL_CLIP="1"; pytest -m real_clip -s` 才執行。
- `test_init_and_scan.py`：`gpu_mode` 斷言改為 `cuda` / `cpu`。

---

## 請在你的電腦上驗證
1. `cd backend` → `pytest`：應為 72 passed、1 skipped。
2. GPU 煙霧測試：`$env:PC_REAL_CLIP="1"; pytest -m real_clip -s`（會下載約 600MB 模型）。
3. 測速（RTX 5080 與 GTX 1650 Ti 各一次）：
   - 先對 2,000 張相片跑一次 basic 階段（產生縮圖）。
   - 再執行 `python scripts/bench_clip.py D:\相片資料夾 --limit 2000`。
   - 把輸出貼給我。
4. RTX 5080 若還是 CPU 版 PyTorch：`pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128`。

## 尚未處理（下一步）
- Phase 4：分類門檻校準（需要你標記約 300 張）、截圖的解析度/EXIF 輔助判斷、`blur_threshold` 重新定預設值。
- Phase 5 其餘：NAS 上的刪除（C4，隔離資料夾 + 還原）、配對檔刪到一半（C5，`partial` 狀態）。在此之前請不要用 Process 刪 NAS 上的相片。
- 前端：Settings 頁尚未顯示新設定（`near_dup_method`、`clip_dup_threshold`、`near_dup_max_group`、連拍門檻、`clip_batch_size`），目前用預設值；Process 頁尚未顯示 `protected_items`。
- 規格書更新到 v0.5。
