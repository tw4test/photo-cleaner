import React, { useState, useEffect } from 'react'
import { 
  Trash2, 
  ShieldCheck, 
  AlertTriangle, 
  CheckCircle2, 
  RefreshCw, 
  FileText, 
  Layers, 
  ArrowRight,
  Loader2
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { QueuedSummary, DeletionResult } from '../services/types'

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0.00 MB'
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(2)} ${sizes[i]}`
}

export const Process: React.FC = () => {
  const [summary, setSummary] = useState<QueuedSummary | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [confirmed, setConfirmed] = useState<boolean>(false)
  const [processing, setProcessing] = useState<boolean>(false)
  const [result, setResult] = useState<DeletionResult | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const loadSummary = async () => {
    try {
      setLoading(true)
      const data = await api.getQueuedSummary()
      setSummary(data)
    } catch (err) {
      console.error('Failed to load queued summary:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSummary()
  }, [])

  const handleExecuteDeletion = async () => {
    if (!confirmed || processing) return
    try {
      setProcessing(true)
      setErrorMessage(null)
      const res = await api.executeDeletion({
        confirmed: true,
        mode: 'trash'
      })
      setResult(res)
      setConfirmed(false)
      loadSummary()
    } catch (err: any) {
      const msg = err.response?.data?.detail || '執行刪除時發生錯誤。'
      setErrorMessage(msg)
    } finally {
      setProcessing(false)
    }
  }

  const handleUnqueue = async (assetId: number) => {
    try {
      await api.unqueueItems([assetId])
      loadSummary()
    } catch (err) {
      console.error('Failed to unqueue item:', err)
    }
  }

  return (
    <div className="p-8 space-y-8 overflow-y-auto max-w-7xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-100">執行刪除（Process）</h2>
          <p className="text-slate-400 text-sm mt-1">
            系統唯一會對本機實體檔案執行刪除操作的頁面，預設移入 Windows 資源回收筒
          </p>
        </div>
        <button
          onClick={loadSummary}
          className="flex items-center gap-2 px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-medium border border-slate-700 transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          <span>重新整理清單</span>
        </button>
      </div>

      {/* Execution Result Modal / Banner */}
      {result && (
        <div className="p-6 bg-emerald-950/40 border border-emerald-800/60 rounded-2xl space-y-4 animate-fade-in">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-emerald-500/20 text-emerald-400 rounded-lg">
                <CheckCircle2 className="w-6 h-6" />
              </div>
              <div>
                <h3 className="font-bold text-emerald-300 text-base">清理批次執行完成！</h3>
                <p className="text-xs text-emerald-400/80 mt-0.5">批次編號：{result.batch_id}</p>
              </div>
            </div>
            <Link
              to="/history"
              className="flex items-center gap-1.5 px-4 py-2 bg-emerald-700 hover:bg-emerald-600 text-white rounded-xl text-xs font-semibold shadow-md transition-colors"
            >
              <span>查看歷史記錄</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs font-mono">
            <div className="p-3 bg-emerald-950/60 rounded-xl border border-emerald-900">
              成功移入回收筒：<strong className="text-emerald-200 text-sm">{result.success_count}</strong> 張
            </div>
            <div className="p-3 bg-emerald-950/60 rounded-xl border border-emerald-900">
              釋放磁碟空間：<strong className="text-emerald-200 text-sm">{formatBytes(result.deleted_size_bytes)}</strong>
            </div>
            <div className="p-3 bg-emerald-950/60 rounded-xl border border-emerald-900">
              失敗項目：<strong className={`${result.failed_count > 0 ? 'text-rose-400' : 'text-emerald-200'} text-sm`}>{result.failed_count}</strong>
            </div>
          </div>
        </div>
      )}

      {errorMessage && (
        <div className="p-4 bg-rose-950/40 border border-rose-800/60 rounded-xl text-sm text-rose-300 flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 shrink-0 text-rose-400" />
          <span>{errorMessage}</span>
        </div>
      )}

      {/* Summary Box */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <h3 className="text-base font-semibold text-slate-200">待刪清單摘要</h3>
          <span className={`text-xs px-3 py-1 rounded-full border font-medium ${
            (summary?.total_count ?? 0) > 0 
              ? 'bg-rose-500/20 text-rose-400 border-rose-500/30'
              : 'bg-slate-800 text-slate-400 border-slate-700'
          }`}>
            {summary?.total_count ?? 0} 項待處理
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-sm">
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-xl space-y-1">
            <span className="text-slate-500 text-xs">總檔案數</span>
            <div className="text-2xl font-bold text-slate-100 font-mono">
              {summary?.total_count ?? 0} 張
            </div>
          </div>
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-xl space-y-1">
            <span className="text-slate-500 text-xs">預計釋放磁碟空間</span>
            <div className="text-2xl font-bold text-emerald-400 font-mono">
              {formatBytes(summary?.total_size_bytes ?? 0)}
            </div>
          </div>
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-xl space-y-1">
            <span className="text-slate-500 text-xs">目前刪除模式</span>
            <div className="text-lg font-bold text-sky-400 flex items-center gap-1.5 mt-1">
              <ShieldCheck className="w-5 h-5 text-sky-400" />
              <span>資源回收筒 (send2trash)</span>
            </div>
          </div>
        </div>

        {/* Queued Items List */}
        <div className="space-y-3">
          <h4 className="text-xs font-semibold text-slate-400">待刪檔案細項列表</h4>
          {loading ? (
            <div className="text-center py-8 text-slate-500">載入待刪清單中...</div>
          ) : (summary?.total_count ?? 0) === 0 ? (
            <div className="text-center py-10 border border-dashed border-slate-800 rounded-xl space-y-2">
              <FileText className="w-8 h-8 text-slate-600 mx-auto" />
              <p className="text-xs text-slate-400">目前待刪清單為空，請至 Review 審核頁將相片加入待刪</p>
              <Link to="/review" className="text-xs text-sky-400 hover:underline inline-block mt-1">
                前往審核工作台
              </Link>
            </div>
          ) : (
            <div className="max-h-80 overflow-y-auto divide-y divide-slate-800/80 border border-slate-800 rounded-xl bg-slate-950/40">
              {summary?.items.map((item) => (
                <div key={item.asset_id} className="p-3.5 flex items-center justify-between text-xs">
                  <div className="flex items-center gap-3">
                    <img
                      src={item.thumbnail_url}
                      alt={item.filename}
                      className="w-10 h-10 object-cover rounded-lg border border-slate-800 shrink-0"
                    />
                    <div>
                      <div className="font-mono font-semibold text-slate-200">{item.filename}</div>
                      <div className="text-[11px] text-slate-500 font-mono truncate max-w-lg">
                        {item.primary_path}
                      </div>
                      {item.paired_paths.length > 0 && (
                        <div className="flex items-center gap-1 text-[10px] text-sky-400 mt-0.5">
                          <Layers className="w-3 h-3" />
                          <span>含配對附屬檔案：{item.paired_paths.map(p => p.split(/[\\/]/).pop()).join(', ')}</span>
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-4">
                    <span className="font-mono text-slate-400">{formatBytes(item.size_total)}</span>
                    <button
                      onClick={() => handleUnqueue(item.asset_id)}
                      className="px-2.5 py-1 text-slate-400 hover:text-slate-200 bg-slate-800 hover:bg-slate-700 rounded-lg border border-slate-700 transition-colors"
                    >
                      移出
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Action Button & Confirmation */}
        <div className="border-t border-slate-800 pt-6 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="confirm-check"
              checked={confirmed}
              disabled={(summary?.total_count ?? 0) === 0}
              onChange={(e) => setConfirmed(e.target.checked)}
              className="rounded border-slate-700 bg-slate-950 text-sky-500 focus:ring-sky-500 cursor-pointer disabled:cursor-not-allowed"
            />
            <label htmlFor="confirm-check" className="text-xs text-slate-400 cursor-pointer">
              我已確認待刪清單中的所有相片與配對檔案無誤，同意執行移入資源回收筒
            </label>
          </div>

          <button
            disabled={!confirmed || (summary?.total_count ?? 0) === 0 || processing}
            onClick={handleExecuteDeletion}
            className="flex items-center gap-2 px-6 py-3 bg-rose-600 hover:bg-rose-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl text-sm font-semibold transition-all shadow-lg shadow-rose-600/20"
          >
            {processing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
            <span>{processing ? '安全刪除執行中...' : '立即執行刪除'}</span>
          </button>
        </div>
      </div>
    </div>
  )
}
