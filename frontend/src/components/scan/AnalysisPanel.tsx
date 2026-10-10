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
  Minus
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

  const [runners, setRunners] = useState<Record<string, number>>({
    hash: status?.runners?.hash ?? 4,
    basic: status?.runners?.basic ?? 4,
    clip: status?.runners?.clip ?? 4,
    video: status?.runners?.video ?? 4,
  })

  useEffect(() => {
    if (status?.runners) {
      setRunners(status.runners)
    }
  }, [status?.runners])

  const handleRunnerChange = async (stageKey: string, delta: number) => {
    const current = runners[stageKey] ?? 4
    const nextVal = Math.max(1, Math.min(16, current + delta))
    if (nextVal === current) return

    const newRunners = { ...runners, [stageKey]: nextVal }
    setRunners(newRunners)

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
      desc: '僅對檔案大小重複的主檔計算 SHA-256 雜湊，不讀取圖片內容',
      icon: <FileCheck2 className="w-4 h-4 text-sky-400" />,
      stats: status?.stages?.hash,
    },
    {
      id: 'basic',
      title: '相片基本特徵 (Basic)',
      desc: '產生縮圖、真實清晰度方差 (Laplacian)、平均亮度、EXIF、pHash',
      icon: <ImageIcon className="w-4 h-4 text-emerald-400" />,
      stats: status?.stages?.basic,
    },
    {
      id: 'clip',
      title: 'CLIP 語義向量 (CLIP)',
      desc: '真實 512 維嵌入向量與 Prompt Ensemble 零樣本分類（截圖/文件/梗圖/誤拍）',
      icon: <Sparkles className="w-4 h-4 text-purple-400" />,
      stats: status?.stages?.clip,
      hasStale: (status?.stages?.clip?.stale ?? 0) > 0,
    },
    {
      id: 'video',
      title: '影片資訊 (Video)',
      desc: '讀取影片時長、解析度、編碼與擷取第 1 幀縮圖',
      icon: <Video className="w-4 h-4 text-amber-400" />,
      stats: status?.stages?.video,
    },
  ]

  const totalPending = 
    (status?.stages?.hash?.pending ?? 0) +
    (status?.stages?.basic?.pending ?? 0) +
    (status?.stages?.clip?.pending ?? 0) +
    (status?.stages?.video?.pending ?? 0)

  const totalFailed = 
    (status?.stages?.hash?.failed ?? 0) +
    (status?.stages?.basic?.failed ?? 0) +
    (status?.stages?.clip?.failed ?? 0) +
    (status?.stages?.video?.failed ?? 0)

  const isClipStale = (status?.stages?.clip?.stale ?? 0) > 0

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <h3 className="text-lg font-bold text-slate-100 flex items-center gap-2">
            <Cpu className="w-5 h-5 text-purple-400" />
            <span>2. 特徵與 AI 語義分析工作台（Analyze）</span>
          </h3>
          <p className="text-xs text-slate-400 mt-1">
            四個階段互不依賴、可單獨執行、單獨重試。支援 GPU/CPU 運算切換與失敗快速補救。
          </p>
        </div>

        {/* Hardware Status Badge & Device Switcher */}
        <div className="flex items-center gap-2.5 flex-wrap">
          {/* Dynamic GPU Status Badge */}
          {gpuStatus?.is_ready ? (
            <div className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-950/40 border border-emerald-800/60 rounded-xl text-xs text-emerald-400 font-mono">
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
              <span>⚡ {gpuStatus.gpu_name} (CUDA 就緒)</span>
            </div>
          ) : gpuStatus?.has_nvidia_gpu ? (
            <button
              onClick={() => setShowCudaModal(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-gradient-to-r from-purple-900/60 to-indigo-900/60 hover:from-purple-800 hover:to-indigo-800 border border-purple-700/60 rounded-xl text-xs text-purple-200 font-medium transition-all shadow-md shadow-purple-900/30 cursor-pointer animate-pulse"
              title="偵測到 NVIDIA 獨立顯卡，點擊一鍵下載安裝 PyTorch CUDA 12.1"
            >
              <Zap className="w-3.5 h-3.5 text-amber-300" />
              <span>⚡ 偵測到 {gpuStatus.gpu_name}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/30 text-white font-bold">
                一鍵啟用 GPU 加速
              </span>
            </button>
          ) : (
            <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded-xl text-xs text-slate-400 font-mono">
              <Cpu className="w-3.5 h-3.5 text-slate-500" />
              <span>未檢測到相容 GPU (CPU 模式)</span>
            </div>
          )}

          {/* Device Switcher */}
          <div className="flex items-center gap-1.5 bg-slate-950 px-2.5 py-1.5 rounded-xl border border-slate-800 text-xs">
            <span className="text-slate-500">運算裝置：</span>
            <select
              value={device}
              onChange={(e) => handleDeviceChange(e.target.value as any)}
              className="bg-slate-900 text-slate-200 rounded-lg px-2 py-0.5 border border-slate-700 text-xs font-medium focus:outline-none focus:border-purple-500 cursor-pointer"
            >
              <option value="auto">自動 (GPU優先)</option>
              <option value="cuda">只用 GPU (CUDA)</option>
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

      {/* Stale CLIP Alert Banner */}
      {isClipStale && (
        <div className="p-4 bg-amber-950/40 border border-amber-800/60 rounded-xl flex items-center justify-between gap-3 text-xs text-amber-300 animate-fade-in">
          <div className="flex items-center gap-2.5">
            <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0" />
            <div>
              <div className="font-semibold">偵測到 CLIP 模型設定變更（舊向量過期）</div>
              <div className="text-[11px] text-amber-400/80 mt-0.5">
                有 <strong>{status?.stages?.clip?.stale}</strong> 張相片是以舊模型分析的，規則評估時將被排除。
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
        <div className="text-xs text-slate-400 flex items-center gap-2">
          <span>待分析項目：<strong className="text-slate-200 font-mono">{totalPending}</strong></span>
          <span>•</span>
          <span>失敗項目：<strong className={`${totalFailed > 0 ? 'text-rose-400' : 'text-slate-400'} font-mono`}>{totalFailed}</strong></span>
        </div>

        <div className="flex items-center gap-2">
          <button
            disabled={isAnalyzing}
            onClick={onTriggerRebuildThumbs}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-300 rounded-lg text-xs font-medium border border-slate-700 transition-colors cursor-pointer"
            title="僅補齊缺少縮圖檔案的相片，不重跑其他分析"
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
              <span>重試全部失敗項目 ({totalFailed})</span>
            </button>
          )}

          <button
            disabled={isAnalyzing}
            onClick={() => onTriggerAnalyze(['hash', 'basic', 'clip', 'video'], device, false)}
            className="flex items-center gap-1.5 px-4 py-1.5 bg-purple-600 hover:bg-purple-500 disabled:opacity-40 text-white rounded-lg text-xs font-semibold shadow-md shadow-purple-600/20 transition-all cursor-pointer"
          >
            <Zap className="w-3.5 h-3.5" />
            <span>{isAnalyzing ? '分析進行中...' : '執行全部分析 (All)'}</span>
          </button>
        </div>
      </div>

      {/* 4 Independent Stage Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {stagesInfo.map((stage) => {
          const stats = stage.stats
          const pending = stats?.pending ?? 0
          const done = stats?.done ?? 0
          const failed = stats?.failed ?? 0
          const stale = stats?.stale ?? 0
          const notNeeded = stats?.not_needed ?? 0

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
                    <div className="flex items-center gap-1 bg-slate-900 border border-slate-700/80 rounded-lg px-1.5 py-0.5 text-[11px] font-mono">
                      <span className="text-sky-400 font-bold">⚡ {runners[stage.id] ?? 4}</span>
                      <span className="text-slate-500 text-[10px]">T</span>
                      <div className="flex items-center gap-0.5 ml-1 border-l border-slate-700 pl-1">
                        <button
                          type="button"
                          onClick={() => handleRunnerChange(stage.id, -1)}
                          disabled={isAnalyzing || (runners[stage.id] ?? 4) <= 1}
                          className="hover:text-sky-300 text-slate-400 disabled:opacity-30 cursor-pointer"
                          title="減少 1 個線程"
                        >
                          <Minus className="w-2.5 h-2.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleRunnerChange(stage.id, 1)}
                          disabled={isAnalyzing || (runners[stage.id] ?? 4) >= 16}
                          className="hover:text-sky-300 text-slate-400 disabled:opacity-30 cursor-pointer"
                          title="增加 1 個線程"
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

              {/* Stats badges */}
              <div className="pt-2 border-t border-slate-900 flex flex-wrap items-center gap-2 text-[11px] font-mono">
                <span className="px-2 py-0.5 bg-emerald-950/60 text-emerald-400 border border-emerald-900/60 rounded-md">
                  已完成: <strong>{done}</strong>
                </span>
                <span className="px-2 py-0.5 bg-sky-950/60 text-sky-400 border border-sky-900/60 rounded-md">
                  待處理: <strong>{pending}</strong>
                </span>
                {notNeeded > 0 && (
                  <span className="px-2 py-0.5 bg-slate-800/60 text-slate-400 border border-slate-700/60 rounded-md">
                    唯一免算: <strong>{notNeeded}</strong>
                  </span>
                )}
                {stale > 0 && (
                  <span className="px-2 py-0.5 bg-amber-950/60 text-amber-400 border border-amber-900/60 rounded-md font-bold animate-pulse">
                    舊模型過期: <strong>{stale}</strong>
                  </span>
                )}
                {failed > 0 && (
                  <span className="px-2 py-0.5 bg-rose-950/60 text-rose-400 border border-rose-900/60 rounded-md font-bold">
                    失敗: <strong>{failed}</strong>
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* CUDA Install Modal */}
      <CudaInstallModal
        isOpen={showCudaModal}
        onClose={() => setShowCudaModal(false)}
        gpuStatus={gpuStatus}
        onRefreshGpuStatus={() => loadGpuStatus(true)}
      />
    </div>
  )
}
