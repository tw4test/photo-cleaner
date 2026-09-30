import React, { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { CheckCircle2, AlertCircle, RefreshCw } from 'lucide-react'
import { api } from '../services/api'
import { RootItem, AnalysisStatus, ScanSummary, ScanProgressState } from '../services/types'
import { useScanProgress } from '../hooks/useScanProgress'
import { ScanProgressBar } from '../components/common/ScanProgressBar'
import { ScanPanel } from '../components/scan/ScanPanel'
import { AnalysisPanel } from '../components/scan/AnalysisPanel'
import { EvaluatePanel } from '../components/scan/EvaluatePanel'

export const Library: React.FC = () => {
  const navigate = useNavigate()
  const [roots, setRoots] = useState<RootItem[]>([])
  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatus | null>(null)
  const [scanSummary, setScanSummary] = useState<ScanSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [statusLoading, setStatusLoading] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [showProgressCard, setShowProgressCard] = useState(false)

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

  const loadAnalysisStatus = useCallback(async () => {
    try {
      setStatusLoading(true)
      const data = await api.getAnalysisStatus()
      setAnalysisStatus(data)
    } catch (err) {
      console.error('Failed to load analysis status:', err)
    } finally {
      setStatusLoading(false)
    }
  }, [])

  const loadAll = useCallback(() => {
    loadRoots()
    loadAnalysisStatus()
  }, [loadRoots, loadAnalysisStatus])

  // Hook for real-time task progress tracking (SSE + Polling fallback)
  const { progress, isScanning, cancelScan } = useScanProgress(
    useCallback((completedState: ScanProgressState) => {
      loadAll()
      if (completedState.scan_summary) {
        setScanSummary(completedState.scan_summary)
      }
      setShowProgressCard(true)
    }, [loadAll])
  )

  useEffect(() => {
    loadAll()
  }, [loadAll])

  const handleAddRoot = async (path: string) => {
    try {
      setMessage(null)
      await api.createRoot({ path })
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

  const handleTriggerScan = async (mode: 'full' | 'new_only', rootId?: number) => {
    try {
      setMessage(null)
      setShowProgressCard(true)
      await api.triggerJobScan(mode, rootId)
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '啟動掃描失敗。'
      setMessage({ type: 'error', text: errorMsg })
    }
  }

  const handleTriggerAnalyze = async (stages: string[], device: string, retryFailed: boolean) => {
    try {
      setMessage(null)
      setShowProgressCard(true)
      await api.triggerJobAnalyze(stages, device, retryFailed)
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '啟動特徵分析失敗。'
      setMessage({ type: 'error', text: errorMsg })
    }
  }

  const handleTriggerRebuildThumbs = async () => {
    try {
      setMessage(null)
      setShowProgressCard(true)
      await api.triggerRebuildThumbs()
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '啟動縮圖補算失敗。'
      setMessage({ type: 'error', text: errorMsg })
    }
  }

  const handleTriggerEvaluate = async () => {
    try {
      setMessage(null)
      setShowProgressCard(true)
      await api.triggerEvaluate()
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '啟動規則評估失敗。'
      setMessage({ type: 'error', text: errorMsg })
    }
  }

  const handleTriggerFullPipeline = async () => {
    try {
      setMessage(null)
      setShowProgressCard(true)
      // 1. 觸發完整掃描
      await api.triggerJobScan('full')
      // 伺服器會在完成後由使用者或串接後續分析
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || '啟動流水線失敗。'
      setMessage({ type: 'error', text: errorMsg })
    }
  }

  return (
    <div className="p-8 space-y-8 overflow-y-auto max-w-7xl mx-auto w-full pb-36">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-100">資料庫與分析工作台（Library & Analysis）</h2>
          <p className="text-slate-400 text-sm mt-1">
            目錄掃描、特徵提取與規則評估徹底獨立運作，支援精細控制與即時透明進度追蹤
          </p>
        </div>
        <button
          onClick={loadAll}
          className="flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-medium border border-slate-700 transition-colors cursor-pointer"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading || statusLoading ? 'animate-spin' : ''}`} />
          <span>重新整理全頁</span>
        </button>
      </div>

      {/* Real-time Transparent Progress Bar Card */}
      {(showProgressCard || isScanning) && (
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

      {/* Panel 1: Scan Panel */}
      <ScanPanel
        roots={roots}
        loading={loading}
        isScanning={isScanning}
        scanSummary={scanSummary}
        onAddRoot={handleAddRoot}
        onDeleteRoot={handleDeleteRoot}
        onTriggerScan={handleTriggerScan}
      />

      {/* Panel 2: Analysis Panel */}
      <AnalysisPanel
        status={analysisStatus}
        loading={statusLoading}
        isAnalyzing={isScanning}
        onTriggerAnalyze={handleTriggerAnalyze}
        onTriggerRebuildThumbs={handleTriggerRebuildThumbs}
        onRefreshStatus={loadAnalysisStatus}
      />

      {/* Panel 3: Evaluate Panel */}
      <EvaluatePanel
        isRunning={isScanning}
        onTriggerEvaluate={handleTriggerEvaluate}
        onTriggerFullPipeline={handleTriggerFullPipeline}
      />
    </div>
  )
}
