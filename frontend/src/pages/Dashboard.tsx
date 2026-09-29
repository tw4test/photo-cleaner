import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { 
  Images, 
  Trash2, 
  HardDrive, 
  Sparkles, 
  ArrowRight, 
  CheckCircle2, 
  AlertCircle,
  RefreshCw
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
          <p className="text-slate-400 text-sm mt-1">本地相片庫分析與瘦身建議統計</p>
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
            <span className="text-slate-400 text-sm font-medium">總掃描資產</span>
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
              完全重複 {stats?.candidates.exact_dup ?? 0} ｜ 模糊 {stats?.candidates.blur ?? 0} ｜ 曝光異常 {stats?.candidates.exposure ?? 0}
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
              總資料庫佔比 {stats && stats.assets.total_size_bytes > 0 ? ((stats.candidates.releasable_size_bytes / stats.assets.total_size_bytes) * 100).toFixed(1) : 0}%
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

      {/* Quick Action Banner */}
      <div className="bg-gradient-to-r from-sky-950/40 via-slate-900 to-slate-900 border border-sky-800/40 rounded-2xl p-6 flex items-center justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-sky-400 font-semibold text-base">
            <CheckCircle2 className="w-5 h-5" />
            <span>
              {(stats?.assets.total ?? 0) === 0 ? '目前尚未掃描任何相片資產' : `已完成 ${stats?.assets.total} 張相片分析`}
            </span>
          </div>
          <p className="text-slate-400 text-sm">
            {(stats?.assets.total ?? 0) === 0
              ? '請先至「資料庫掃描」頁面加入本機相片資料夾並執行掃描。'
              : `偵測到 ${stats?.candidates.total} 個候選瘦身項目，點擊前往審核工作台確認。`}
          </p>
        </div>
        <Link
          to={(stats?.assets.total ?? 0) === 0 ? "/library" : "/review"}
          className="flex items-center gap-2 px-5 py-2.5 bg-sky-600 hover:bg-sky-500 text-white rounded-xl font-medium transition-all shadow-lg shadow-sky-600/20"
        >
          <span>{(stats?.assets.total ?? 0) === 0 ? "前往設定路徑" : "前往審核工作台"}</span>
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
