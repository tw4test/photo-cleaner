# 相片瘦身 WebApp (Photo Cleaner)

本地運行的相片庫整理與瘦身工具。透過 AI 特徵分析與規則引擎，找出相片庫中的完全重複、近似重複、連拍多餘、模糊與曝光不良等廢片，提供流暢的縮圖虛擬滾動工作台供人工審核，並以安全的資源回收筒機制執行批量清理。

---

## 🛡️ 核心防護原則（鐵則）

1. **只有 Process 頁能刪檔**：Review 審核頁面的所有操作僅變更資料庫狀態，絕不觸碰實體檔案。
2. **預設安全刪除**：使用 Windows 資源回收筒（`send2trash`），刪除後隨時可於桌面回收筒還原。
3. **保留最後一張保護**：任何重複或連拍分組中，系統強制禁止全選刪除，確保至少保留一份最佳檔案。
4. **配對檔案一併處置**：Live Photo（HEIC/JPG + MOV）與 RAW + JPG 自動綁定為單一資產，刪除與保留連動操作。
5. **資料全程不離開本機**：服務僅綁定 `127.0.0.1`，無任何外部網路連線與遙測。

---

## 🛠️ 技術架構

- **後端**：Python 3.10+、FastAPI、SQLAlchemy、SQLite（WAL 模式）、Pillow、pillow-heif、OpenCV、imagehash
- **前端**：React 18、Vite、TypeScript、Tailwind CSS、TanStack Virtual（虛擬滾動）、Lucide React
- **AI / 向量（規劃中）**：open_clip / SigLIP、FAISS / sqlite-vec（目前處於 P1 Mock/Stub 階段）

---

## 🚀 快速啟動

### 1. 後端 (Backend)

```bash
cd backend

# 建立並啟用虛擬環境 (Windows)
python -m venv venv
.\venv\Scripts\activate

# 安裝依賴套件
pip install -r requirements.txt

# 啟動 FastAPI 服務 (預設監聽 127.0.0.1:8000)
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 2. 前端 (Frontend)

```bash
cd frontend

# 安裝 Node 套件
npm install

# 啟動開發伺服器 (http://127.0.0.1:5173，API 自動 Proxy 至 8000)
npm run dev
```

---

## 📂 專案結構

```
photo-cleaner/
├── backend/                  # FastAPI 後端
│   ├── app/
│   │   ├── api/              # RESTful API 路由 (roots, scan, thumbnails)
│   │   ├── core/             # 資料庫 WAL 連線與環境配置 (127.0.0.1)
│   │   ├── models/           # SQLite 資料模型與列舉定義
│   │   ├── services/         # 掃描器、資產配對、縮圖快取、特徵提取
│   │   └── stubs/            # AI / GPU Mock 模組 (TODO: GPU)
│   └── tests/                # 單元測試
├── frontend/                 # React + Vite 前端
│   └── src/
│       ├── components/       # 側邊欄導航與通用元件
│       └── pages/            # 5 大功能頁面 (Dashboard, Library, Review, Process, History)
├── photo-cleaner-functional-spec-0.3.md # 功能規格書
└── AGENT.md                  # 開發規範與鐵則
```
