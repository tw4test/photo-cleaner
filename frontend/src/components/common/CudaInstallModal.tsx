import React, { useState, useEffect, useRef } from 'react'
import { 
  Zap, 
  Cpu, 
  CheckCircle2, 
  X, 
  Download, 
  Loader2, 
  Terminal, 
  StopCircle, 
  ChevronDown, 
  ChevronUp,
  Sparkles,
  ShieldCheck
} from 'lucide-react'
import { api } from '../../services/api'
import { GpuDiagnosticStatus, CudaInstallProgress } from '../../services/types'

interface CudaInstallModalProps {
  isOpen: boolean
  onClose: () => void
  onSuccess?: () => void
  gpuStatus: GpuDiagnosticStatus | null
  onRefreshGpuStatus: () => Promise<void>
}

export const CudaInstallModal: React.FC<CudaInstallModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
  gpuStatus,
  onRefreshGpuStatus,
}) => {
  const [installState, setInstallState] = useState<CudaInstallProgress>(
    gpuStatus?.install_state || {
      status: 'idle',
      percentage: 0,
      message: '閒置中',
      logs: [],
    }
  )
  const [showLogs, setShowLogs] = useState(true)
  const [isStarting, setIsStarting] = useState(false)
  const logsEndRef = useRef<HTMLDivElement>(null)
  const eventSourceRef = useRef<EventSource | null>(null)

  // Auto-scroll terminal logs
  useEffect(() => {
    if (showLogs && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [installState.logs, showLogs])

  // Connect to SSE when modal is open
  useEffect(() => {
    if (!isOpen) return

    const es = new EventSource('/api/system/install-cuda/events')
    eventSourceRef.current = es

    es.onmessage = (e) => {
      try {
        const data: CudaInstallProgress = JSON.parse(e.data)
        setInstallState(data)
        if (data.status === 'success') {
          onRefreshGpuStatus()
          if (onSuccess) onSuccess()
        }
      } catch (err) {
        console.error('Failed to parse install SSE data:', err)
      }
    }

    es.onerror = () => {
      es.close()
    }

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
      }
    }
  }, [isOpen, onRefreshGpuStatus, onSuccess])

  if (!isOpen) return null

  const isInstalling = ['downloading', 'installing', 'verifying'].includes(installState.status)
  const isSuccess = installState.status === 'success' || gpuStatus?.torch_cuda_available
  const isFailed = installState.status === 'failed'

  const handleStartInstall = async () => {
    try {
      setIsStarting(true)
      await api.triggerInstallCuda('cu121')
    } catch (err: any) {
      console.error('Failed to start CUDA install:', err)
    } finally {
      setIsStarting(false)
    }
  }

  const handleCancel = async () => {
    try {
      await api.cancelInstallCuda()
    } catch (err) {
      console.error('Failed to cancel install:', err)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md animate-fade-in">
      <div className="bg-slate-900 border border-slate-700/80 rounded-2xl max-w-2xl w-full p-6 shadow-2xl shadow-black/80 space-y-5 overflow-hidden max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-gradient-to-tr from-purple-600/30 to-sky-600/30 border border-purple-500/40 rounded-xl text-purple-300">
              <Zap className="w-6 h-6 text-purple-400" />
            </div>
            <div>
              <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
                <span>NVIDIA GPU (CUDA) 加速套件安裝精靈</span>
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 border border-purple-500/30 font-mono">
                  CUDA 12.1
                </span>
              </h3>
              <p className="text-xs text-slate-400 mt-0.5">
                自動為 Python 環境下載並安裝支援 GPU 加速的 PyTorch 輪子套件
              </p>
            </div>
          </div>

          <button
            disabled={isInstalling}
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition-colors disabled:opacity-30 cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Hardware Diagnostic Card */}
        <div className="grid grid-cols-2 gap-3 p-3.5 bg-slate-950/60 border border-slate-800 rounded-xl text-xs font-mono">
          <div>
            <span className="text-slate-500 text-[11px]">偵測到顯示卡：</span>
            <div className="font-semibold text-slate-200 flex items-center gap-1.5 mt-0.5">
              <Cpu className="w-3.5 h-3.5 text-sky-400 shrink-0" />
              <span className="truncate">{gpuStatus?.gpu_name || 'NVIDIA GeForce GPU'}</span>
            </div>
          </div>
          <div>
            <span className="text-slate-500 text-[11px]">驅動 / CUDA 支援：</span>
            <div className="font-semibold text-purple-300 mt-0.5">
              Driver {gpuStatus?.driver_version || '--'} (支援 CUDA {gpuStatus?.max_cuda_version || '12.x+'})
            </div>
          </div>
          <div>
            <span className="text-slate-500 text-[11px]">目前 PyTorch 版本：</span>
            <div className={`font-semibold mt-0.5 ${gpuStatus?.torch_cuda_available ? 'text-emerald-400' : 'text-amber-400'}`}>
              {gpuStatus?.torch_version || '2.14.0+cpu'}
            </div>
          </div>
          <div>
            <span className="text-slate-500 text-[11px]">目標安裝套件：</span>
            <div className="font-semibold text-emerald-400 flex items-center gap-1 mt-0.5">
              <Sparkles className="w-3 h-3 text-emerald-400" />
              <span>PyTorch 2.x + cu121 (CUDA 12.1)</span>
            </div>
          </div>
        </div>

        {/* Progress Display */}
        <div className="space-y-2">
          <div className="flex justify-between items-center text-xs font-mono">
            <div className="flex items-center gap-2">
              <span className="text-slate-300 font-semibold">
                {isSuccess ? '安裝狀態' : isInstalling ? '下載安裝進度' : isFailed ? '安裝中斷' : '準備就緒'}
              </span>
              <span className="text-purple-400 font-bold text-sm">
                {isSuccess ? '100%' : `${installState.percentage.toFixed(0)}%`}
              </span>
            </div>

            <div className="flex items-center gap-3 text-slate-400 text-xs">
              {installState.speed && isInstalling && (
                <span className="text-amber-400 font-semibold flex items-center gap-1">
                  <Download className="w-3 h-3" />
                  <span>{installState.speed}</span>
                </span>
              )}
              <span className="truncate max-w-[200px] text-slate-400">
                {installState.current_step || (isSuccess ? '已就緒' : '待觸發')}
              </span>
            </div>
          </div>

          {/* Progress Bar Track */}
          <div className="w-full bg-slate-950 border border-slate-800 h-3 rounded-full overflow-hidden p-0.5 relative">
            <div
              className={`h-full rounded-full transition-all duration-300 ease-out relative ${
                isSuccess
                  ? 'bg-gradient-to-r from-emerald-500 to-teal-400 shadow-md shadow-emerald-500/40'
                  : isFailed
                  ? 'bg-gradient-to-r from-rose-600 to-red-500'
                  : 'bg-gradient-to-r from-purple-600 via-sky-500 to-indigo-500 shadow-md shadow-purple-500/30'
              }`}
              style={{ width: `${Math.max(installState.percentage, isInstalling ? 4 : isSuccess ? 100 : 0)}%` }}
            >
              {isInstalling && (
                <div className="absolute inset-0 bg-white/20 animate-[shimmer_1.5s_infinite] bg-[linear-gradient(90deg,transparent,rgba(255,255,255,0.4),transparent)]" />
              )}
            </div>
          </div>

          <p className="text-xs text-slate-400 font-mono">
            {installState.message || (isSuccess ? 'GPU (CUDA) 加速已完全啟用！' : '點擊下方按鈕即可開始背景下載與安裝。')}
          </p>
        </div>

        {/* Live Terminal Log Stream */}
        <div className="flex-1 flex flex-col min-h-[140px] max-h-[220px] bg-slate-950 border border-slate-800 rounded-xl overflow-hidden font-mono text-[11px]">
          <div 
            onClick={() => setShowLogs(!showLogs)}
            className="flex items-center justify-between px-3 py-1.5 bg-slate-900 border-b border-slate-800 text-slate-400 text-xs cursor-pointer hover:bg-slate-850 select-none"
          >
            <div className="flex items-center gap-1.5 text-slate-300">
              <Terminal className="w-3.5 h-3.5 text-purple-400" />
              <span>即時終端機日誌 ({installState.logs.length} 行)</span>
            </div>
            {showLogs ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </div>

          {showLogs && (
            <div className="flex-1 p-3 overflow-y-auto space-y-1 text-slate-300 font-mono scrollbar-thin">
              {installState.logs.length === 0 ? (
                <div className="text-slate-600 italic">尚無日誌記錄...</div>
              ) : (
                installState.logs.map((log, idx) => (
                  <div key={idx} className="leading-tight break-all">
                    <span className="text-slate-600 mr-1.5 select-none">&gt;</span>
                    <span className={
                      log.includes('Successfully') || log.includes('SUCCESS') || log.includes('🎉')
                        ? 'text-emerald-400 font-bold'
                        : log.includes('error') || log.includes('Failed') || log.includes('❌')
                        ? 'text-rose-400'
                        : log.includes('Downloading')
                        ? 'text-sky-300'
                        : 'text-slate-300'
                    }>
                      {log}
                    </span>
                  </div>
                ))
              )}
              <div ref={logsEndRef} />
            </div>
          )}
        </div>

        {/* Action Buttons */}
        <div className="flex items-center justify-between pt-2 border-t border-slate-800">
          <div className="text-xs text-slate-500">
            {isInstalling && (
              <span className="flex items-center gap-1.5 text-sky-400 animate-pulse">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>正在背景下載與安裝，請稍候...</span>
              </span>
            )}
            {isSuccess && (
              <span className="flex items-center gap-1 text-emerald-400 font-medium">
                <ShieldCheck className="w-4 h-4" />
                <span>CUDA GPU 加速運算已就緒</span>
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            {isInstalling && (
              <button
                onClick={handleCancel}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-rose-950/40 hover:bg-rose-900/60 border border-rose-800/60 text-rose-300 rounded-xl text-xs font-medium transition-colors cursor-pointer"
              >
                <StopCircle className="w-3.5 h-3.5" />
                <span>中斷安裝</span>
              </button>
            )}

            {!isInstalling && !isSuccess && (
              <button
                disabled={isStarting}
                onClick={handleStartInstall}
                className="flex items-center gap-1.5 px-4 py-2 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white rounded-xl text-xs font-semibold shadow-lg shadow-purple-600/30 transition-all cursor-pointer"
              >
                {isStarting ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Download className="w-4 h-4" />
                )}
                <span>開始一鍵升級 CUDA</span>
              </button>
            )}

            {isSuccess && (
              <button
                onClick={onClose}
                className="flex items-center gap-1.5 px-4 py-2 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white rounded-xl text-xs font-semibold shadow-lg shadow-emerald-600/30 transition-all cursor-pointer"
              >
                <CheckCircle2 className="w-4 h-4" />
                <span>完成並關閉</span>
              </button>
            )}

            {!isInstalling && !isSuccess && (
              <button
                onClick={onClose}
                className="px-3 py-2 text-slate-400 hover:text-slate-200 rounded-xl text-xs transition-colors cursor-pointer"
              >
                稍後再說
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
