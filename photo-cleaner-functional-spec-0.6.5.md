# 相片清理助手（Photo Cleaner）：Functional Spec（v0.6.5）

## 0. 文件說明

| 項目 | 內容 |
|---|---|
| 版本 | v0.6.5（對應應用程式 v0.6.5，2026-10-15） |
| 取代 | `photo-cleaner-functional-spec-0.4.md`；`spec-update-scan-analysis.md`（已併入）；`CHANGELOG-v0.5.md`、`CHANGELOG-v0.6.md` … `CHANGELOG-v0.6.5.md` 的內容已整理進本文，各檔保留作歷史紀錄 |
| 讀者 | 開發者（含 AI 助手）。程式與文件不一致時，先以本文為準，再回頭修正文件或程式 |
| 語言 | 介面與文件使用繁體中文；程式識別字、API 路徑與設定鍵使用英文 |
| 需求編號 | `SC-` 掃描、`AN-` 分析、`EV-` 評估、`RV-` 審核、`PV-` 預覽、`PR-` 執行刪除、`ST-` 設定、`UI-` 版面、`SF-` 安全 |

**本文的來源與限制**：本文依 v0.5 / v0.6 的變更紀錄、程式碼與 v0.6.1–v0.6.5 的實作整理而成。舊版規格書 0.4 的第 10 節「已知差距」（G-x 編號）沒有逐項沿用：已完成者（如連拍規則 G-1、影片規則 G-2、遷移備份 G-16、可釋放容量 G-17）已併入對應章節，其餘未完成項目重新整理在第 10 節。

---

## 1. 目標與範圍

**目標**：本地運行的網頁工具，掃描指定資料夾，用 AI 語義分析與規則引擎找出螢幕截圖、文件、梗圖、誤拍、重複、近似、連拍與畫質廢片，讓使用者在虛擬化縮圖網格中批量審核（可預覽大圖），安全地移至資源回收筒，或排除與復原。

**核心特色**

- AI 內容語義分類（CLIP 零樣本）是一級核心功能，可用 GPU 加速。
- 掃描、分析、評估各自獨立；分析分為四個獨立階段，可單獨執行、重試與續跑。
- 即時透明的任務進度（SSE：階段、當前檔案、速度、ETA、取消）。
- 鐵則級資料保護（見第 7 節）。
- 規模目標：約 10 萬張、300GB（本機磁碟或 NAS），審核頁在這個規模下仍然流暢。

**使用情境**：單一使用者，Windows 筆電（已在 GTX 1650 Ti 與 RTX 5080 驗證），資料全程不離開本機。

**非目標（目前不做）**：雲端同步、多用戶、手機 App、相片編輯、人臉辨識、語義搜尋、影片內容分析（只做容器資訊與大小檢查）、多語言介面（只有繁體中文）。

---

## 2. 已確認的架構決策

| # | 議題 | 決定 |
|---|---|---|
| 1 | 刪除方式 | **資源回收筒**（`send2trash`）。介面不提供其他模式；永久刪除只能透過 API 並需確認文字；隔離資料夾（quarantine）尚未實作，後端拒絕 |
| 2 | RAW | 完整支援（解碼、分析、縮圖、與 JPG 配對）；RAW + JPG 優先用伴生 JPG |
| 3 | 影片 | 不做內容分析，只讀容器資訊（時長、解析度、編碼），做大小相同與極小影片檢查 |
| 4 | 多路徑重複 | 完整支援跨路徑比對；路徑清單順序 = 保留優先權 |
| 5 | AI 語義識別 | 一級核心功能：截圖、文件 / 收據、梗圖 / 表情包、誤拍 |
| 6 | 任務可視化 | SSE 即時進度；任一時間只允許一個任務 |
| 7 | 介面語言 | 繁體中文；後端只綁定 127.0.0.1 |
| 8 | 設定預設值 | 只定義在 `backend/app/core/defaults.py`（單一來源）；前端從 `/api/settings/meta` 取得預設值與範圍 |
| 9 | 資料庫變更 | 一律透過 `core/migration.py`（`PRAGMA user_version`），先備份，遷移必須冪等 |
| 10 | 審核規模 | 後端分頁與統計；前端只保留可見的卡片（虛擬捲動），不一次載入全部候選 |
| 11 | 螢幕截圖 / 文件 | 不受「群組至少保留一張」保護（使用者決定，v0.6.3） |

---

## 3. 核心概念

### 3.1 資產與檔案

- **資產（Asset）**：一個或多個檔案組成的處理單位。Live Photo（HEIC/JPG + MOV）、RAW + JPG 是同一個資產，處理時不可分割。
- 資產的 `media_type`：`photo`、`raw`、`video`。
- 資產狀態 `status`：`unanalysed`、`analysed`、`candidate`、`queued`、`kept`、`deleted`、`missing`。
  `deleted`、`missing` 稱為「不再存在」，所有規則、統計、審核頁一律排除。

### 3.2 候選與狀態機

一個資產可以同時命中多個規則，產生多筆 **候選（Candidate）**（每筆有 `rule`、`reason`、`score`、`group_id`、`is_keeper`、`state`）。審核動作以**資產**為單位：同一資產的所有候選記錄一起改變狀態，資產的 `status` 同步。

```
unanalysed → analysed → candidate ─┬→ queued ──→ deleted
                                   │     └─→ candidate / kept（移出待刪）
                                   └→ kept ─→ candidate（恢復）/ queued
```

