import React, { useEffect, useState, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { CheckCircle2, AlertCircle, RefreshCw } from 'lucide-react'
import { api } from '../services/api'
import { RootItem, AnalysisStatus, ScanSummary, ScanProgressState } from '../services/types'
import { useScanProgress } from '../hooks/useScanProgress'
import { useWorkflowStatus } from '../hooks/useWorkflowStatus'
import { workflowApi } from '../services/workflow'
import { ScanProgressBar } from '../components/common/ScanProgressBar'
import { ScanPanel } from '../components/scan/ScanPanel'
import { AnalysisPanel } from '../components/scan/AnalysisPanel'
import { EvaluatePanel } from '../components/scan/EvaluatePanel'
import { WorkflowSteps } from '../components/workflow/WorkflowSteps'

export const Library: React.FC = () => {
  const navigate = useNavigate()
  const [roots, setRoots] = useState<RootItem[]>([])
  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatus | null>(null)
  const [scanSummary, setScanSummary] = useState<ScanSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [statusLoading, setStatusLoading] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [showProgressCard, setShowProgressCard] = useState(false)
  const { status: workflow, reload: reloadWorkflow } = useWorkflowStatus()

  // 只處理「本頁開啟之後」才結束的任務，避免每次進入頁面都彈出上一個任務的完成卡片（F-8），
  // 也避免輪詢模式下同一個完成狀態被重複處理
  const mountedAt = useRef(Date.now() / 1000)
  const lastHandledFinish = useRef<number | null>(null)

  const loadRoots = useCallback(async () => {
    try {
      setLoading(true)
      setRoots(await api.getRoots())
    } catch (err) {
      console.error('Failed to load roots:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadAnalysisStatus = useCallback(async () => {
    try {
      setStatusLoading(true)
      setAnalysisStatus(await api.getAnalysisStatus())
    } catch (err) {
      console.error('Failed to load analysis status:', err)
    } finally {
      setStatusLoading(false)
    }
  }, [])

  const loadAll = useCallback(() => {
    loadRoots()
    loadAnalysisStatus()
    reloadWorkflow()
  }, [loadRoots, loadAnalysisStatus, reloadWorkflow])

  const { progress, isScanning, cancelScan } = useScanProgress(
    useCallback((completedState: ScanProgressState) => {
      const finishedAt = (completedState as any).finished_at as number | undefined
      if (!finishedAt || finishedAt < mountedAt.current - 1 || finishedAt === lastHandledFinish.current) return
      lastHandledFinish.current = finishedAt
      loadAll()
      if (completedState.scan_summary && Object.keys(completedState.scan_summary).length > 0) {
        setScanSummary(completedState.scan_summary)
      }
      setShowProgressCard(true)
    }, [loadAll])
  )

  useEffect(() => {
    loadRoots()
    loadAnalysisStatus()
  }, [loadRoots, loadAnalysisStatus])

  const run = async (fn: () => Promise<unknown>, failText: string, okText?: string) => {
    try {
      setMessage(null)
      setShowProgressCard(true)
      await fn()
      if (okText) setMessage({ type: 'success', text: okText })
      reloadWorkflow()
    } catch (err: any) {
      setMessage({ type: 'error', text: err?.response?.data?.detail || err?.message || failText })
    }
  }

  const handleAddRoot = async (path: string) => {
    try {
      setMessage(null)
      await api.createRoot({ path })
      setMessage({ type: 'success', text: '已成功新增掃描目錄！' })
      loadRoots()
      reloadWorkflow()
    } catch (err: any) {
      setMessage({ type: 'error', text: err.response?.data?.detail || '新增目錄失敗，請確認路徑是否存在。' })
    }
  }

  const handleDeleteRoot = async (id: number) => {
    try {
      await api.deleteRoot(id)
      loadAll()
    } catch (err) {
      console.error('Failed to delete root:', err)
    }
  }

  const handleTriggerScan = (mode: 'full' | 'new_only', rootId?: number) =>
    run(() => api.triggerJobScan(mode, rootId), '啟動掃描失敗。')

  const handleTriggerAnalyze = (stages: string[], device: string, retryFailed: boolean) =>
    run(() => api.triggerJobAnalyze(stages, device, retryFailed), '啟動特徵分析失敗。')

  const handleTriggerRebuildThumbs = () => run(() => api.triggerRebuildThumbs(), '啟動縮圖補算失敗。')

  const handleTriggerEvaluate = (force: boolean) => run(() => workflowApi.evaluate(force), '啟動規則評估失敗。')

  const handleTriggerPipeline = (mode: 'full' | 'new_only') =>
    run(() => workflowApi.pipeline(mode, 'auto'), '啟動流水線失敗。')

  const cancellable = (progress as any)?.cancellable !== false

  return (
    <div className="p-8 space-y-6 overflow-y-auto max-w-7xl mx-auto w-full pb-36">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-100">資料庫掃描與分析</h2>
          <p className="text-slate-400 text-sm mt-1">依序完成 掃描 → 分析 → 評估，再到審核工作台確認要清理的相片</p>
        </div>
        <button
          onClick={loadAll}
          className="flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-medium border border-slate-700 transition-colors cursor-pointer"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading || statusLoading ? 'animate-spin' : ''}`} />
          <span>重新整理</span>
        </button>
      </div>

      {/* Workflow stepper */}
      {workflow && <WorkflowSteps status={workflow} layout="horizontal" />}

      {/* Real-time progress */}
      {(showProgressCard || isScanning) && (
        <ScanProgressBar
          progress={progress}
          onCancel={cancellable ? cancelScan : async () => setMessage({ type: 'success', text: '規則評估無法中途取消，通常數秒內完成。' })}
          onDismiss={() => setShowProgressCard(false)}
          onNavigateReview={() => navigate('/review')}
        />
      )}

      {message && (
        <div
          className={`p-4 rounded-xl text-sm flex items-center gap-2 border ${
            message.type === 'success'
              ? 'bg-emerald-950/40 text-emerald-300 border-emerald-800/60'
              : 'bg-rose-950/40 text-rose-300 border-rose-800/60'
          }`}
        >
          {message.type === 'success' ? <CheckCircle2 className="w-4 h-4 shrink-0" /> : <AlertCircle className="w-4 h-4 shrink-0" />}
          <span>{message.text}</span>
        </div>
      )}

      <ScanPanel
        roots={roots}
        loading={loading}
        isScanning={isScanning}
        scanSummary={scanSummary}
        onAddRoot={handleAddRoot}
        onDeleteRoot={handleDeleteRoot}
        onTriggerScan={handleTriggerScan}
      />

      <AnalysisPanel
        status={analysisStatus}
        loading={statusLoading}
        isAnalyzing={isScanning}
        onTriggerAnalyze={handleTriggerAnalyze}
        onTriggerRebuildThumbs={handleTriggerRebuildThumbs}
        onRefreshStatus={loadAnalysisStatus}
      />

      <EvaluatePanel
        status={workflow}
        isRunning={isScanning}
        onTriggerEvaluate={handleTriggerEvaluate}
        onTriggerPipeline={handleTriggerPipeline}
      />
    </div>
  )
}
