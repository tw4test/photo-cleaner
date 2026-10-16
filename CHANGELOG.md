# 變更紀錄

新的在上。每一版只記重點；需求的完整描述在 [`docs/functional-spec.md`](docs/functional-spec.md)，逐版的詳細說明（含舊的安裝步驟）封存在 [`docs/archive/`](docs/archive/)。

## v0.7.0 — 2026-10-16

- **所有影片頁**（`/videos`，VD-01 至 VD-06）：列出資料庫裡所有影片（不論有沒有命中規則），搜尋、依名稱 / 日期 / 大小 / 時長 / 解析度排序、無限捲動；只讀，不能加入待刪。入口在主選單與審核頁篩選欄（展開與收合都有）。新 API：`GET /api/videos`、`GET /api/videos/summary`。
- **影片播放器**（VD-10 至 VD-13）：原檔 HTTP Range 串流；播放 / 暫停、音量、倍速、劇院模式、子母畫面、全螢幕與鍵盤（`←` `→` 快轉；`Shift` + `←` `→` 或 `PageUp` / `PageDown` 上一支 / 下一支）。新 API：`GET /api/videos/{id}/info`、`/stream`、`/transcoded`。
- **ffmpeg 轉檔**（VD-14 至 VD-17）：AVI / WMV / FLV 等瀏覽器播不了的影片，按「轉檔後播放」轉成 H.264 MP4（最高 1080p），有進度、可取消，關閉播放器 / 換片 / 關閉分頁自動取消；取消用 `proc.kill()`（不用 `terminate()`，ffmpeg 收到 SIGTERM 會先轉完整支）。結果存 `DATA_DIR/video_cache`（上限 5 GB，依最近使用淘汰），原檔不動。**ffmpeg 是選用的**，沒有時顯示安裝說明，其他功能不受影響。新 API：`POST /api/videos/{id}/transcode`、`GET /api/videos/{id}/transcode`、`POST /api/videos/{id}/transcode/cancel`、`GET /api/videos/ffmpeg`。新環境變數：`FFMPEG_PATH`、`FFPROBE_PATH`、`VIDEO_CACHE_MAX_BYTES`、`VIDEO_TRANSCODE_MAX_HEIGHT`。
- **HEVC 先試播，失敗才提示轉檔**（VD-11）：HEVC 與 10-bit H.264 能不能播取決於使用者的瀏覽器與顯示卡，所以先播原檔；播得出來就不出現任何提示。沒有 HEVC 解碼器的瀏覽器常常只播出聲音、不觸發 `error`，前端播放約 1.5 秒後檢查 `videoWidth` 與解碼畫格數，沒有影像才提示。`GET /api/videos/{id}/info` 新增 `playback`（`direct` / `try_first` / `transcode`）。
- **審核頁影片預覽**（VD-20）：影片候選改用同一個播放器；`D` / `K` / `R` 的行為與大圖預覽相同（只處理目前這一支、跳到下一個、`Ctrl+Z` 復原）。圖片預覽也支援 `PageUp` / `PageDown`，圖片與影片之間可直接切換。
- **ffmpeg 備援**（VD-21、VD-22）：影片縮圖與 `GET /api/candidates/preview/{asset_id}` 在 OpenCV 讀不到第一幀時改用 ffmpeg 抽幀；video 分析階段在 OpenCV 打不開或缺時長 / 解析度時改用 ffprobe 補讀。
- **影片分數不是 AI 信心度**（VD-30）：`video_same_size` 固定 1.0、`video_tiny` 固定 0.7（規則本來就是這樣，這版改成命名常數並寫進規格）。預覽不再顯示影片的分數，影片分類沒有「分數」排序選項。
- **進度卡片**（UI-07、UI-08、AN-10）：一鍵流水線有自己的六個步驟並隨階段前進；完成後不再顯示「正在處理中」與最後一個階段的 N / N；完成摘要依各階段回傳的欄位計算（hash: done / not_needed；basic: done；clip / video: processed）；分析頁在任務進行中每 2 秒更新；卡片高度回報給頁面、頁面底部依高度留白；卡片位置跟著主選單收合。修正：後端預設的空 `scan_summary` / `stage_stats` 被當成有資料（分析完成時多出空的掃描摘要）、單獨評估完成時不顯示摘要。
- **更名與版本**：頁面標題（`index.html`）與後端應用程式名稱改為「相片清理助手」，後端版本 0.7.0。
- 新增 `npm run test:logic`（播放器按鍵、進度卡片等純邏輯的測試，不需額外套件）。
- 文件：規格書新增 4.12（VD-）、UI-07、UI-08、AN-10 與對應的驗收條件，G-4 的「影片播放」已完成，新增 G-9、G-10；`docs/operations.md` 新增第 4 節（ffmpeg），後面的章節順延；兩份 README、`AGENT.md` 同步。

## v0.6.5 — 2026-10-15