| 狀態 | 意義 |
|---|---|
| candidate（候選） | 系統建議清理，等待決定 |
| kept（已排除） | 使用者明確保留；重新評估時不再建議刪除 |
| queued（待刪） | 使用者已加入待刪清單，等待在「執行刪除」頁處理 |
| deleted（已刪除） | 已在「執行刪除」頁處理，保留操作紀錄 |

評估會保留使用者的 `queued` / `kept` 決定，並確保每個群組至少有一張不在待刪清單（螢幕截圖 / 文件的例外見 RV-30）。

### 3.3 群組與 Keeper

- 群組類型：`exact`（完全重複）、`near`（近似）、`burst`（連拍）、`video_size`（影片大小相同）。
- 每個資產只屬於一個群組：依序「完全重複 → 近似 → 連拍」，前面已歸組的不再進後面的群組。
- 每個群組有一個 **Keeper**（建議保留者），用綠框標示；Keeper 不計入待審核數量，批次動作永遠不碰 Keeper。

### 3.4 主要分類與分類

- **規則**（`rule`）：`exact_dup`、`near_dup`、`burst`、`blur`、`over_exposure`、`under_exposure`、`screenshot`、`document`、`meme`、`mistouch`、`video_same_size`、`video_tiny`。
- **審核頁分類**：全部、螢幕截圖、文件 / 收據、梗圖 / 迷因、誤拍 / 廢片、完全重複、近似重複、連拍、模糊、曝光不良（過暗 + 過曝）、影片（影片重複 + 極小影片）。一張相片命中多個規則時，會同時出現在多個分類，所以**分類張數加總可能大於「全部」**；「全部」是不重複資產數。
- **主要分類**（首頁用）：每張待審核相片只歸入一個類別，優先順序為完全重複 → 近似重複 → 連拍 → 截圖 → 文件 → 梗圖 → 誤拍 → 模糊 → 曝光 → 影片，所以各類別加總 = 待審核總數。

---

## 4. 功能模組

### 4.1 Library（資料庫掃描與分析工作台）

頁面分三個區塊，各自獨立操作：**掃描**、**分析**、**評估**，加上「一鍵流水線」。

- **SC-01 路徑管理**：輸入本機絕對路徑，可設遞迴與排除 pattern；路徑以 `Path.resolve()` 標準化（Windows 大小寫不敏感）。
- **SC-02 重疊防護**：新增的路徑若已被上層遞迴目錄涵蓋、或涵蓋了已登錄的子目錄，後端拒絕並說明原因。
- **SC-03 優先序**：路徑清單順序即重複群組的保留優先權。
- **SC-04 離線**：路徑不可用時標示「離線 / 不可用」，不可單獨掃描。

### 4.2 掃描（Scan）

- **SC-10** 只同步目錄與 metadata，**不解碼圖片、不呼叫任何特徵提取**。
- **SC-11 模式**：`full`（新增、比對修改、標記遺失）與 `new_only`（只加入新檔案；修改過的檔案分析結果保留，消失的檔案不標為 missing）。
- **SC-12** 檔案被修改時，清除該資產的衍生分析資料，之後重新分析；掃描每個根目錄完成即 commit。
- **SC-13 配對**：RAW + JPG（同目錄同檔名，RAW 為主檔）、Live Photo（HEIC/JPG + MOV，圖片為主檔）；其餘為獨立資產。
- **SC-14 支援格式**：照片 jpg/jpeg/png/webp/tiff/tif/bmp/gif/heic/heif；RAW cr2/cr3/nef/arw/dng/orf/rw2/pef/raf；影片 mp4/mov/avi/mkv/wmv/flv/webm/m4v。
- **SC-15** 掃描結果摘要：新增、修改、消失、未變更。

### 4.3 分析（Analyze）

四個獨立階段，可一起或單獨執行、可重試失敗項目，只處理尚未完成的項目；中斷後重新執行會從未完成處繼續。

| 階段 | 做什麼 | 狀態 |
|---|---|---|
| **hash** | SHA-256，**只算「大小與其他檔案相同」者**；大小唯一者標 `not_needed`；之後新增了同大小的檔案，`not_needed` 會被重設為 `pending` | `pending` / `done` / `not_needed` / `error` |
| **basic** | 每張相片**只解碼一次**（JPEG 用 draft 降解析度解碼；RAW + JPG 用伴生 JPG；RAW 取內嵌預覽），依 EXIF 轉正，同時寫 320px 縮圖快取與計算 pHash / 模糊 / 亮度 / EXIF；特徵在**最長邊 1024px** 的縮小圖上計算 | `features.decode_status`：`ok` / `fallback` / `error` |
| **clip** | CLIP 向量與零樣本分類，GPU 或 CPU；優先讀縮圖快取 | 依 `clip_features.model/pretrained` 判斷是否過期 |
| **video** | OpenCV 讀時長、解析度、編碼 | 有 `video_info` 即完成 |

