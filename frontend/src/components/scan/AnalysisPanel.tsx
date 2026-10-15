import React, { useState, useEffect, useCallback } from 'react'
import {
  Cpu,
  Sparkles,
  FileCheck2,
  Image as ImageIcon,
  Video,
  RefreshCw,
  AlertTriangle,
  Play,
  RotateCcw,
  Zap,
  ShieldCheck,
  Plus,
  Minus,
} from 'lucide-react'
import { AnalysisStatus, GpuDiagnosticStatus } from '../../services/types'
import { api } from '../../services/api'
import { CudaInstallModal } from '../common/CudaInstallModal'

interface AnalysisPanelProps {
  status: AnalysisStatus | null
  loading: boolean
  isAnalyzing: boolean
  onTriggerAnalyze: (stages: string[], device: string, retryFailed: boolean) => Promise<void>
  onTriggerRebuildThumbs: () => Promise<void>
  onRefreshStatus: () => Promise<void>
}

export const AnalysisPanel: React.FC<AnalysisPanelProps> = ({
  status,
  loading,
  isAnalyzing,
  onTriggerAnalyze,
  onTriggerRebuildThumbs,
  onRefreshStatus,
}) => {
  const [device, setDevice] = useState<'auto' | 'cuda' | 'cpu'>('auto')
  const [gpuStatus, setGpuStatus] = useState<GpuDiagnosticStatus | null>(null)
  const [gpuLoading, setGpuLoading] = useState(false)
  const [showCudaModal, setShowCudaModal] = useState(false)

  const loadGpuStatus = useCallback(async (refresh = false) => {
    try {
      setGpuLoading(true)
      const data = await api.getGpuStatus(refresh)
      setGpuStatus(data)
    } catch (err) {
      console.error('Failed to load GPU status:', err)
    } finally {
      setGpuLoading(false)
    }
  }, [])

  useEffect(() => {
    loadGpuStatus()
  }, [loadGpuStatus])

  // runner 數以後端回傳為準；尚未載入前顯示「—」，不再假設為 4
  const [runners, setRunners] = useState<Record<string, number>>({})
  useEffect(() => {
    if (status?.runners) {
      setRunners(status.runners)
    }
  }, [status?.runners])

  const handleRunnerChange = async (stageKey: string, delta: number) => {
    const current = runners[stageKey]
    if (current === undefined) return
    const nextVal = Math.max(1, Math.min(16, current + delta))
    if (nextVal === current) return
    setRunners({ ...runners, [stageKey]: nextVal })
    try {
      await api.updateSettings({ [`${stageKey}_runners`]: nextVal }, false)
      await onRefreshStatus()
    } catch (err) {
      console.error('Failed to update runner count:', err)
    }
  }

  const handleRefreshAll = async () => {
    await Promise.all([onRefreshStatus(), loadGpuStatus(true)])
  }

  const handleDeviceChange = (newDevice: 'auto' | 'cuda' | 'cpu') => {
    if (newDevice === 'cuda' && gpuStatus?.has_nvidia_gpu && !gpuStatus.is_ready) {
      setShowCudaModal(true)
    }
    setDevice(newDevice)
  }

  const stagesInfo = [
    {
      id: 'hash',
      title: '檔案雜湊 (Hash)',
      desc: '只對大小相同的主檔計算 SHA-256，用來找完全重複；不讀取圖片內容',
      icon: <FileCheck2 className="w-4 h-4 text-sky-400" />,
      stats: status?.stages?.hash,
    },
    {
      id: 'basic',
      title: '相片基本特徵 (Basic)',
      desc: '產生 320px 縮圖，計算清晰度、亮度、pHash 與拍攝時間（每張只讀取一次）',
      icon: <ImageIcon className="w-4 h-4 text-emerald-400" />,
      stats: status?.stages?.basic,
    },
    {
      id: 'clip',
      title: 'CLIP 語義向量 (CLIP)',
      desc: 'AI 向量與零樣本分類（截圖 / 文件 / 梗圖 / 誤拍），GPU 批次處理；有縮圖時不需再讀原檔',
      icon: <Sparkles className="w-4 h-4 text-purple-400" />,
      stats: status?.stages?.clip,
    },
    {
      id: 'video',
      title: '影片資訊 (Video)',
      desc: '讀取影片時長、解析度與編碼',
      icon: <Video className="w-4 h-4 text-amber-400" />,
      stats: status?.stages?.video,
    },
  ]

  const s = status?.stages
  const clipStale = s?.clip?.stale ?? 0
  // 各階段分開列出（同一張相片會同時出現在多個階段，不能直接相加）
  const pendingParts = [
    { label: '雜湊', n: s?.hash?.pending ?? 0 },
    { label: '基本', n: s?.basic?.pending ?? 0 },
    { label: 'CLIP', n: (s?.clip?.pending ?? 0) + clipStale },
    { label: '影片', n: s?.video?.pending ?? 0 },
  ]
  const anyPending = pendingParts.some((p) => p.n > 0)
  const totalFailed =
    (s?.hash?.failed ?? 0) + (s?.basic?.failed ?? 0) + (s?.clip?.failed ?? 0) + (s?.video?.failed ?? 0)

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <h3 className="text-lg font-bold text-slate-100 flex items-center gap-2">
            <Cpu className="w-5 h-5 text-purple-400" />
            <span>2. 特徵與 AI 分析（Analyze）</span>
          </h3>
          <p className="text-xs text-slate-400 mt-1">
            四個階段可一起或單獨執行，只處理尚未完成的項目；中斷後重新執行會從未完成處繼續。
          </p>
        </div>
        <div className="flex items-center gap-2.5 flex-wrap">
          {gpuStatus?.is_ready ? (
            <div className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-950/40 border border-emerald-800/60 rounded-xl text-xs text-emerald-400 font-mono">
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
              <span>⚡ {gpuStatus.gpu_name}（CUDA 就緒）</span>
            </div>
          ) : gpuStatus?.has_nvidia_gpu ? (
            <button
              onClick={() => setShowCudaModal(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-gradient-to-r from-purple-900/60 to-indigo-900/60 hover:from-purple-800 hover:to-indigo-800 border border-purple-700/60 rounded-xl text-xs text-purple-200 font-medium transition-all cursor-pointer"
              title="偵測到 NVIDIA 顯示卡：點擊安裝 CUDA 版 PyTorch（依顯示卡自動選擇版本）"
            >
              <Zap className="w-3.5 h-3.5 text-amber-300" />
              <span>⚡ 偵測到 {gpuStatus.gpu_name}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/30 text-white font-bold">啟用 GPU 加速</span>
            </button>
          ) : (
            <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded-xl text-xs text-slate-400 font-mono">
              <Cpu className="w-3.5 h-3.5 text-slate-500" />
              <span>未偵測到相容 GPU（CPU 模式）</span>
            </div>
          )}
          <div className="flex items-center gap-1.5 bg-slate-950 px-2.5 py-1.5 rounded-xl border border-slate-800 text-xs">
            <span className="text-slate-500">運算裝置：</span>
            <select
              value={device}
              onChange={(e) => handleDeviceChange(e.target.value as any)}
              className="bg-slate-900 text-slate-200 rounded-lg px-2 py-0.5 border border-slate-700 text-xs font-medium focus:outline-none focus:border-purple-500 cursor-pointer"
            >
              <option value="auto">自動（GPU 優先）</option>
              <option value="cuda">只用 GPU（CUDA）</option>
              <option value="cpu">只用 CPU</option>
            </select>
          </div>
          <button
            onClick={handleRefreshAll}
            className="p-2 text-slate-400 hover:text-slate-200 bg-slate-800 hover:bg-slate-700 rounded-xl border border-slate-700 transition-colors cursor-pointer"
            title="重新整理分析統計與硬體狀態"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading || gpuLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {clipStale > 0 && (
        <div className="p-4 bg-amber-950/40 border border-amber-800/60 rounded-xl flex items-center justify-between gap-3 text-xs text-amber-300">
          <div className="flex items-center gap-2.5">
            <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0" />
            <div>
              <div className="font-semibold">CLIP 模型設定已變更（舊向量過期）</div>
              <div className="text-[11px] text-amber-400/80 mt-0.5">
                有 <strong>{clipStale}</strong> 張相片是以舊模型分析的，評估時會被排除。
              </div>
            </div>
          </div>
          <button
            disabled={isAnalyzing}
            onClick={() => onTriggerAnalyze(['clip'], device, false)}
            className="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-slate-950 font-bold rounded-lg text-xs transition-colors shrink-0 cursor-pointer"
          >
            只重算 CLIP
          </button>
        </div>
      )}

      {/* Global Action Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-slate-950/60 p-3 rounded-xl border border-slate-800">
        <div className="text-xs text-slate-400 flex flex-wrap items-center gap-x-3 gap-y-1">
          <span>待處理：</span>
          {anyPending ? (
            pendingParts.map((p) => (
              <span key={p.label} className={p.n > 0 ? 'text-slate-200' : 'text-slate-600'}>
                {p.label} <strong className="font-mono">{p.n}</strong>
              </span>
            ))
          ) : (
            <span className="text-emerald-400">全部完成</span>
          )}
          <span className="text-slate-700">|</span>
          <span>
            失敗：<strong className={`${totalFailed > 0 ? 'text-rose-400' : 'text-slate-400'} font-mono`}>{totalFailed}</strong>
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            disabled={isAnalyzing}
            onClick={onTriggerRebuildThumbs}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-300 rounded-lg text-xs font-medium border border-slate-700 transition-colors cursor-pointer"
            title="只補回被刪掉的縮圖檔，不重算其他分析"
          >
            <RotateCcw className="w-3.5 h-3.5 text-emerald-400" />
            <span>補算缺少的縮圖</span>
          </button>
          {totalFailed > 0 && (
            <button
              disabled={isAnalyzing}
              onClick={() => onTriggerAnalyze(['hash', 'basic', 'clip', 'video'], device, true)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-rose-950/60 hover:bg-rose-900/60 disabled:opacity-40 text-rose-300 rounded-lg text-xs font-medium border border-rose-800/80 transition-colors cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>重試失敗項目（{totalFailed}）</span>
            </button>
          )}
          <button
            disabled={isAnalyzing}
            onClick={() => onTriggerAnalyze(['hash', 'basic', 'clip', 'video'], device, false)}
            className="flex items-center gap-1.5 px-4 py-1.5 bg-purple-600 hover:bg-purple-500 disabled:opacity-40 text-white rounded-lg text-xs font-semibold shadow-md shadow-purple-600/20 transition-all cursor-pointer"
          >
            <Zap className="w-3.5 h-3.5" />
            <span>{isAnalyzing ? '任務進行中...' : '執行全部分析'}</span>
          </button>
        </div>
      </div>

      {/* Stage cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {stagesInfo.map((stage) => {
          const st: any = stage.stats
          const total = st?.total ?? 0
          const done = st?.done ?? 0
          const pending = st?.pending ?? 0
          const failed = st?.failed ?? 0
          const stale = st?.stale ?? 0
          const notNeeded = st?.not_needed ?? 0
          const finished = done + notNeeded
          const pct = total > 0 ? Math.round((finished / total) * 100) : 0
          const r = runners[stage.id]
          return (
            <div
              key={stage.id}
              className="bg-slate-950/40 border border-slate-800/80 rounded-xl p-4 flex flex-col justify-between space-y-3 hover:border-slate-700 transition-colors"
            >
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-semibold text-slate-200 text-sm">
                    {stage.icon}
                    <span>{stage.title}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div
                      className="flex items-center gap-1 bg-slate-900 border border-slate-700/80 rounded-lg px-1.5 py-0.5 text-[11px] font-mono"
                      title={stage.id === 'clip' ? '讀圖執行緒數（GPU 推理固定一條流水線）' : '並行執行緒數'}
                    >
                      <span className="text-sky-400 font-bold">{r ?? '—'}</span>
                      <span className="text-slate-500 text-[10px]">執行緒</span>
                      <div className="flex items-center gap-0.5 ml-1 border-l border-slate-700 pl-1">
                        <button
                          type="button"
                          onClick={() => handleRunnerChange(stage.id, -1)}
                          disabled={isAnalyzing || r === undefined || r <= 1}
                          className="hover:text-sky-300 text-slate-400 disabled:opacity-30 cursor-pointer"
                          title="減少 1 個"
                        >
                          <Minus className="w-2.5 h-2.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleRunnerChange(stage.id, 1)}
                          disabled={isAnalyzing || r === undefined || r >= 16}
                          className="hover:text-sky-300 text-slate-400 disabled:opacity-30 cursor-pointer"
                          title="增加 1 個"
                        >
                          <Plus className="w-2.5 h-2.5" />
                        </button>
                      </div>
                    </div>
                    <button
                      disabled={isAnalyzing}
                      onClick={() => onTriggerAnalyze([stage.id], device, false)}
                      className="flex items-center gap-1 px-2.5 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-200 rounded-lg text-[11px] font-medium border border-slate-700 transition-colors cursor-pointer"
                    >
                      <Play className="w-3 h-3 text-purple-400" />
                      <span>單獨執行</span>
                    </button>
                  </div>
                </div>
                <p className="text-[11px] text-slate-500 leading-relaxed">{stage.desc}</p>
              </div>
              <div className="space-y-2">
                <div className="h-1.5 w-full rounded-full bg-slate-800 overflow-hidden">
                  <div className="h-full bg-emerald-500" style={{ width: `${pct}%` }} />
                </div>
                <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono">
                  <span className="text-slate-400">
                    {finished.toLocaleString()} / {total.toLocaleString()}
                  </span>
                  {notNeeded > 0 && (
                    <span className="px-2 py-0.5 bg-slate-800/60 text-slate-400 border border-slate-700/60 rounded-md">
                      其中 {notNeeded} 個大小唯一免算
                    </span>
                  )}
                  {pending > 0 && (
                    <span className="px-2 py-0.5 bg-sky-950/60 text-sky-400 border border-sky-900/60 rounded-md">待處理 {pending}</span>
                  )}
                  {stale > 0 && (
                    <span className="px-2 py-0.5 bg-amber-950/60 text-amber-400 border border-amber-900/60 rounded-md">過期 {stale}</span>
                  )}
                  {failed > 0 && (
                    <span className="px-2 py-0.5 bg-rose-950/60 text-rose-400 border border-rose-900/60 rounded-md">失敗 {failed}</span>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      <CudaInstallModal
        isOpen={showCudaModal}
        onClose={() => setShowCudaModal(false)}
        gpuStatus={gpuStatus}
        onRefreshGpuStatus={() => loadGpuStatus(true)}
      />
    </div>
  )
}