- **大圖預覽**：雙擊卡片、縮圖放大鏡或空白鍵開啟；`←` `→` 沿清單切換；預覽中 `D` / `K` / `R` 只處理目前這一張並跳到下一張；點圖或 `F` 切換適合視窗 / 100%；資訊欄含偵測原因與重複群組的所有成員，可切換比較畫質。（PV-）
- 新 API：`GET /api/candidates/preview/{asset_id}`（瀏覽器原生格式回傳原檔，其他格式與影片縮成最長邊 2560px 的 JPEG）、`GET /api/candidates/group/{group_id}`。
- 應用程式更名為「**相片清理助手**」。
- **文件整理**：README 精簡並拆出 `README.zh-TW.md`；新增 `docs/operations.md`；功能規格改為穩定檔名 `docs/functional-spec.md` 並移除預設值與完整 API 清單；各版 `CHANGELOG-v*.md` 合併為本檔，舊檔移到 `docs/archive/`；重寫 `AGENT.md`。

## v0.6.4 — 2026-10-15

- 主選單與審核篩選欄可各自收合成窄欄；`[` 一次收合 / 展開全部（專注模式）；狀態記在瀏覽器。（UI-01 至 UI-04）

## v0.6.3 — 2026-10-15

- **審核工作台重寫**（RV-）：後端分頁與統計（`GET /api/candidates/summary`、`/page`）、無限捲動；狀態變更直接更新畫面並可復原（`POST /api/candidates/undo`）；Shift 範圍選取；「選取符合條件的全部 N 張」→ 預覽 → 確認（`POST /api/candidates/bulk-status`，帶 `expected_count`）；分數門檻、排序、卡片大小；新增「連拍」「影片」分類。
- **螢幕截圖 / 文件不受「群組至少保留一張」保護**（RV-31）：請求帶 `waive_protection_for` 才生效。
- 行為變更：「全部候選」預設隱藏建議保留的相片，數量與側邊欄、首頁一致；`POST /api/candidates/batch-status` 的回應多了 `undo_token`、`updated_asset_ids`。

## v0.6.2 — 2026-10-15

- 修正設定頁無法捲動：頁面自己負責捲動。（UI-05）

## v0.6.1 — 2026-10-15

- **設定頁重新設計**（ST-）：分區、只送出有變更的項目、**只有變更了會影響評估的設定時才重新評估**（效能類設定不評估，分析進行中也能儲存）、顯示後端真正的錯誤訊息、未儲存變更提示、每個欄位可還原預設值。
- 設定頁新增：近似重複比對方式、CLIP 近似門檻、連拍設定、CLIP 批次大小；移除刪除模式的介面（一律資源回收筒）。
- 新 API：`GET /api/settings/meta`（預設值、範圍、選項、不影響評估的設定鍵）。`PUT /api/settings` 與 `POST /api/settings/reset` 的回應多了 `changed`、`evaluated`；值沒有變的 PUT 不再評估。

## v0.6 — 2026-10-15

- **評估狀態追蹤**：相片庫、分析結果、會影響評估的設定都沒變時自動略過評估；回報每次評估新增 / 移除幾張；提示「有 N 張相片在上次評估後完成分析」。（EV-09）
- **一鍵流水線**：`POST /api/jobs/pipeline`，掃描 → 全部分析 → 評估，單一任務，掃描與分析期間可取消。
- `GET /api/workflow/status`；首頁與資料庫頁重新設計：統計卡、「主要分類」（每張只算一次，加總 = 待審核總數）、即時處理流程、逐階段列出待處理數。（DB-）
- 修正：評估期間取消不再誤報「已取消」（評估不可取消）；任務進行中儲存設定或還原預設時，先回 409 才寫入。

## v0.5 — 2026-10-14

- **資料安全**：Keeper 保護改為以資產為單位檢查所屬的每個群組（封住三種繞過方式）；刪除前最後一道防線；刪前校驗改為大小**或**修改時間任一不符就不刪；永久刪除需帶 `confirm_text`，分析或評估進行中不能刪除。
- **不再寫入假資料**：任何失敗寫入 `analysis_failures`，不再用隨機向量、假 pHash 冒充成功。
- **GPU 批次 CLIP 管線**：loader 執行緒 → 單一批次推理 → 背景批次寫入；顯存不足自動減半；補邊成正方形再縮放；`scripts/bench_clip.py` 測速。
- **basic 階段每張相片只解碼一次**；模糊 / pHash / 亮度改在最長邊 1024px 的縮小圖上計算（模糊門檻的意義因此改變）；拍攝時間改讀 EXIF `DateTimeOriginal`。
- **近似重複與連拍**改為向量化規則引擎（`near_dup_method`、`clip_dup_threshold`、`near_dup_max_group`；連拍規則；影片的完全重複與極小影片規則）；每個資產只屬於一個群組。
- **任務與本機安全**：任務啟動原子化、SSE thread-safe；只接受本機 Host，拒絕其他網站來源的寫入請求；設定預設值集中到 `core/defaults.py` 並驗證；SQLite WAL 與連線池調整；CUDA 安裝依顯示卡自動挑選版本（RTX 50 系列使用 cu128）。
- 測試改用暫存資料庫與假 CLIP 後端，並新增 Keeper 保護、刪前校驗、失敗處理、OOM 減半、近似分組、任務互斥、安全防護與設定驗證等測試。

## v0.4 及更早

初版功能（Library / Review / Process / History / Settings / Dashboard）、AI 語義分類，以及 v0.4 的掃描與分析拆分，沒有逐版的變更紀錄；見 git 歷史。