- **AN-01 不寫假資料**：任何失敗（解碼失敗、CLIP 錯誤、影片打不開、檔案不存在）一律寫入 `analysis_failures` 並標示失敗，不可用隨機向量、假 pHash 或「normal_photo = 1.0」當作成功。
- **AN-02 失敗重試**：失敗清單可依階段篩選，「重試失敗」會重新處理。
- **AN-03 縮圖快取**：快取鍵 = sha256(路徑 + 大小 + mtime + 伴生檔)，雙層目錄分片，只在寫入時建立目錄；失敗不快取佔位圖；可「補算縮圖」只補缺少的。
- **AN-04 並行**：各階段 worker 數（`*_runners`，1–16，預設 CPU 核心數 // 2）；SQLite 寫入在記憶體累積後以短交易批次寫入，解碼 / 推理期間不持有寫入交易。
- **AN-05 裝置選擇**（只影響 clip）：「自動 / 只用 GPU / 只用 CPU」。選「只用 GPU」但 CUDA 不可用時，工作直接失敗並說明原因，不可靜默退回 CPU。
- **AN-06 CLIP 管線**：loader 執行緒（數量 = `clip_runners`）讀縮圖並前處理（補邊成正方形，不中心裁切）→ **單一**推理迴圈批次 forward → 背景 writer 每批 bulk upsert。批次大小 `clip_batch_size`（0 = 依顯存：≥12GB 128、≥6GB 64、其餘 32；CPU 16）；顯存不足自動減半重試同一批；單張讀圖或推理失敗寫入 `analysis_failures`（stage = `clip`），可用「重試失敗」再跑。CUDA 精度自動實測 fp16 / fp32 取較快者（可用 `PC_CLIP_PRECISION` 指定）。OpenAI 權重強制使用 QuickGELU。
- **AN-07 CLIP 向量記錄模型**：`clip_features.model / pretrained / dim` 必填；評估只使用與目前設定相同模型的向量；更換模型後舊向量標為「過期」。
- **AN-08 零樣本類別**：`normal_photo`、`screenshot`、`document`、`meme`、`mistouch`，各以 prompt ensemble 評分；輸出為 softmax 機率，`zs` 一律使用這些英文鍵。
- **AN-09** 分析頁顯示各階段的待處理 / 已完成 / 失敗 / 過期、目前的 CLIP 模型與裝置；「待處理項目」逐階段列出，不跨階段相加。

### 4.4 評估與規則引擎（Evaluate）

- **EV-01** 規則引擎一律排除 `deleted` / `missing` 資產（遺失的檔案不可能成為 Keeper）。
- **EV-02 完全重複**：SHA-256 相同；相片 / RAW 為 `exact_dup`，影片為 `video_same_size`；跨路徑比對（`cross_path_duplicates`）。
- **EV-03 近似重複**：`near_dup_method` = `clip` / `phash` / `both` / `either`（預設 `either`）。
  - CLIP 餘弦相似度分塊計算（有 CUDA 時在 GPU 上；10 萬張 × 512 維在 RTX 級 GPU 上約數秒，CPU 約數十秒）；pHash 以 multi-index hashing 找配對，避免 O(n²) 全比對。
  - 配對以 union-find 串成群組；群組超過 `near_dup_max_group`（預設 50）時逐步收緊門檻拆小。
- **EV-04 連拍**：拍攝時間（EXIF `DateTimeOriginal` + `SubsecTimeOriginal`）在時間視窗內（預設 2 秒）且 CLIP 相似度 ≥ 門檻（預設 0.85）；保留最清晰的一張。讀不到 EXIF 拍攝時間而以檔案修改時間代替時，不採用連拍規則。
- **EV-05 Keeper 挑選**：依 `keeper_strategy`（偏好順序包含路徑優先序、清晰度、檔案大小等，實際預設值見 `defaults.py`）；近似重複保留「畫質最佳」的一張，連拍保留「最清晰」的一張。
- **EV-06 AI 語義規則**：該類別分數 ≥ 門檻**且**高於 `normal_photo` 才列為候選；預設門檻 截圖 0.60、文件 0.55、梗圖 0.65、誤拍 0.55。
- **EV-07 畫質規則**：模糊（Laplacian 變異數低於門檻，預設 100）、過暗（平均亮度低於 30）、過曝（高於 225）。**注意：模糊門檻改為在 1024px 縮小圖上計算後，預設值 100 尚未重新校準**。
- **EV-08 影片規則**：`video_same_size`（大小完全相同）、`video_tiny`（小於 `video_tiny_threshold_kb`）；各自的開關與預設值見 `defaults.py`。
- **EV-09 評估狀態追蹤**：
  - 指紋 = 資產數與狀態（存在 / 已刪除 / 遺失）、已雜湊檔案數、各階段完成數與最新分析時間、影片資訊數、以及**會影響評估**的設定。
  - 以下設定不納入指紋：四個 `*_runners`、`compute_device`、`clip_batch_size`、`auto_evaluate_on_save`、`deletion_mode`。
  - 審核動作（待刪 / 保留）不改變指紋，不會讓評估過期。
  - 與上次評估的指紋相同 → 評估自動略過（`force=true` 可強制）。
  - 每次評估回報新增 / 移除幾張候選；並顯示「有 N 張相片在上次評估後完成分析」。
  - 狀態存在 `app_state` 表，「恢復預設值」不會清掉。
- **EV-10** 評估一次完成、無法中途取消（`cancellable=false`，`/api/jobs/cancel` 回 `ignored`）。

### 4.5 審核工作台（Review）

**目的**：以虛擬化網格讓使用者快速確認要刪除或保留的相片，在 10 萬筆候選的規模下仍然流暢。**本頁只改資料庫狀態，絕不動實體檔案**。

**資料與效能**

- **RV-01 後端分頁**：`GET /api/candidates/page`，一次 200 張（上限 1000），捲到底自動載入下一頁；排序穩定（同分以資產 id 排序），可用 offset 續讀。
- **RV-02 後端統計**：`GET /api/candidates/summary` 回傳各審核狀態與各分類的張數、容量與 Keeper 數（不重複資產）。側邊欄每個分類顯示張數與容量，審核狀態頁籤顯示張數。
- **RV-03 虛擬捲動**：只渲染畫面附近的列；卡片高度固定，列高精確。
- **RV-04 Keeper 顯示**：「顯示建議保留的相片」開關（show / hide / only）。完全重複 / 近似重複 / 連拍預設顯示（看得到群組全貌），其餘分類與「全部」預設隱藏，數量才會和側邊欄、首頁一致；顯示時標題註明「含 N 張建議保留」。

