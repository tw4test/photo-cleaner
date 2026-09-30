import React, { useState } from 'react'
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
  Check, 
  FileCheck2, 
  Image as ImageIcon, 
  Video, 
  ChevronDown, 
  ChevronUp, 
  X, 
  Database
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
  const [isMinimized, setIsMinimized] = useState(false)

  const {
    is_scanning,
    is_running,
    job_type = 'scan',
    stage,
    stage_label,
    current_stage,
    device_used,
    total_files,
    processed_files,
    percentage,
    current_file,
    speed_fps,
    eta_seconds,
    new_candidates_count,
    scan_summary,
    stage_stats,
    error_message,
    duration_seconds,
  } = progress

  const active = is_scanning || is_running

  // Format seconds to "X分Y秒" or "X秒"
  const formatTime = (secs: number | null | undefined) => {
    if (secs === null || secs === undefined || isNaN(secs)) return '--'
    if (secs < 60) return `${Math.max(1, Math.round(secs))} 秒`
    const m = Math.floor(secs / 60)
    const s = Math.round(secs % 60)
    return `${m} 分 ${s} 秒`
  }

  // Truncate path for display
  const truncatePath = (path: string, maxLen = 60) => {
    if (!path) return ''
    if (path.length <= maxLen) return path
    const parts = path.split(/[\\/]/)
    const fileName = parts.pop() || ''
    const start = parts.slice(0, 2).join('/')
    return `${start}/.../${fileName}`
  }

  // If completely idle with 0 processed, don't show anything unless error or completed
  if (!active && stage === 'idle') {
    return null
  }

  // Determine stage configurations based on job_type
  let jobTitle = '正在處理中...'
  let stagesConfig: Array<{ key: string; label: string; icon: React.FC<any> }> = []
  let currentStageIndex = 0

  if (job_type === 'scan') {
    jobTitle = active
      ? '相片目錄掃描與資產同步中'
      : stage === 'completed'
      ? '相片目錄掃描已完成'
      : stage === 'error'
      ? '相片目錄掃描發生錯誤'
      : '相片目錄掃描'

    stagesConfig = [
      { key: 'discovering', label: '目錄遍歷', icon: FileSearch },
      { key: 'processing', label: '資料庫同步', icon: Database },
      { key: 'completed', label: '掃描完成', icon: CheckCircle2 },
    ]

    if (stage === 'discovering') currentStageIndex = 0
    else if (stage === 'processing') currentStageIndex = 1
    else if (stage === 'completed') currentStageIndex = 2
  } else if (job_type === 'analyze') {
    jobTitle = active
      ? '特徵與 AI 語義分析進行中'
      : stage === 'completed'
      ? '特徵與 AI 語義分析已完成'
      : stage === 'error'
      ? '特徵分析發生錯誤'
      : '特徵與 AI 語義分析'

    stagesConfig = [
      { key: 'hash', label: '檔案雜湊 (Hash)', icon: FileCheck2 },
      { key: 'basic', label: '基本特徵 (Basic)', icon: ImageIcon },
      { key: 'clip', label: 'CLIP 語義向量', icon: Sparkles },
      { key: 'video', label: '影片解析 (Video)', icon: Video },
    ]

    const effectiveStage = current_stage || stage
    if (effectiveStage === 'hash') currentStageIndex = 0
    else if (effectiveStage === 'basic') currentStageIndex = 1
    else if (effectiveStage === 'clip') currentStageIndex = 2
    else if (effectiveStage === 'video') currentStageIndex = 3
    else if (stage === 'completed') currentStageIndex = 4
  } else if (job_type === 'rebuild_thumbs') {
    jobTitle = active
      ? '正在補算缺少縮圖'
      : stage === 'completed'
      ? '縮圖補算已完成'
      : '縮圖補算'

    stagesConfig = [
      { key: 'scanning', label: '掃描缺圖', icon: FileSearch },
      { key: 'processing', label: '生成縮圖', icon: ImageIcon },
      { key: 'completed', label: '完成', icon: CheckCircle2 },
    ]

    if (stage === 'processing') currentStageIndex = 1
    else if (stage === 'completed') currentStageIndex = 2
  } else if (job_type === 'evaluate') {
    jobTitle = active
      ? '規則引擎評估中'
      : stage === 'completed'
      ? '規則引擎評估完成'
      : '規則引擎評估'

    stagesConfig = [
      { key: 'evaluating_rules', label: '特徵比對與規則運算', icon: Cpu },
      { key: 'candidates', label: '產出候選清單', icon: Sparkles },
      { key: 'completed', label: '完成', icon: CheckCircle2 },
    ]

    if (stage === 'evaluating_rules') currentStageIndex = 0
    else if (stage === 'completed') currentStageIndex = 2
  } else {
    // Fallback
    stagesConfig = [
      { key: 'discovering', label: '目錄遍歷', icon: FileSearch },
      { key: 'processing', label: '縮圖與特徵', icon: Layers },
      { key: 'evaluating_rules', label: 'AI 規則分析', icon: Cpu },
      { key: 'completed', label: '完成', icon: CheckCircle2 },
    ]
    if (stage === 'discovering') currentStageIndex = 0
    else if (stage === 'processing') currentStageIndex = 1
    else if (stage === 'evaluating_rules') currentStageIndex = 2
    else if (stage === 'completed') currentStageIndex = 3
  }

  const isJobCompleted = stage === 'completed'
  const isJobError = stage === 'error'

  return (
    <div className="fixed bottom-5 left-4 right-4 md:left-72 md:right-8 z-50 max-w-5xl mx-auto transition-all duration-300">
      <div className="bg-slate-900/95 backdrop-blur-xl border border-slate-700/80 rounded-2xl p-5 shadow-2xl shadow-black/60 ring-1 ring-sky-500/20 space-y-4">
        {/* Header Bar */}
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <div className={`p-2 rounded-xl shrink-0 ${
              isJobCompleted
                ? 'bg-emerald-500/15 border border-emerald-500/30 text-emerald-400'
                : isJobError
                ? 'bg-rose-500/15 border border-rose-500/30 text-rose-400'
                : 'bg-sky-500/15 border border-sky-500/30 text-sky-400'
            }`}>
              {active ? (
                <Loader2 className="w-5 h-5 animate-spin text-sky-400" />
              ) : isJobCompleted ? (
                <CheckCircle2 className="w-5 h-5 text-emerald-400" />
              ) : (
                <AlertCircle className="w-5 h-5 text-rose-400" />
              )}
            </div>

            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="text-sm sm:text-base font-semibold text-slate-100 truncate">
                  {jobTitle}
                </h3>
                {active && (
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold bg-sky-500/10 text-sky-400 border border-sky-500/20 animate-pulse">
                    執行中
                  </span>
                )}
                {device_used && (
                  <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-mono border ${
                    device_used === 'cuda'
                      ? 'bg-purple-500/10 text-purple-300 border-purple-500/20'
                      : 'bg-slate-800 text-slate-300 border-slate-700'
                  }`}>
                    {device_used === 'cuda' ? '⚡ GPU (CUDA)' : '💻 CPU'}
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400 mt-0.5 truncate font-mono">
                {stage_label || '系統正在處理任務...'}
              </p>
            </div>
          </div>

          {/* Action Buttons */}
          <div className="flex items-center gap-2 shrink-0">
            {active && onCancel && (
              <button
                onClick={onCancel}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-rose-950/50 hover:bg-rose-900/60 border border-rose-800/60 text-rose-300 rounded-xl text-xs font-medium transition-colors cursor-pointer"
                title="中斷目前正在進行的任務"
              >
                <StopCircle className="w-3.5 h-3.5 text-rose-400" />
                <span className="hidden sm:inline">中斷任務</span>
              </button>
            )}

            {isJobCompleted && onNavigateReview && (
              <button
                onClick={onNavigateReview}
                className="flex items-center gap-1.5 px-3.5 py-1.5 bg-gradient-to-r from-sky-600 to-indigo-600 hover:from-sky-500 hover:to-indigo-500 text-white rounded-xl text-xs font-medium shadow-md shadow-sky-600/20 transition-all cursor-pointer"
              >
                <Sparkles className="w-3.5 h-3.5 text-amber-300" />
                <span>審核工作台</span>
                <ArrowRight className="w-3.5 h-3.5 ml-0.5" />
              </button>
            )}

            <button
              onClick={() => setIsMinimized(!isMinimized)}
              className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition-colors cursor-pointer"
              title={isMinimized ? '展開詳情' : '收合面板'}
            >
              {isMinimized ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>

            {!active && onDismiss && (
              <button
                onClick={onDismiss}
                className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition-colors cursor-pointer"
                title="關閉提示"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        {/* Progress Track (Always Visible in compact & expanded mode) */}
        <div className="space-y-1.5">
          <div className="flex justify-between items-center text-xs font-mono">
            <div className="flex items-center gap-2">
              <span className="text-slate-300 font-semibold">進度</span>
              <span className="text-sky-400 text-sm font-bold">{percentage.toFixed(1)}%</span>
              {total_files > 0 && (
                <span className="text-slate-400 text-xs font-normal">
                  ({processed_files} / {total_files})
                </span>
              )}
            </div>

            <div className="flex items-center gap-3 text-slate-400 text-[11px]">
              {active && speed_fps > 0 && (
                <span className="flex items-center gap-1 text-amber-400 font-medium">
                  <Zap className="w-3 h-3" />
                  <span>{speed_fps} 筆/秒</span>
                </span>
              )}
              {active && eta_seconds !== null && (
                <span className="flex items-center gap-1 text-slate-300">
                  <Clock className="w-3 h-3 text-sky-400" />
                  <span>剩餘約 {formatTime(eta_seconds)}</span>
                </span>
              )}
              {!active && duration_seconds > 0 && (
                <span className="text-slate-400">
                  總耗時：{formatTime(duration_seconds)}
                </span>
              )}
            </div>
          </div>

          <div className="w-full bg-slate-950 border border-slate-800 h-2.5 rounded-full overflow-hidden p-0.5 relative">
            <div
              className={`h-full rounded-full transition-all duration-300 ease-out relative ${
                isJobCompleted
                  ? 'bg-gradient-to-r from-emerald-500 to-teal-400 shadow-md shadow-emerald-500/50'
                  : isJobError
                  ? 'bg-gradient-to-r from-rose-600 to-red-500'
                  : 'bg-gradient-to-r from-sky-500 via-indigo-500 to-purple-500 shadow-md shadow-sky-500/30'
              }`}
              style={{ width: `${Math.max(percentage, active ? 2 : 0)}%` }}
            >
              {active && (
                <div className="absolute inset-0 bg-white/20 animate-[shimmer_1.5s_infinite] bg-[linear-gradient(90deg,transparent,rgba(255,255,255,0.4),transparent)]" />
              )}
            </div>
          </div>
        </div>

        {/* Expanded Detailed Sections */}
        {!isMinimized && (
          <>
            {/* Step Indicators */}
            {stagesConfig.length > 0 && (
              <div className={`grid gap-2 pt-1 ${
                stagesConfig.length === 3 ? 'grid-cols-3' : 'grid-cols-2 sm:grid-cols-4'
              }`}>
                {stagesConfig.map((st, idx) => {
                  const Icon = st.icon
                  const isDone = currentStageIndex > idx || isJobCompleted
                  const isCurrent = currentStageIndex === idx && active

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
                        className={`w-4 h-4 rounded-full flex items-center justify-center shrink-0 text-[9px] font-bold ${
                          isDone
                            ? 'bg-emerald-500/20 text-emerald-400'
                            : isCurrent
                            ? 'bg-sky-500/20 text-sky-400 animate-pulse'
                            : 'bg-slate-800 text-slate-600'
                        }`}
                      >
                        {isDone ? <Check className="w-2.5 h-2.5 text-emerald-400" /> : idx + 1}
                      </div>
                      <div className="truncate font-medium flex items-center gap-1 min-w-0">
                        <Icon className="w-3 h-3 shrink-0 opacity-80" />
                        <span className="truncate">{st.label}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}

            {/* Current File Telemetry */}
            {active && current_file && (
              <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-950/70 border border-slate-800/80 rounded-xl text-xs text-slate-400 font-mono overflow-hidden">
                <span className="shrink-0 text-slate-500">處理中：</span>
                <span className="truncate text-slate-300" title={current_file}>
                  {truncatePath(current_file)}
                </span>
              </div>
            )}

            {/* Completion Summary for Scan */}
            {isJobCompleted && scan_summary && (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 p-3 bg-emerald-950/15 border border-emerald-800/30 rounded-xl text-xs font-mono">
                <div>
                  <div className="text-slate-400 text-[10px]">新增資產</div>
                  <div className="text-sm font-bold text-sky-400">+{scan_summary.added} 筆</div>
                </div>
                <div>
                  <div className="text-slate-400 text-[10px]">修改更新</div>
                  <div className="text-sm font-bold text-amber-400">~{scan_summary.modified} 筆</div>
                </div>
                <div>
                  <div className="text-slate-400 text-[10px]">檔案遺失</div>
                  <div className="text-sm font-bold text-rose-400">-{scan_summary.missing} 筆</div>
                </div>
                <div>
                  <div className="text-slate-400 text-[10px]">未變更</div>
                  <div className="text-sm font-bold text-slate-200">{scan_summary.unchanged} 筆</div>
                </div>
              </div>
            )}

            {/* Completion Summary for Analyze */}
            {isJobCompleted && stage_stats && (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 p-3 bg-purple-950/15 border border-purple-800/30 rounded-xl text-xs font-mono">
                {stage_stats.hash && (
                  <div>
                    <div className="text-slate-400 text-[10px]">Hash 雜湊完成</div>
                    <div className="text-sm font-bold text-slate-100">{stage_stats.hash.done ?? 0} 筆</div>
                  </div>
                )}
                {stage_stats.basic && (
                  <div>
                    <div className="text-slate-400 text-[10px]">Basic 特徵完成</div>
                    <div className="text-sm font-bold text-emerald-400">{stage_stats.basic.done ?? 0} 筆</div>
                  </div>
                )}
                {stage_stats.clip && (
                  <div>
                    <div className="text-slate-400 text-[10px]">CLIP 向量完成</div>
                    <div className="text-sm font-bold text-purple-400">
                      {stage_stats.clip.processed ?? stage_stats.clip.done ?? 0} 筆
                    </div>
                  </div>
                )}
                {stage_stats.video && (
                  <div>
                    <div className="text-slate-400 text-[10px]">Video 解析完成</div>
                    <div className="text-sm font-bold text-amber-400">{stage_stats.video.done ?? 0} 筆</div>
                  </div>
                )}
              </div>
            )}

            {/* Completion Summary for Evaluate */}
            {isJobCompleted && new_candidates_count > 0 && !scan_summary && !stage_stats && (
              <div className="flex items-center justify-between p-3 bg-emerald-950/20 border border-emerald-800/40 rounded-xl text-xs font-mono">
                <span className="text-slate-300">規則引擎已完成評估</span>
                <span className="text-amber-400 font-bold text-sm">
                  共找到 {new_candidates_count} 個清理候選項目
                </span>
              </div>
            )}

            {/* Error Card */}
            {isJobError && error_message && (
              <div className="p-3 bg-rose-950/30 border border-rose-800/50 rounded-xl text-xs text-rose-300 font-mono">
                錯誤詳情：{error_message}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
