import React from 'react'
import { 
  Loader2, 
  CheckCircle2, 
  AlertCircle, 
  StopCircle, 
  ArrowRight, 
  Sparkles, 
  Zap, 
  Clock, 
  FileSearch, 
  Layers, 
  Cpu, 
  Check 
} from 'lucide-react'
import { ScanProgressState } from '../../services/types'

interface ScanProgressBarProps {
  progress: ScanProgressState
  onCancel?: () => void
  onDismiss?: () => void
  onNavigateReview?: () => void
}

export const ScanProgressBar: React.FC<ScanProgressBarProps> = ({
  progress,
  onCancel,
  onDismiss,
  onNavigateReview,
}) => {
  const {
    is_scanning,
    stage,
    stage_label,
    total_files,
    processed_files,
    percentage,
    current_file,
    speed_fps,
    eta_seconds,
    new_assets_count,
    new_candidates_count,
    error_message,
    duration_seconds,
  } = progress

  // Format seconds to "X分Y秒" or "X秒"
  const formatTime = (secs: number | null | undefined) => {
    if (secs === null || secs === undefined || isNaN(secs)) return '--'
    if (secs < 60) return `${Math.max(1, Math.round(secs))} 秒`
    const m = Math.floor(secs / 60)
    const s = Math.round(secs % 60)
    return `${m} 分 ${s} 秒`
  }

  // Truncate path for display
  const truncatePath = (path: string, maxLen = 65) => {
    if (!path) return ''
    if (path.length <= maxLen) return path
    const parts = path.split(/[\\/]/)
    const fileName = parts.pop() || ''
    const start = parts.slice(0, 2).join('/')
    return `${start}/.../${fileName}`
  }

  // If completely idle with 0 processed, don't show anything unless error or completed
  if (!is_scanning && stage === 'idle') {
    return null
  }

  const stages = [
    { key: 'discovering', label: '目錄遍歷', icon: FileSearch },
    { key: 'processing', label: '縮圖與特徵', icon: Layers },
    { key: 'evaluating_rules', label: 'AI 規則分析', icon: Cpu },
    { key: 'completed', label: '完成', icon: CheckCircle2 },
  ]

  const getStageIndex = (st: string) => {
    switch (st) {
      case 'discovering': return 0
      case 'processing': return 1
      case 'evaluating_rules': return 2
      case 'completed': return 3
      default: return 0
    }
  }

  const currentStageIdx = getStageIndex(stage)

  return (
    <div className="bg-slate-900/95 backdrop-blur-md border border-slate-700/80 rounded-2xl p-6 space-y-5 shadow-2xl shadow-black/40 ring-1 ring-sky-500/20 transition-all">
      {/* Header Bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-sky-500/10 border border-sky-500/30 rounded-xl text-sky-400">
            {is_scanning ? (
              <Loader2 className="w-5 h-5 animate-spin text-sky-400" />
            ) : stage === 'completed' ? (
              <CheckCircle2 className="w-5 h-5 text-emerald-400" />
            ) : (
              <AlertCircle className="w-5 h-5 text-rose-400" />
            )}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold text-slate-100">
                {stage === 'completed'
                  ? '相片資料庫掃描完成'
                  : stage === 'error'
                  ? '掃描中斷或發生錯誤'
                  : '正在執行相片資料庫掃描與 AI 語義分析'}
              </h3>
              {is_scanning && (
                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-sky-500/10 text-sky-400 border border-sky-500/20 animate-pulse">
                  即時處理中
                </span>
              )}
            </div>
            <p className="text-xs text-slate-400 mt-0.5 font-mono">{stage_label}</p>
          </div>
        </div>

        {/* Action button */}
        <div className="flex items-center gap-2">
          {is_scanning && onCancel && (
            <button
              onClick={onCancel}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-rose-950/40 hover:bg-rose-900/60 border border-rose-800/60 text-rose-300 rounded-lg text-xs font-medium transition-colors"
            >
              <StopCircle className="w-3.5 h-3.5 text-rose-400" />
              <span>中斷掃描</span>
            </button>
          )}
          {stage === 'completed' && onNavigateReview && (
            <button
              onClick={onNavigateReview}
              className="flex items-center gap-1.5 px-4 py-1.5 bg-gradient-to-r from-sky-600 to-indigo-600 hover:from-sky-500 hover:to-indigo-500 text-white rounded-lg text-xs font-medium shadow-md shadow-sky-600/20 transition-all cursor-pointer"
            >
              <Sparkles className="w-3.5 h-3.5 text-amber-300" />
              <span>前往審核工作台</span>
              <ArrowRight className="w-3.5 h-3.5 ml-0.5" />
            </button>
          )}
          {!is_scanning && onDismiss && (
            <button
              onClick={onDismiss}
              className="text-xs text-slate-500 hover:text-slate-300 px-2.5 py-1.5 rounded-lg hover:bg-slate-800 transition-colors"
            >
              關閉提示
            </button>
          )}
        </div>
      </div>

      {/* Step Indicators */}
      <div className="grid grid-cols-4 gap-2 pt-1">
        {stages.map((st, idx) => {
          const Icon = st.icon
          const isDone = currentStageIdx > idx || stage === 'completed'
          const isCurrent = currentStageIdx === idx && is_scanning

          return (
            <div
              key={st.key}
              className={`flex items-center gap-2 p-2 rounded-xl text-xs border transition-all ${
                isDone
                  ? 'bg-emerald-950/20 border-emerald-800/40 text-emerald-300'
                  : isCurrent
                  ? 'bg-sky-950/40 border-sky-600/50 text-sky-200 shadow-sm shadow-sky-500/10 ring-1 ring-sky-500/30'
                  : 'bg-slate-950/40 border-slate-800 text-slate-500'
              }`}
            >
              <div
                className={`w-5 h-5 rounded-full flex items-center justify-center shrink-0 text-[10px] font-bold ${
                  isDone
                    ? 'bg-emerald-500/20 text-emerald-400'
                    : isCurrent
                    ? 'bg-sky-500/20 text-sky-400 animate-pulse'
                    : 'bg-slate-800 text-slate-600'
                }`}
              >
                {isDone ? <Check className="w-3 h-3 text-emerald-400" /> : idx + 1}
              </div>
              <div className="truncate font-medium flex items-center gap-1">
                <Icon className="w-3.5 h-3.5 shrink-0 opacity-80" />
                <span className="truncate">{st.label}</span>
              </div>
            </div>
          )
        })}
      </div>

      {/* Progress Bar with Glowing Pulse */}
      <div className="space-y-2">
        <div className="flex justify-between items-end text-xs font-mono">
          <span className="text-slate-300 font-semibold flex items-center gap-2">
            <span>進度</span>
            <span className="text-sky-400 text-sm font-bold">{percentage.toFixed(1)}%</span>
            {total_files > 0 && (
              <span className="text-slate-500 text-xs font-normal">
                ({processed_files} / {total_files} 檔案)
              </span>
            )}
          </span>
          <div className="flex items-center gap-4 text-slate-400">
            {is_scanning && speed_fps > 0 && (
              <span className="flex items-center gap-1 text-slate-300">
                <Zap className="w-3.5 h-3.5 text-amber-400" />
                <span>{speed_fps} 張/秒</span>
              </span>
            )}
            {is_scanning && eta_seconds !== null && (
              <span className="flex items-center gap-1 text-slate-300">
                <Clock className="w-3.5 h-3.5 text-sky-400" />
                <span>剩餘約 {formatTime(eta_seconds)}</span>
              </span>
            )}
            {!is_scanning && duration_seconds > 0 && (
              <span className="text-slate-400">
                總耗時：{formatTime(duration_seconds)}
              </span>
            )}
          </div>
        </div>

        {/* Progress Track */}
        <div className="w-full bg-slate-950 border border-slate-800 h-3 rounded-full overflow-hidden p-0.5 relative">
          <div
            className={`h-full rounded-full transition-all duration-300 ease-out relative ${
              stage === 'completed'
                ? 'bg-gradient-to-r from-emerald-500 to-teal-400 shadow-lg shadow-emerald-500/50'
                : stage === 'error'
                ? 'bg-gradient-to-r from-rose-600 to-red-500'
                : 'bg-gradient-to-r from-sky-500 via-indigo-500 to-purple-500 shadow-lg shadow-sky-500/30'
            }`}
            style={{ width: `${Math.max(percentage, is_scanning ? 2 : 0)}%` }}
          >
            {is_scanning && (
              <div className="absolute inset-0 bg-white/20 animate-[shimmer_1.5s_infinite] bg-[linear-gradient(90deg,transparent,rgba(255,255,255,0.4),transparent)]" />
            )}
          </div>
        </div>
      </div>

      {/* Current File Telemetry / Completion Stats */}
      {is_scanning && current_file && (
        <div className="flex items-center gap-2 px-3 py-2 bg-slate-950/60 border border-slate-800/80 rounded-xl text-xs text-slate-400 font-mono overflow-hidden">
          <span className="shrink-0 text-slate-500">處理中：</span>
          <span className="truncate text-slate-300" title={current_file}>
            {truncatePath(current_file)}
          </span>
        </div>
      )}

      {/* Completion Summary Card */}
      {stage === 'completed' && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 p-3.5 bg-emerald-950/15 border border-emerald-800/30 rounded-xl text-xs font-mono">
          <div>
            <div className="text-slate-400 text-[11px]">總掃描檔案數</div>
            <div className="text-base font-bold text-slate-100">{total_files} 張</div>
          </div>
          <div>
            <div className="text-slate-400 text-[11px]">新登錄相片庫資產</div>
            <div className="text-base font-bold text-sky-400">+{new_assets_count} 筆</div>
          </div>
          <div>
            <div className="text-slate-400 text-[11px]">候選清理項目</div>
            <div className="text-base font-bold text-amber-400">{new_candidates_count} 筆</div>
          </div>
        </div>
      )}

      {/* Error Card */}
      {stage === 'error' && error_message && (
        <div className="p-3 bg-rose-950/30 border border-rose-800/50 rounded-xl text-xs text-rose-300 font-mono">
          錯誤詳情：{error_message}
        </div>
      )}
    </div>
  )
}
