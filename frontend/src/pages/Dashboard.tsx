import React from 'react'
import { Link } from 'react-router-dom'
import { 
  Images, 
  Trash2, 
  HardDrive, 
  Sparkles, 
  ArrowRight, 
  CheckCircle2, 
  AlertCircle 
} from 'lucide-react'

export const Dashboard: React.FC = () => {
  return (
    <div className="p-8 space-y-8 overflow-y-auto max-w-7xl mx-auto w-full">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-bold text-slate-100">首頁總覽</h2>
        <p className="text-slate-400 text-sm mt-1">本地相片庫分析與瘦身建議統計</p>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 relative overflow-hidden">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">總掃描資產</span>
            <Images className="w-5 h-5 text-sky-400" />
          </div>
          <div className="mt-4">
            <div className="text-3xl font-bold text-slate-100 font-mono">0</div>
            <div className="text-xs text-slate-500 mt-1">相片 0 ｜ RAW 0 ｜ 影片 0</div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 relative overflow-hidden">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">候選清理項目</span>
            <Sparkles className="w-5 h-5 text-amber-400" />
          </div>
          <div className="mt-4">
            <div className="text-3xl font-bold text-amber-400 font-mono">0</div>
            <div className="text-xs text-slate-500 mt-1">完全重複 0 ｜ 近似 0 ｜ 廢片 0</div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 relative overflow-hidden">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">預計可釋放容量</span>
            <HardDrive className="w-5 h-5 text-emerald-400" />
          </div>
          <div className="mt-4">
            <div className="text-3xl font-bold text-emerald-400 font-mono">0.00 GB</div>
            <div className="text-xs text-slate-500 mt-1">佔比 0%</div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 relative overflow-hidden">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">累計已瘦身容量</span>
            <Trash2 className="w-5 h-5 text-purple-400" />
          </div>
          <div className="mt-4">
            <div className="text-3xl font-bold text-purple-400 font-mono">0.00 GB</div>
            <div className="text-xs text-slate-500 mt-1">已執行 0 批次</div>
          </div>
        </div>
      </div>

      {/* Quick Action Banner */}
      <div className="bg-gradient-to-r from-sky-950/40 via-slate-900 to-slate-900 border border-sky-800/40 rounded-2xl p-6 flex items-center justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-sky-400 font-semibold text-base">
            <CheckCircle2 className="w-5 h-5" />
            <span>目前尚未加入任何掃描路徑</span>
          </div>
          <p className="text-slate-400 text-sm">
            請先至「資料庫掃描」頁面加入本機相片資料夾，系統將進行差異掃描與配對。
          </p>
        </div>
        <Link
          to="/library"
          className="flex items-center gap-2 px-5 py-2.5 bg-sky-600 hover:bg-sky-500 text-white rounded-xl font-medium transition-all shadow-lg shadow-sky-600/20"
        >
          <span>前往設定路徑</span>
          <ArrowRight className="w-4 h-4" />
        </Link>
      </div>

      {/* System Status */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
        <h3 className="font-semibold text-slate-200 text-base">系統防護與運行狀態</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
          <div className="flex items-start gap-3 p-4 bg-slate-950/60 rounded-xl border border-slate-800">
            <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
            <div>
              <div className="font-medium text-slate-200">只有 Process 頁能動檔案</div>
              <div className="text-xs text-slate-500 mt-0.5">Review 審核操作僅變更資料庫狀態</div>
            </div>
          </div>
          <div className="flex items-start gap-3 p-4 bg-slate-950/60 rounded-xl border border-slate-800">
            <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
            <div>
              <div className="font-medium text-slate-200">資源回收筒安全刪除</div>
              <div className="text-xs text-slate-500 mt-0.5">預設使用 send2trash，隨時可在 Windows 還原</div>
            </div>
          </div>
          <div className="flex items-start gap-3 p-4 bg-slate-950/60 rounded-xl border border-slate-800">
            <AlertCircle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
            <div>
              <div className="font-medium text-slate-200">AI 模組運行於 Mock 模式</div>
              <div className="text-xs text-slate-500 mt-0.5">尚未載入 GPU 模型，使用虛擬特徵驗證流程</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