**檢視**

- **RV-10 排序**：分數（高 → 低）、檔案大小、重複群組（成員相鄰）、路徑；各分類有預設排序。
- **RV-11 AI 分類的分數門檻滑桿**：只在 AI 分類出現，伺服器端過濾。
- **RV-12 卡片大小**：3–8 欄（記住設定）；7 欄以上為精簡卡片。
- **RV-13 卡片內容**：縮圖、媒體類型、分數（AI 分類）、群組編號與組內張數、建議保留標記、檔名、大小、路徑、規則徽章、偵測原因（完整原因在提示文字）、解析度與拍攝日期。
- **RV-14 側邊欄**：可收合成窄欄（只留圖示與數量），仍可切換分類與審核狀態（UI-02）。

**選取與動作**

- **RV-20 選取**：點選切換；`Shift` + 點選範圍選取；「全選已載入」（跳過 Keeper）；`Ctrl+A`；`Esc` 取消。選取列顯示張數與可釋放容量。
- **RV-21 狀態變更**：加入待刪 / 排除保留 / 恢復候選（`D` / `K` / `R`），以資產 id 送出；成功後**直接更新畫面**（不重新載入、不丟失捲動位置）。選取超過 200 張時先確認。
- **RV-22 依條件批次**：選取已載入的全部後，出現「選取符合條件的全部 N 張」→ 後端依目前篩選條件處理：
  1. 先預覽（`dry_run`）：符合的張數、容量、隨機 12 張縮圖，並標明是否適用保護豁免；
  2. 確認後執行，必須帶預覽的 `expected_count`，與目前實際張數不符（清單已變更）回 409 且**什麼都不寫入**；
  3. 可排除個別相片（點選卡片）；
  4. **永遠只動非 Keeper 的相片**。
- **RV-23 復原**：每次狀態變更回傳 `undo_token`；畫面下方顯示「復原」（`Ctrl+Z`，12 秒內；預覽開著時也可用）。只還原「目前仍處於變更後狀態」的相片，不會蓋掉之後的其他決定；同一個 token 只能用一次；最多保留最近 20 筆，存在記憶體，**後端重新啟動後失效**。
- **RV-24 分塊**：狀態變更每 500 筆一塊（避開 SQLite 變數上限）；每塊先寫入再檢查下一塊，所以 Keeper 保護看得到前面已加入待刪的成員。
- **RV-25 預設快速鍵**：`D` / `K` / `R`、`Ctrl+Z`、`Ctrl+A`、`Esc`、`Space` / `Enter`（預覽）；焦點在輸入框、下拉選單時不觸發；焦點在按鈕 / 連結上時，空白鍵與 Enter 留給它們自己。

**保護規則**

- **RV-30 Keeper 保護**（後端強制）：加入待刪時，檢查資產所屬的每個群組，不論前端傳的是哪一條候選記錄；若會讓某群組一張不剩，擋下並回報 `protected_asset_ids`，畫面提示「因群組至少保留一張而保留」。
- **RV-31 螢幕截圖 / 文件豁免**：請求帶 `waive_protection_for = screenshot | document`（前端在該分類操作時自動帶入；依條件批次在該分類也自動套用）時，**具有該規則候選記錄的資產**不受 RV-30 保護，先寫入；其餘資產仍交給保護檢查（此時豁免的資產已是待刪，所以非豁免的成員仍會被擋下「最後一張」）。啟用豁免時，確認視窗與側邊欄會明確提示。

### 4.6 預覽大圖（Preview）

- **PV-01 開啟**：雙擊卡片、縮圖右上角的放大鏡（滑鼠移上去才出現），或 `Space` / `Enter`（預覽最後點選的那張）。開啟預覽不改變選取。
- **PV-02 導覽**：`←` `→` 與畫面上的箭頭沿著審核清單切換；顯示「第幾張 / 總張數」；快到清單尾端（剩 4 張）時自動載入下一頁；前後幾張的大圖先載入。換一張會回到「適合視窗」。
- **PV-03 在預覽裡決定**：`D` / `K` / `R`（與下方按鈕）只處理**目前這一張**，不影響已選取的其他卡片；成功後自動跳到下一張（沒有下一張則上一張，都沒有則關閉）。被保護規則擋下時留在原地並顯示原因。`Ctrl+Z` 可復原；通知顯示在預覽上層且不蓋住操作按鈕。
- **PV-04 檢視**：點圖或 `F` 切換「適合視窗」↔「100%」；100% 時可捲動；顯示實際顯示的像素尺寸。
- **PV-05 資訊欄**（`I` 顯示 / 隱藏，會記住）：檔名、路徑、大小、解析度、拍攝時間、類型；偵測規則徽章、原因與分數；**重複群組的所有成員**（縮圖、大小、解析度、清晰度、保留 / 待刪 / 已排除標記，Keeper 排最前面）。
- **PV-06 群組比對**：點群組成員切換預覽以比較畫質。成員若不在目前的審核清單（例如被隱藏的 Keeper、已待刪），進入「比對中」唯讀模式：不能操作，`←` `→` 仍從原本的清單位置繼續；點回清單內的成員則回到一般模式。
- **PV-07 關閉**：`Esc` 或關閉鈕；清單捲到最後看的那一張；切換篩選條件會關閉預覽。
- **PV-08 圖片來源**（`GET /api/candidates/preview/{asset_id}`）：
  - jpg / png / webp / gif / bmp：**原檔直接回傳**（完整畫質）。
  - 其他格式（HEIC / TIFF / RAW）與影片：解碼後縮成最長邊 2560px 的 JPEG（影片只取第一幀），並在畫面提示「不是原始解析度」。
  - RAW + JPG 優先用伴生 JPG；影片不使用伴生檔。
  - 路徑一律來自資料庫（只接受資產 id）；解碼結果放在記憶體 LRU（12 張）；帶 `?v=mtime` 時長快取，否則 5 分鐘。
  - 檔案不存在（已移動 / 刪除 / 磁碟離線）回 404、無法解碼回 422，前端顯示後端的原因。
