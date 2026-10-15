# 相片清理助手 (Photo Cleaner)

[English](README.md) | **繁體中文**

本地運行的相片庫清理工具，針對大型相片庫（約 10 萬張、300 GB，可放在 NAS）設計。它會找出重複、近似重複、連拍多餘、螢幕截圖、文件 / 收據、梗圖、誤拍，以及模糊或曝光不良的相片。你先審核建議，確認之後才會把檔案移到資源回收筒。

![審核工作台](docs/images/review-workbench.png)

## 它能做什麼

- **用 AI 與規則找出雜物。** SHA-256、感知雜湊、清晰度與亮度檢查，加上 CLIP 零樣本分類（有 GPU 時自動加速）。
- **撐得住大型相片庫。** 分頁與統計都在後端完成，10 萬筆候選時審核頁仍然流暢。
- **審核很快。** 大圖預覽、快速鍵、範圍選取、「選取符合條件的全部」（先預覽、再確認），以及復原。
- **你掌握節奏。** 掃描、分析、評估是獨立的步驟，可以單獨執行、重試或續跑。
- **配對檔視為同一張。** Live Photo（HEIC/JPG + MOV）與 RAW + JPG 一定一起處理。

## 安全

- **在「執行刪除」頁確認之前，不會刪除任何檔案。** 審核、預覽、設定只會改變程式自己的資料庫。
- **刪除的檔案移到資源回收筒。** 介面不提供永久刪除。
- **每個重複群組至少保留一張**（螢幕截圖與文件是唯一刻意的例外）。
- **一切都留在你的電腦上。** 後端只監聽 `127.0.0.1`，沒有任何遙測。

完整規則見 [`docs/functional-spec.md`](docs/functional-spec.md#7-安全防護)。

> ⚠️ **NAS 使用者：** 網路磁碟通常不會進資源回收筒，檔案可能直接被刪除。請先用一張測試檔確認；在 NAS 上的安全刪除完成之前，不建議用本程式刪除 NAS 上的相片。

## 快速啟動

需求：Python 3.10+、Node.js 18+。在 Windows（PowerShell）開發與測試。

```powershell
# 後端
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（另開一個終端機）
cd frontend
npm install
npm run dev
```

開啟 <http://127.0.0.1:5173>，在「資料庫掃描」頁加入相片資料夾並執行一鍵流水線。API 文件在 <http://127.0.0.1:8000/docs>。

GPU 加速、環境變數、效能建議與測試請看 [`docs/operations.md`](docs/operations.md)。

## 文件

| 文件 | 用途 |
|---|---|
| [`docs/functional-spec.md`](docs/functional-spec.md) | 系統應該怎麼運作（需求、規則、驗收條件、已知差距） |
| [`docs/operations.md`](docs/operations.md) | 安裝細節、設定、GPU、效能、NAS、測試 |
| [`CHANGELOG.md`](CHANGELOG.md) | 各版本的變更 |
| [`AGENT.md`](AGENT.md) | 給 AI 程式助手與新貢獻者的工作規則 |

## 目前狀態

持續開發中，版本見 [`CHANGELOG.md`](CHANGELOG.md)。有兩件事尚未完成：偵測門檻還沒有用真實相片校準，以及在 NAS 上安全刪除。詳見規格書的已知差距。
