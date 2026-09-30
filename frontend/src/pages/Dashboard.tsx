import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { 
  Images, 
  Trash2, 
  HardDrive, 
  Sparkles, 
  ArrowRight, 
  CheckCircle2, 
  RefreshCw,
  Smartphone,
  FileText,
  Laugh,
  EyeOff
} from 'lucide-react'
import { api } from '../services/api'
import { DashboardStats } from '../services/types'

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0.00 MB'
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(2)} ${sizes[i]}`
}

export const Dashboard: React.FC = () => {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [loading, setLoading] = useState<boolean>(true)

  const loadStats = async () => {
    try {
      setLoading(true)
      const data = await api.getDashboardStats()
      setStats(data)
    } catch (err) {
      console.error('Failed to load dashboard stats:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadStats()
  }, [])

  return (
    <div className="p-8 space-y-8 overflow-y-auto max-w-7xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-100">首頁總覽</h2>
          <p className="text-slate-400 text-sm mt-1">本地相片庫 AI 語義內容分析與清理建議</p>
        </div>
        <button
          onClick={loadStats}
          className="flex items-center gap-2 px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-medium border border-slate-700 transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          <span>重新整理統計</span>
        </button>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 relative overflow-hidden">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">總相片庫資產</span>
            <Images className="w-5 h-5 text-sky-400" />
          </div>
          <div className="mt-4">
            <div className="text-3xl font-bold text-slate-100 font-mono">
              {stats?.assets.total ?? 0}
            </div>
            <div className="text-xs text-slate-500 mt-1">
              相片 {stats?.assets.photo_count ?? 0} ｜ RAW {stats?.assets.raw_count ?? 0} ｜ 影片 {stats?.assets.video_count ?? 0}
            </div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 relative overflow-hidden">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">候選清理項目</span>
            <Sparkles className="w-5 h-5 text-amber-400" />
          </div>
          <div className="mt-4">
            <div className="text-3xl font-bold text-amber-400 font-mono">
              {stats?.candidates.total ?? 0}
            </div>
            <div className="text-xs text-slate-500 mt-1">
              AI 語義雜圖 {(stats?.candidates.screenshot ?? 0) + (stats?.candidates.document ?? 0) + (stats?.candidates.meme ?? 0) + (stats?.candidates.mistouch ?? 0)} ｜ 重複/模糊 {(stats?.candidates.exact_dup ?? 0) + (stats?.candidates.near_dup ?? 0) + (stats?.candidates.blur ?? 0)}
            </div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 relative overflow-hidden">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">預計可釋放容量</span>
            <HardDrive className="w-5 h-5 text-emerald-400" />
          </div>
          <div className="mt-4">
            <div className="text-3xl font-bold text-emerald-400 font-mono">
              {formatBytes(stats?.candidates.releasable_size_bytes ?? 0)}
            </div>
            <div className="text-xs text-slate-500 mt-1">
              佔總容量 {stats && stats.assets.total_size_bytes > 0 ? ((stats.candidates.releasable_size_bytes / stats.assets.total_size_bytes) * 100).toFixed(1) : 0}%
            </div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 relative overflow-hidden">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 text-sm font-medium">累計已瘦身容量</span>
            <Trash2 className="w-5 h-5 text-purple-400" />
          </div>
          <div className="mt-4">
            <div className="text-3xl font-bold text-purple-400 font-mono">
              {formatBytes(stats?.history.deleted_size_bytes ?? 0)}
            </div>
            <div className="text-xs text-slate-500 mt-1">
              已執行 {stats?.history.deleted_batches ?? 0} 批次
            </div>
          </div>
        </div>
      </div>

      {/* AI Semantic Categories Breakdown Cards */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-slate-100 text-base flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-amber-400" />
            <span>AI 內容語義分類候選分佈（CLIP 智能識別）</span>
          </h3>
          <Link to="/review" className="text-xs text-sky-400 hover:underline flex items-center gap-1">
            <span>前往審核工作台</span>
            <ArrowRight className="w-3 h-3" />
          </Link>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Link
            to="/review"
            className="p-4 bg-slate-950/70 border border-slate-800 hover:border-sky-500/50 rounded-xl space-y-2 transition-colors group"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400 group-hover:text-slate-200">螢幕截圖</span>
              <Smartphone className="w-4 h-4 text-sky-400" />
            </div>
            <div className="text-2xl font-bold font-mono text-slate-100">
              {stats?.candidates.screenshot ?? 0}
            </div>
            <div className="text-[10px] text-slate-500">聊天紀錄 / 應用擷取</div>
          </Link>

          <Link
            to="/review"
            className="p-4 bg-slate-950/70 border border-slate-800 hover:border-emerald-500/50 rounded-xl space-y-2 transition-colors group"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400 group-hover:text-slate-200">文件 / 收據</span>
              <FileText className="w-4 h-4 text-emerald-400" />
            </div>
            <div className="text-2xl font-bold font-mono text-slate-100">
              {stats?.candidates.document ?? 0}
            </div>
            <div className="text-[10px] text-slate-500">發票 / 報表 / 白板筆記</div>
          </Link>

          <Link
            to="/review"
            className="p-4 bg-slate-950/70 border border-slate-800 hover:border-purple-500/50 rounded-xl space-y-2 transition-colors group"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400 group-hover:text-slate-200">梗圖 / 迷因</span>
              <Laugh className="w-4 h-4 text-purple-400" />
            </div>
            <div className="text-2xl font-bold font-mono text-slate-100">
              {stats?.candidates.meme ?? 0}
            </div>
            <div className="text-[10px] text-slate-500">網路貼圖 / 搞笑圖片</div>
          </Link>

          <Link
            to="/review"
            className="p-4 bg-slate-950/70 border border-slate-800 hover:border-rose-500/50 rounded-xl space-y-2 transition-colors group"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400 group-hover:text-slate-200">誤拍 / 廢片</span>
              <EyeOff className="w-4 h-4 text-rose-400" />
            </div>
            <div className="text-2xl font-bold font-mono text-slate-100">
              {stats?.candidates.mistouch ?? 0}
            </div>
            <div className="text-[10px] text-slate-500">口袋純黑 / 地板 / 手指遮擋</div>
          </Link>
        </div>
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
            <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
            <div>
              <div className="font-medium text-slate-200">AI 語義模型與特徵管線已就緒</div>
              <div className="text-xs text-slate-500 mt-0.5">本地 PyTorch (open_clip) + OpenCV 混合加速運算</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