- **PV-09 群組成員**（`GET /api/candidates/group/{group_id}`）：含各種狀態；排除已刪除與遺失；Keeper 最前面，其餘依清晰度（高 → 低，沒有的排最後）、大小；最多 200 張。

### 4.7 執行刪除（Process）

**鐵則：本頁是全系統唯一會動實體檔案的入口。**

- **PR-01** 待刪清單總覽：張數、預計釋放容量（以不重複資產計）、每筆的原因與配對檔，可「移出」。
- **PR-02** 必須勾選確認；`POST /api/process/execute` 需 `confirmed = true`。
- **PR-03 模式**：`trash`（預設，`send2trash`）；`permanent` 需 `confirm_text = "永久刪除"`；`quarantine` 後端拒絕（未實作）。介面一律使用 `trash`。
- **PR-04** 分析或評估進行中不能刪除（409）。
- **PR-05 刪前即時校驗**：檔案必須存在，且大小**或**修改時間任一與資料庫不符就不刪。
- **PR-06 最後一道防線**：若這批刪除會讓某群組一張不剩，自動擋下一張（優先 Keeper）並移回候選，回應列出 `protected_items`。**目前尚未配合 RV-31 的螢幕截圖 / 文件豁免**（見第 10 節）。
- **PR-07 配對檔**：資產的主檔與附屬檔（RAW + JPG、Live Photo 的 MOV）一併處理。
- **PR-08** 單一資產失敗不中斷整體；所有操作寫入 `actions_log`（批次 id、動作、模式、原始路徑、時間）。

### 4.8 歷史記錄（History）

- **HI-01** 批次列表：時間、動作、模式、張數、批次 id；可展開每張相片的原始完整路徑。
- **HI-02** 資源回收筒模式提供還原指引（Windows 無法由網頁一鍵還原）。

### 4.9 設定（Settings）

- **ST-01 版面**：AI 內容分類（四個開關 + 門檻）、重複與連拍（完全重複開關；近似重複開關、比對方式、CLIP 門檻 / pHash 距離；連拍開關、時間視窗、相似度）、畫質（模糊、曝光）、收合的「進階：分析效能」（`clip_batch_size` 與四個階段的執行緒數）。
- **ST-02 只顯示會明顯影響結果的設定**；保留者排序、跨路徑重複、近似群組上限、影片規則、CLIP 模型 / pretrained、運算裝置、刪除模式使用預設值（不在頁面顯示）。
- **ST-03 只送出有變更的項目**；沒有任何變更時，後端什麼都不寫、不評估、不檢查任務。
- **ST-04 依需要才評估**：只有變更了「會影響評估結果」的設定，且 `re_evaluate = true` 時才評估。`non_evaluation_keys`（效能類：四個 runners、`clip_batch_size`、`deletion_mode`、`auto_evaluate_on_save`）不評估、不佔任務鎖，分析進行中也能儲存。新增設定時預設視為「會影響評估」。
- **ST-05 任務鎖**：需要評估時先佔住任務鎖，有任務進行中回 409 且**不寫入任何設定**；使用者可選「只儲存」（`re_evaluate = false`），設定寫入但評估狀態變成過期，之後手動評估。
- **ST-06 還原預設**：只有被還原的設定會影響評估時才評估。
- **ST-07 驗證**：`PUT /api/settings` 驗證未知鍵、型別、範圍與選項，全部通過才寫入；讀取時無效或已淘汰的值退回預設，確保「讀取 → 修改 → 送回」不會被擋。
- **ST-08 預設值與範圍來源**：前端從 `GET /api/settings/meta` 取得 `defaults`、`ranges`、`choices`、`non_evaluation_keys`，不在前端重複定義。
- **ST-09 使用者體驗**：未儲存變更提示與「放棄變更」、每個欄位可還原為預設值、顯示後端真正的錯誤訊息、任務進行中與評估過期的提示；過暗上限必須小於過曝下限；模糊門檻旁提示「已改在 1024px 上計算，預設值尚未校準」。
- **ST-10** 儲存後若評估失敗，畫面會說明「設定已儲存，但重新評估失敗」（後端先寫入、再評估）。

**設定項目、預設值與範圍**（範圍與選項取自 `defaults.py` 的 `SETTING_RANGES` / `SETTING_CHOICES`；「—」表示本文未逐項核對，以 `defaults.py` 為準）

