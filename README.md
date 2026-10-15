# Photo Cleaner (相片清理助手)

**English** | [繁體中文](README.zh-TW.md)

A local web app that helps you clean up a large photo library (about 100k photos / 300 GB, on a local disk or a NAS). It finds duplicates, near duplicates, burst leftovers, screenshots, documents and receipts, memes, accidental shots, and blurry or badly exposed photos. You review the suggestions, and only then are files moved to the Recycle Bin.

> The interface is in Traditional Chinese only.

![Review workbench](docs/images/review-workbench.png)

## What it does

- **Finds clutter with AI and rules.** SHA-256, perceptual hashes, sharpness and brightness checks, plus CLIP zero-shot classification (GPU-accelerated when available).
- **Handles big libraries.** Server-side paging and counts, so the review page stays fast with 100k candidates.
- **Makes review quick.** Full-size preview, keyboard shortcuts, range select, "select everything that matches" with a preview and confirmation, and undo.
- **Keeps you in control.** Scan, analyze and evaluate are separate steps you can run, retry or resume on their own.
- **Treats pairs as one photo.** Live Photo (HEIC/JPG + MOV) and RAW + JPG are always handled together.

## Safety

- **Nothing is deleted until you confirm on the Process page.** Review, preview and settings only change the app's own database.
- **Deleted files go to the Recycle Bin.** Permanent deletion is not offered in the UI.
- **Every duplicate group keeps at least one photo** (screenshots and documents are the one deliberate exception).
- **Everything stays on your machine.** The backend listens on `127.0.0.1` only and sends no telemetry.

The full list of rules is in [`docs/functional-spec.md`](docs/functional-spec.md#7-安全防護).

> ⚠️ **NAS users:** on network drives, the Recycle Bin is usually not used and files may be removed outright. Test with a single file first, and avoid deleting photos on a NAS from the app until NAS-safe deletion is finished.

## Quick start

Requirements: Python 3.10+ and Node.js 18+. Developed and tested on Windows (PowerShell).

```powershell
# Backend
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173>, add a photo folder on the **資料庫掃描** page, and run the pipeline. API docs are at <http://127.0.0.1:8000/docs>.

GPU acceleration, environment variables, performance tips and tests are covered in [`docs/operations.md`](docs/operations.md) (Traditional Chinese).

## Documentation

| Document | What it is for |
|---|---|
| [`docs/functional-spec.md`](docs/functional-spec.md) | How the system is supposed to behave (requirements, rules, acceptance criteria, known gaps) |
| [`docs/operations.md`](docs/operations.md) | Setup details, configuration, GPU, performance, NAS, testing |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed in each version |
| [`AGENT.md`](AGENT.md) | Working rules for AI coding assistants and new contributors |

## Status

Under active development; the version is in [`CHANGELOG.md`](CHANGELOG.md). Two things are not finished: the detection thresholds have not been calibrated on real photo sets, and deleting photos on a NAS is not yet safe. See the known gaps in the spec.
