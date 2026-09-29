import React, { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { FolderPlus, Play, Folder, Trash2, CheckCircle2, AlertCircle } from 'lucide-react'
import { api } from '../services/api'
import { RootItem, ScanProgressState } from '../services/types'
import { useScanProgress } from '../hooks/useScanProgress'
import { ScanProgressBar } from '../components/common/ScanProgressBar'

export const Library: React.FC = () => {
  const navigate = useNavigate()
  const [roots, setRoots] = useState<RootItem[]>([])
  const [newPath, setNewPath] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [showProgressCard, setShowProgressCard] = useState(true)

  const loadRoots = useCallback(async () => {
    try {
      setLoading(true)
      const data = await api.getRoots()
      setRoots(data)
    } catch (err) {
      console.error('Failed to load roots:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  // Hook for real-time scanning progress tracking (SSE + Polling fallback)
  const { progress, isScanning, cancelScan } = useScanProgress(
    useCallback((_completedState: ScanProgressState) => {
      loadRoots()
      setShowProgressCard(true)
    }, [loadRoots])
  )

  useEffect(() => {
    loadRoots()
  }, [loadRoots])

  const handleAddRoot = async () => {
    if (!newPath.trim()) return
    try {
      setMessage(null)
      await api.createRoot({ path: newPath.trim() })
      setNewPath('')
      setMessage({ type: 'success', text: '已成功新增掃描目錄！' })
      loadRoots()
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '新增目錄失敗，請確認路徑是否存在。'
      setMessage({ type: 'error', text: errorMsg })
    }
  }

  const handleDeleteRoot = async (id: number) => {
    try {
      await api.deleteRoot(id)
      loadRoots()
    } catch (err) {
      console.error('Failed to delete root:', err)
    }
  }

  const handleTriggerScan = async (rootId?: number) => {
    try {
      setMessage(null)
      setShowProgressCard(true)
      if (rootId) {
        await api.triggerScan(rootId)
      } else {
        await api.triggerScanAll()
      }
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '啟動掃描失敗。'
      setMessage({ type: 'error', text: errorMsg })
    }
  }

  return (
    <div className="p-8 space-y-8 overflow-y-auto max-w-7xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-100">資料庫掃描（Library）</h2>
          <p className="text-slate-400 text-sm mt-1">管理相片掃描目錄、路徑優先順序與即時透明掃描</p>
        </div>
        <button
          disabled={roots.length === 0 || isScanning}
          onClick={() => handleTriggerScan()}
          className="flex items-center gap-2 px-4 py-2 bg-sky-600 hover:bg-sky-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl text-sm font-medium transition-all shadow-lg shadow-sky-600/20 cursor-pointer"
        >
          <Play className="w-4 h-4" />
          <span>{isScanning ? '掃描進行中...' : '開始全庫掃描'}</span>
        </button>
      </div>

      {/* Real-time Transparent Progress Bar Card */}
      {showProgressCard && (
        <ScanProgressBar
          progress={progress}
          onCancel={cancelScan}
          onDismiss={() => setShowProgressCard(false)}
          onNavigateReview={() => navigate('/review')}
        />
      )}

      {/* Notice Message */}
      {message && (
        <div className={`p-4 rounded-xl text-sm flex items-center gap-2 border ${
          message.type === 'success' 
            ? 'bg-emerald-950/40 text-emerald-300 border-emerald-800/60' 
            : 'bg-rose-950/40 text-rose-300 border-rose-800/60'
        }`}>
          {message.type === 'success' ? <CheckCircle2 className="w-4 h-4 shrink-0" /> : <AlertCircle className="w-4 h-4 shrink-0" />}
          <span>{message.text}</span>
        </div>
      )}

      {/* Add Path Input */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
        <h3 className="text-base font-semibold text-slate-200">新增相片來源目錄</h3>
        <div className="flex gap-3">
          <input
            type="text"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAddRoot()}
            placeholder="請輸入本機絕對路徑，例如：D:\Photos\2023 或 D:\Pictures"
            className="flex-1 bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500 font-mono"
          />
          <button
            onClick={handleAddRoot}
            className="flex items-center gap-2 px-5 py-2.5 bg-sky-600 hover:bg-sky-500 text-white rounded-xl text-sm font-medium transition-colors shadow-md shadow-sky-600/20 cursor-pointer"
          >
            <FolderPlus className="w-4 h-4" />
            <span>加入路徑</span>
          </button>
        </div>
      </div>

      {/* Path List */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-slate-200">已登錄掃描路徑清單</h3>
          <span className="text-xs text-slate-500">提示：越上方的路徑在重複比對時享有更高的保留優先權</span>
        </div>

        {loading ? (
          <div className="text-center py-12 text-slate-500">載入中...</div>
        ) : roots.length === 0 ? (
          <div className="text-center py-12 border border-dashed border-slate-800 rounded-xl space-y-3">
            <Folder className="w-10 h-10 text-slate-600 mx-auto" />
            <p className="text-sm text-slate-400">目前尚無已登錄路徑，請在上方輸入資料夾路徑</p>
          </div>
        ) : (
          <div className="divide-y divide-slate-800/80">
            {roots.map((root, index) => (
              <div key={root.id} className="py-4 flex items-center justify-between first:pt-0 last:pb-0">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 bg-slate-800 text-sky-400 rounded-md text-xs font-mono font-bold">
                      優先序 #{index + 1}
                    </span>
                    <span className="font-mono text-sm text-slate-200 font-semibold">{root.path}</span>
                  </div>
                  <div className="text-xs text-slate-500">
                    上次掃描時間：{root.last_scan_at ? new Date(root.last_scan_at).toLocaleString('zh-TW') : '尚未掃描'}
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleTriggerScan(root.id)}
                    disabled={isScanning}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-200 rounded-lg text-xs font-medium border border-slate-700 transition-colors cursor-pointer"
                  >
                    <Play className="w-3.5 h-3.5 text-sky-400" />
                    <span>掃描此目錄</span>
                  </button>
                  <button
                    onClick={() => handleDeleteRoot(root.id)}
                    disabled={isScanning}
                    className="p-1.5 text-slate-500 hover:text-rose-400 hover:bg-rose-950/20 disabled:opacity-40 rounded-lg transition-colors cursor-pointer"
                    title="移除路徑"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