| 分類 | 鍵 | 預設 | 範圍 / 選項 |
|---|---|---|---|
| AI 規則開關 | `rule_screenshot_enabled` / `rule_document_enabled` / `rule_meme_enabled` / `rule_mistouch_enabled` | — | — |
| AI 門檻 | `screenshot_threshold` / `document_threshold` / `meme_threshold` / `mistouch_threshold` | 0.60 / 0.55 / 0.65 / 0.55 | 0.0–1.0 |
| 重複開關 | `rule_exact_dup_enabled`、`rule_near_dup_enabled`、`rule_burst_enabled` | — | — |
| 近似 | `near_dup_method` | `either` | clip / phash / both / either |
| 近似 | `clip_dup_threshold` / `phash_threshold` / `near_dup_max_group` | 0.95 / 6 / 50 | 0.5–1.0 / 0–20 / 2–10000 |
| 連拍 | `burst_time_window_seconds` / `burst_similarity_threshold` | 2.0 / 0.85 | 0–60 / 0.5–1.0 |
| 畫質 | `rule_blur_enabled`、`blur_threshold` | —、100（未校準） | 0–100000 |
| 畫質 | `rule_exposure_enabled`、`under_exposure_threshold` / `over_exposure_threshold` | —、30 / 225 | 0–255 |
| 影片 | `rule_video_same_size_enabled` / `rule_video_tiny_enabled` / `video_tiny_threshold_kb` | — | 0–10,000,000 |
| 保留 / 刪除 | `keeper_strategy` / `cross_path_duplicates` / `auto_evaluate_on_save` | — | — |
| 保留 / 刪除 | `deletion_mode` | `trash` | trash / quarantine / permanent（目前只有 trash、permanent 可用） |
| CLIP | `clip_model` / `clip_pretrained` | `ViT-B-16` / `openai` | — |
| CLIP | `compute_device` / `clip_batch_size` | `auto` / 0（自動） | auto / cuda / cpu；0–1024 |
| 並行 | `hash_runners` / `basic_runners` / `clip_runners` / `video_runners` | CPU 核心數 // 2 | 1–16 |

### 4.10 首頁總覽（Dashboard）

- **DB-01** 四張統計卡：相片庫（資產數、容量）、待審核、待刪、已清理（累計容量與批次數）。
- **DB-02** 主要分類長條圖：每張相片只歸入一個分類，各類別加總 = 待審核總數；待審核 / 待刪各自顯示數量與容量。
- **DB-03** 處理流程：掃描 → 分析 → 評估 → 審核 → 刪除，即時顯示各步驟狀態（完成 / 待處理 / 需注意 / 進行中）、失敗與遺失提示、評估是否過期。
- **DB-04** 數字隨任務進度更新（任務中每 2 秒、閒置每 30 秒、任務剛結束立即更新）。

### 4.11 版面與導覽（Layout）

- **UI-01 主選單**：展開 256px；可收合成 64px 的圖示欄（滑鼠移上去顯示名稱，目前頁面仍標示；底部只剩運算引擎圖示）。
- **UI-02 審核篩選欄**：展開 256px；可收合成 56px 窄欄（11 個分類圖示 + 數量、候 / 刪 / 留三個狀態按鈕；數量縮寫如 1.2k、12k；提示文字顯示完整名稱、張數、容量）。
- **UI-03** `[`：專注模式——審核頁一次收合 / 展開兩個側邊欄（只要有一個是展開的，就全部收合）；其他頁只動主選單；在輸入框 / 下拉選單裡不觸發。
- **UI-04** 兩個欄的收合狀態各自記在 localStorage，重新整理後維持。
- **UI-05** 頁面自己負責捲動（設定頁、審核頁），不依賴外層容器。
- **UI-06 名稱**：應用程式名稱「相片清理助手」（頁面標題、側邊欄標題；`index.html` 的 `<title>` 也要同步）。

---

## 5. 資料模型（SQLite WAL）

資料庫版本以 `PRAGMA user_version` 管理；啟動時自動遷移（冪等，遷移前先備份）。

| 資料表 | 說明 |
|---|---|
| `roots` | 掃描路徑：`path`（唯一）、`recursive`、`exclude_patterns`、`priority`、`last_scan_at`、`available` |
| `assets` | 資產：`root_id`、`media_type`、`primary_path`（唯一）、`size_total`、`paired_paths`、`status`、`created_at` |
| `files` | 實體檔案：`asset_id`、`path`（唯一）、`size`、`mtime`、`sha256`、`format`、`role`（primary / pair）、`hash_state` |
| `features` | 基本特徵（1:1）：`phash`、`blur_score`、`brightness`、`width`、`height`、`taken_at`、`exif`、`decode_status`、`analyzed_at` |
| `clip_features` | CLIP（1:1）：`model`、`pretrained`、`dim`、`embedding`（BLOB）、`zs`（JSON 機率）、`analyzed_at` |
| `video_info` | 影片（1:1）：`duration`、`width`、`height`、`codec` |
| `analysis_failures` | 分析失敗：`asset_id`、`stage`、`error_msg`、`occurred_at` |
| `groups` | 重複群組：`type`、`keeper_asset_id` |
| `candidates` | 候選：`asset_id`、`rule`、`reason`、`score`、`group_id`、`is_keeper`、`state` |
| `actions_log` | 刪除與還原軌跡：`batch_id`、`asset_id`、`action`、`mode`、`original_path`、`new_path`、`at` |
| `settings` | 使用者設定（JSON 鍵值） |
| `app_state` | 系統內部狀態（評估指紋、時間、結果）；不是使用者設定，「恢復預設值」不會清掉 |

---

## 6. API

任務類 API 在已有任務進行中時回 400（`BUSY`）；設定與刪除相關的互斥回 409。

| 路徑 | 說明 |
|---|---|
| `/api/roots` | 路徑：列出 / 新增（含重疊檢查）/ 刪除 |
| `/api/jobs/scan`、`analyze`、`rebuild_thumbs`、`evaluate`、`pipeline` | 啟動任務（`evaluate` 可帶 `force`；`pipeline` 帶 `mode` 與 `device`） |
| `/api/jobs/status`、`events`（SSE）、`cancel` | 進度與取消 |
| `/api/scan/…` | 舊版相容層（`/all`、`/{root_id}`、`status`、`events`、`cancel`） |
| `/api/analysis/status`、`failures` | 各階段統計與目前 CLIP 設定；失敗清單（可依階段篩選） |
| `/api/workflow/status` | 流程狀態、主要分類、審核統計、目前任務（首頁 / 資料庫頁共用） |
| `/api/candidates`（舊） | 一次回傳全部候選（保留給舊程式與測試；審核頁不再使用） |
| `/api/candidates/summary` | 審核統計（`state`） |
| `/api/candidates/page` | 分頁：`category`、`state`、`sort`、`min_score`、`keepers`、`offset`、`limit` |
| `/api/candidates/batch-status` | 以資產 id 變更狀態（`asset_ids` / `candidate_ids`、`state`、`waive_protection_for`）；回傳 `updated_count`、`protected_asset_ids`、`undo_token` |
| `/api/candidates/bulk-status` | 依條件批次：`filter`、`target_state`、`exclude_asset_ids`、`dry_run`、`expected_count` |
| `/api/candidates/undo` | 復原（`token`） |
| `/api/candidates/preview/{asset_id}` | 預覽大圖 |
| `/api/candidates/group/{group_id}` | 群組成員 |
| `/api/candidates/evaluate` | 同步重新評估（舊版相容） |
| `/api/process/summary`、`execute`、`unqueue` | 待刪清單、執行刪除、移出待刪 |
| `/api/history/batches`、`/batches/{id}` | 清理歷史 |
| `/api/settings`（GET / PUT）、`/meta`、`/reset` | 設定 |
| `/api/system/gpu-status`、`install-cuda`（含 `/cancel`） | GPU 診斷、安裝 CUDA 版 PyTorch |
| `/api/thumbnails/{id}` | 縮圖 |
| `/api/dashboard/stats`、`/api/health` | 統計、健康檢查 |

---

## 7. 安全防護

**資料保護（鐵則，違反即為缺陷）**

- **SF-01** 只有「執行刪除」頁（`POST /api/process/execute` → deletion service）能動實體檔案；審核、預覽、設定、歷史只改資料庫狀態。
- **SF-02** 預設刪除 = 資源回收筒；永久刪除必須由使用者明確選擇並輸入確認文字。
- **SF-03** 保留最後一張：任何重複群組（exact / near / burst）不能被全刪，且必須在**後端**強制執行（例外：螢幕截圖 / 文件，RV-31）。
- **SF-04** 配對檔案（Live Photo、RAW + JPG）是同一資產，不可拆開處理。
- **SF-05** 刪除前即時校驗（PR-05）。
- **SF-06** 分析失敗不寫假資料（AN-01）。

**本機安全**

- **SF-10** 後端只綁定 `127.0.0.1`，無遙測；`TrustedHostMiddleware` 只接受 `127.0.0.1` / `localhost`（防 DNS rebinding）。
- **SF-11** 非 GET 請求若帶 `Origin` 不在允許清單，或跨站（`Sec-Fetch-Site`），回 403。
- **SF-13** 預覽 API 只接受資產 id，不接受路徑。

**NAS**：`send2trash` 在網路磁碟 / UNC 路徑上通常不會進 Windows 資源回收筒；是否進 NAS 自己的回收筒取決於該分享資料夾設定，請先用一張測試檔確認。NAS 上的安全刪除尚未完成（第 10 節）。

---

## 8. 非功能需求

- **NF-01 規模**：10 萬張 / 300GB。在開發環境以合成資料（10 萬資產、約 11 萬筆候選）測得：審核統計約 1 秒、一頁 200 張 0.3–0.8 秒、依條件批次預覽與執行約 1–3 秒（尚未在你的真實相片庫與 NAS 上量測）。
- **NF-02 GPU**：CLIP 批次推理；已驗證 GTX 1650 Ti（fp32 通常比 fp16 快，自動選擇）與 RTX 5080（需 PyTorch cu128）；`scripts/bench_clip.py` 可測速並估算 10 萬張時間。
- **NF-03 任務互斥**：任一時間只有一個任務；「檢查是否有任務並開始」在 endpoint 內原子完成（`try_start_*`）。
- **NF-04 SSE**：進度推送有節流，訂閱者只需要看到最新狀態；SSE 失敗時前端退回輪詢。
- **NF-05 SQLite**：WAL、`busy_timeout`（預設 30 秒）；同步 endpoint 在 threadpool 執行，不阻塞 event loop。
- **NF-06 預覽延遲**：瀏覽器原生格式直接串流原檔；需解碼的格式只解碼一次並快取。

---

## 9. 驗收與測試

**測試**

- 後端以 `pytest`（`backend/` 目錄）驗證，測試使用暫存資料庫與假 CLIP 後端，不會動到 `backend/data/app.db`；新增或修改規則、刪除、Keeper 邏輯時必須補測試。
- 真實模型煙霧測試：`$env:PC_REAL_CLIP="1"; pytest -m real_clip -s`；功能測試用已知答案的測試相片庫（`scripts/make_test_library.py`）。
- 前端以 `npm run build`（含型別檢查）驗證。
- 本版新增的測試：設定「依需要才評估」、審核 API（統計、分頁、批次、復原、保護豁免）、審核純邏輯、預覽圖片來源、預覽與群組 API。

**驗收條件**（`AC-` 為本文的編號；括號內是舊測試註解使用的 T- 編號）

| 編號 | 條件 |
|---|---|
| AC-01 | 「只加入新檔案」模式下，修改過的檔案分析結果保留（見 SC-11） |
| AC-02（T-2） | 單獨執行 hash 階段時，不讀取任何圖片內容 |
| AC-03（T-3） | 單獨執行 clip 階段時，`features` 內的模糊、亮度、pHash 不被改動 |
| AC-04 | 換 CLIP 模型後，舊向量的相片被標為過期；評估時不使用這些向量 |
| AC-05 | 選「只用 GPU」但 CUDA 不可用時，工作直接失敗並顯示原因；選「自動」時退回 CPU 並顯示實際裝置 |
| AC-06（T-7） | 刪除部分縮圖後，「補算縮圖」只補缺少的，其餘分析結果不變 |
| AC-07（T-8） | 有解碼失敗的相片時，「重試失敗項目」會重新嘗試；仍失敗則保持失敗狀態 |
| AC-08（T-9） | `hash_state = 'not_needed'` 的檔案在新增同大小檔案後，下次 hash 會被改為 `pending` 並算出雜湊 |
| AC-09 | 中斷（取消）後重新執行，不會重複處理已完成的項目 |
| AC-10 | 舊版資料庫升級後，既有分析結果完整保留 |
| RV-T1 | 審核統計與分頁在 10 萬筆候選下仍在數秒內回應；分頁無重複、無遺漏 |
| RV-T2 | 依條件批次：預覽後清單變更 → 執行被拒絕（409）且沒有任何寫入；永遠不動 Keeper |
| RV-T3 | 復原只還原仍處於變更後狀態的相片，不蓋掉之後的決定；token 只能用一次 |
| RV-T4 | 螢幕截圖 / 文件分類可把整個群組加入待刪；其他分類仍被擋下「最後一張」 |
| PV-T1 | 瀏覽器原生格式回傳原檔（位元組相同）；HEIC / TIFF / RAW / 影片縮成最長邊 ≤ 2560px 的 JPEG；同一檔案只解碼一次 |
| PV-T2 | 預覽中 D / K / R 只改這一張；被保護時留在原地並說明原因；Ctrl+Z 可復原 |
| ST-T1 | 沒有變更的 PUT 不寫入、不評估；只改效能類設定不評估且分析進行中可儲存；影響評估的設定在任務進行中回 409 且不寫入任何設定 |

---

## 10. 已知差距與待辦

| 編號 | 項目 |
|---|---|
| G-1 | **Phase 4 分類門檻校準**：需要使用者標記約 300 張；截圖的解析度 / EXIF 輔助判斷；模糊門檻在 1024px 下重新定預設值 |
| G-2 | **Phase 5 NAS 上的安全刪除**：隔離資料夾 + 還原（`quarantine` 目前被拒絕）；配對檔刪到一半的 `partial` 狀態。在此之前不建議用「執行刪除」頁刪除 NAS 上的相片 |
| G-3 | `services/deletion.py` 的最後一道防線尚未配合螢幕截圖 / 文件的保護豁免（PR-06 與 RV-31 不一致：加入待刪成功的整組截圖，刪除時仍會被擋下一張並移回候選） |
| G-4 | 預覽：並排比較兩張、滾輪縮放 / 拖曳平移、影片播放 |
| G-5 | 審核工作台：以群組為單位的列表（一列一組、一鍵接受建議、切換 Keeper） |
| G-6 | 復原紀錄只存在記憶體，後端重新啟動後失效 |
| G-7 | 設定頁不提供刪除模式、保留者排序、CLIP 模型的選項（使用預設值）；之後若需要調整，需要同時決定是否影響評估指紋 |

---

## 11. 版本沿革

| 版本 | 日期 | 摘要 |
|---|---|---|
| v0.3.x | — | 初版：Library / Review / Process / History / Settings / Dashboard；AI 語義分類為一級功能；即時掃描進度 |
| v0.4 | — | 掃描與分析拆分（Scan / Analyze 四階段、評估獨立）；規格書 0.4 |
| v0.5 | 2026-10-14 | 資料安全（Keeper 保護以資產為單位、刪前校驗、永久刪除確認）、不再寫入假資料、GPU 批次 CLIP 管線、basic 階段每張只解碼一次、近似重複與連拍的向量化規則引擎、任務控制與本機安全、設定單一來源 |
| v0.6 | 2026-10-15 | 評估狀態追蹤、一鍵流水線（`/api/jobs/pipeline`）、`/api/workflow/status`、首頁與資料庫頁重新設計、F-12 / F-13 修正 |
| v0.6.1–0.6.2 | 2026-10-15 | 設定頁重新設計（只存變更、依需要才評估、`/api/settings/meta`、頁面自己捲動）；移除刪除模式 UI |
| v0.6.3 | 2026-10-15 | 審核工作台：後端分頁與統計、無限捲動、原地更新與復原、Shift 範圍選取、依條件批次（預覽 → 確認）、分數門檻、卡片大小、螢幕截圖 / 文件的保護豁免 |
| v0.6.4 | 2026-10-15 | 可收合的主選單與審核篩選欄、`[` 專注模式 |
| v0.6.5 | 2026-10-15 | 大圖預覽（導覽、預覽中決定、100%、資訊欄、群組成員比對）、`GET /api/candidates/preview/{id}`、`GET /api/candidates/group/{id}`；應用程式更名為「相片清理助手」；規格書更新為本文 |
