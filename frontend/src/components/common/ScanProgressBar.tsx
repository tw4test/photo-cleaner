import React, { useEffect, useRef, useState } from 'react'
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
  Database,
  Filter,
} from 'lucide-react'
import { ScanProgressState } from '../../services/types'
import { NAV_COLLAPSED_KEY, useLayoutPref } from '../../hooks/useLayoutPref'
import {
  PIPELINE_STEP_KEYS,
  SummaryTile,
  analysisTiles,
  analyzeStepIndex,
  evaluateSummary,
  pipelineStepIndex,
  progressLabel,
  shouldShowCounts,
  summaryBlocks,
} from './progressLogic'

/* ────────────────────────────────────────────────────────────
 * 進度卡片（掃描 / 分析 / 補算縮圖 / 評估 / 一鍵流水線）
 * v0.7.0
 * - 流水線（job_type = 'pipeline'）有自己的六個步驟：掃描 → Hash → Basic → CLIP → Video → 評估，隨階段前進
 * - 完成後不再顯示「正在處理中」與最後一個階段的 N / N；完成摘要依各階段回傳的欄位計算
 *   （hash：done / not_needed；basic：done；clip / video：processed）
 * - 卡片高度用 onHeightChange 回報給頁面（頁面底部才能留出空間，不被卡片蓋住）
 * - 卡片的左邊界跟著主選單的收合狀態（展開 w-64 / 收合 w-16）
 * ──────────────────────────────────────────────────────────── */

interface ScanProgressBarProps {
  progress: ScanProgressState
  onCancel?: () => void
  onDismiss?: () => void
  onNavigateReview?: () => void
  /** 卡片目前的高度（px）；卡片消失 / 卸載時回報 0 */
  onHeightChange?: (height: number) => void
}

type StepDef = { key: string; label: string; icon: React.FC<any> }

// 步驟的順序與鍵只定義在 progressLogic.PIPELINE_STEP_KEYS（單一來源）；這裡只補上顯示用的名稱與圖示
const PIPELINE_STEP_META: Record<(typeof PIPELINE_STEP_KEYS)[number], { label: string; icon: React.FC<any> }> = {
  scan: { label: '掃描目錄', icon: FileSearch },
  hash: { label: '檔案雜湊', icon: FileCheck2 },
  basic: { label: '基本特徵', icon: ImageIcon },
  clip: { label: 'CLIP 向量', icon: Sparkles },
  video: { label: '影片解析', icon: Video },
  evaluate: { label: '規則評估', icon: Filter },
}
const PIPELINE_STEPS: StepDef[] = PIPELINE_STEP_KEYS.map((k) => ({ key: k, ...PIPELINE_STEP_META[k] }))

const TILE_TONE: Record<SummaryTile['tone'], string> = {
  slate: 'text-slate-100',
  emerald: 'text-emerald-400',
  purple: 'text-purple-400',
  amber: 'text-amber-400',
}

export const ScanProgressBar: React.FC<ScanProgressBarProps> = ({
  progress,
  onCancel,
  onDismiss,
  onNavigateReview,
  onHeightChange,
}) => {
  const [isMinimized, setIsMinimized] = useState(false)
  const [navCollapsed] = useLayoutPref(NAV_COLLAPSED_KEY)

  /* 卡片高度 → 頁面（hooks 必須在任何提早 return 之前） */
  const [cardEl, setCardEl] = useState<HTMLDivElement | null>(null)
  const heightCb = useRef(onHeightChange)
  heightCb.current = onHeightChange
  useEffect(() => {
    if (!cardEl) return
    const report = () => heightCb.current?.(Math.ceil(cardEl.getBoundingClientRect().height))
    report()
    if (typeof ResizeObserver === 'undefined') return () => heightCb.current?.(0)
    const ro = new ResizeObserver(report)
    ro.observe(cardEl)
    return () => {
      ro.disconnect()
      heightCb.current?.(0)
    }
  }, [cardEl])

  const {
    is_scanning,
    is_running,
    job_type = 'scan',
    stage,
    stage_label,
    device_used,
    current_runners,
    total_files,
    processed_files,
    percentage,
    current_file,
    speed_fps,
    eta_seconds,
    scan_summary,
    stage_stats,
    error_message,
    duration_seconds,
  } = progress

  const active = !!(is_scanning || is_running)

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

  const isJobCompleted = stage === 'completed'
  const isJobError = stage === 'error'
  const state = (active ? 'active' : isJobCompleted ? 'done' : isJobError ? 'error' : 'idle') as 'active' | 'done' | 'error' | 'idle'
  const pick = (a: string, d: string, e: string, i: string) => (state === 'active' ? a : state === 'done' ? d : state === 'error' ? e : i)

  // Determine stage configurations based on job_type
  let jobTitle = pick('任務進行中', '任務已完成', '任務發生錯誤', '任務')
  let stagesConfig: StepDef[] = []
  let currentStageIndex = 0

  if (job_type === 'scan') {
    jobTitle = pick('相片目錄掃描與資產同步中', '相片目錄掃描已完成', '相片目錄掃描發生錯誤', '相片目錄掃描')
    stagesConfig = [
      { key: 'discovering', label: '目錄遍歷', icon: FileSearch },
      { key: 'processing', label: '資料庫同步', icon: Database },
      { key: 'completed', label: '掃描完成', icon: CheckCircle2 },
    ]
    if (stage === 'discovering') currentStageIndex = 0
    else if (stage === 'processing') currentStageIndex = 1
    else if (stage === 'completed') currentStageIndex = 2
  } else if (job_type === 'analyze') {
    jobTitle = pick('特徵與 AI 語義分析進行中', '特徵與 AI 語義分析已完成', '特徵分析發生錯誤', '特徵與 AI 語義分析')
    stagesConfig = [
      { key: 'hash', label: '檔案雜湊 (Hash)', icon: FileCheck2 },
      { key: 'basic', label: '基本特徵 (Basic)', icon: ImageIcon },
      { key: 'clip', label: 'CLIP 語義向量', icon: Sparkles },
      { key: 'video', label: '影片解析 (Video)', icon: Video },
    ]
    currentStageIndex = analyzeStepIndex(progress)
  } else if (job_type === 'pipeline') {
    jobTitle = pick('一鍵流水線進行中（掃描 → 分析 → 評估）', '一鍵流水線已完成', '一鍵流水線發生錯誤', '一鍵流水線')
    stagesConfig = PIPELINE_STEPS
    currentStageIndex = pipelineStepIndex(progress)
  } else if (job_type === 'rebuild_thumbs') {
    jobTitle = pick('正在補算缺少縮圖', '縮圖補算已完成', '縮圖補算發生錯誤', '縮圖補算')
    stagesConfig = [
      { key: 'scanning', label: '掃描缺圖', icon: FileSearch },
      { key: 'processing', label: '生成縮圖', icon: ImageIcon },
      { key: 'completed', label: '完成', icon: CheckCircle2 },
    ]
    if (stage === 'processing') currentStageIndex = 1
    else if (stage === 'completed') currentStageIndex = 2
  } else if (job_type === 'evaluate') {
    jobTitle = pick('規則引擎評估中', '規則引擎評估完成', '規則引擎評估發生錯誤', '規則引擎評估')
    stagesConfig = [
      { key: 'evaluating_rules', label: '特徵比對與規則運算', icon: Cpu },
      { key: 'candidates', label: '產出候選清單', icon: Sparkles },
      { key: 'completed', label: '完成', icon: CheckCircle2 },
    ]
    if (stage === 'evaluating_rules') currentStageIndex = 0
    else if (stage === 'completed') currentStageIndex = 2
  } else {
    // 不認得的工作類型：用通用步驟，標題依狀態顯示（完成後不再寫「正在處理中」）
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

  const blocks = isJobCompleted ? summaryBlocks(progress) : { scan: false, analyze: false, evaluate: false }
  const tiles = blocks.analyze ? analysisTiles(stage_stats) : []
  const ev = blocks.evaluate ? evaluateSummary(progress) : null
  const stepGrid =
    stagesConfig.length === 3 ? 'grid-cols-3' : stagesConfig.length === 6 ? 'grid-cols-3 sm:grid-cols-6' : 'grid-cols-2 sm:grid-cols-4'
  const showAnalyzeRunners = (job_type === 'analyze' || (job_type === 'pipeline' && active)) && !!current_runners

  return (
    <div
      ref={setCardEl}
      data-testid="progress-card"
      data-job-type={job_type}
      data-nav-collapsed={navCollapsed ? 'true' : 'false'}
      className={`fixed bottom-5 left-4 right-4 ${navCollapsed ? 'md:left-24' : 'md:left-72'} md:right-8 z-50 max-w-5xl mx-auto transition-all duration-300`}
    >
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
                <h3 data-testid="progress-title" className="text-sm sm:text-base font-semibold text-slate-100 truncate">
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
                    {device_used === 'cuda' ? '⚡ GPU (CUDA)' : '\uD83D\uDCBB CPU'}
                  </span>
                )}
                {showAnalyzeRunners && (
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-mono bg-sky-950/80 text-sky-300 border border-sky-800/60 shadow-sm">
                    ⚡ {current_runners} Runners
                  </span>
                )}
              </div>
              <p data-testid="progress-label" className="text-xs text-slate-400 mt-0.5 truncate font-mono">
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
              data-testid="progress-toggle"
              className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition-colors cursor-pointer"
              title={isMinimized ? '展開詳情' : '收合面板'}
            >
              {isMinimized ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>

            {!active && onDismiss && (
              <button
                onClick={onDismiss}
                data-testid="progress-dismiss"
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
              <span className="text-slate-300 font-semibold">{progressLabel(progress)}</span>
              <span className="text-sky-400 text-sm font-bold">{percentage.toFixed(1)}%</span>
              {shouldShowCounts(progress) && (
                <span data-testid="progress-counts" className="text-slate-400 text-xs font-normal">
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
              <div data-testid="progress-steps" className={`grid gap-2 pt-1 ${stepGrid}`}>
                {stagesConfig.map((st, idx) => {
                  const Icon = st.icon
                  const isDone = currentStageIndex > idx || isJobCompleted
                  const isCurrent = currentStageIndex === idx && active
                  const isFailed = isJobError && currentStageIndex === idx

                  return (
                    <div
                      key={st.key}
                      data-step={st.key}
                      data-state={isDone ? 'done' : isCurrent ? 'current' : isFailed ? 'failed' : 'pending'}
                      className={`flex items-center gap-2 p-2 rounded-xl text-xs border transition-all ${
                        isDone
                          ? 'bg-emerald-950/20 border-emerald-800/40 text-emerald-300'
                          : isCurrent
                          ? 'bg-sky-950/40 border-sky-600/50 text-sky-200 shadow-sm shadow-sky-500/10 ring-1 ring-sky-500/30'
                          : isFailed
                          ? 'bg-rose-950/30 border-rose-800/50 text-rose-300'
                          : 'bg-slate-950/40 border-slate-800 text-slate-500'
                      }`}
                    >
                      <div
                        className={`w-4 h-4 rounded-full flex items-center justify-center shrink-0 text-[9px] font-bold ${
                          isDone
                            ? 'bg-emerald-500/20 text-emerald-400'
                            : isCurrent
                            ? 'bg-sky-500/20 text-sky-400 animate-pulse'
                            : isFailed
                            ? 'bg-rose-500/20 text-rose-400'
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
            {blocks.scan && scan_summary && (
              <div data-testid="summary-scan" className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 p-3 bg-emerald-950/15 border border-emerald-800/30 rounded-xl text-xs font-mono">
                <div>
                  <div className="text-slate-400 text-[10px]">新增資產</div>
                  <div className="text-sm font-bold text-sky-400">+{scan_summary.added ?? 0} 筆</div>
                </div>
                <div>
                  <div className="text-slate-400 text-[10px]">修改更新</div>
                  <div className="text-sm font-bold text-amber-400">~{scan_summary.modified ?? 0} 筆</div>
                </div>
                <div>
                  <div className="text-slate-400 text-[10px]">檔案遺失</div>
                  <div className="text-sm font-bold text-rose-400">-{scan_summary.missing ?? 0} 筆</div>
                </div>
                <div>
                  <div className="text-slate-400 text-[10px]">未變更</div>
                  <div className="text-sm font-bold text-slate-200">{scan_summary.unchanged ?? 0} 筆</div>
                </div>
              </div>
            )}

            {/* Completion Summary for Analyze：依各階段回傳的欄位（hash: done / not_needed；basic: done；clip、video: processed） */}
            {tiles.length > 0 && (
              <div data-testid="summary-analyze" className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 p-3 bg-purple-950/15 border border-purple-800/30 rounded-xl text-xs font-mono">
                {tiles.map((t) => (
                  <div key={t.key} data-tile={t.key}>
                    <div className="text-slate-400 text-[10px]">{t.label}{t.error ? '' : '完成'}</div>
                    {t.error ? (
                      <div className="text-sm font-bold text-rose-400" title={t.error}>失敗</div>
                    ) : (
                      <div className={`text-sm font-bold ${TILE_TONE[t.tone]}`}>{t.value}</div>
                    )}
                    {t.sub && <div className="text-[10px] text-slate-500">{t.sub}</div>}
                    {t.failed !== undefined && <div className="text-[10px] text-rose-400">失敗 {t.failed} 筆</div>}
                    {t.error && <div className="truncate text-[10px] text-rose-400" title={t.error}>{t.error}</div>}
                  </div>
                ))}
              </div>
            )}

            {/* Completion Summary for Evaluate（單獨評估與流水線） */}
            {ev && (
              <div data-testid="summary-evaluate" className="flex items-center justify-between gap-3 p-3 bg-emerald-950/20 border border-emerald-800/40 rounded-xl text-xs font-mono">
                <span className="text-slate-300">
                  {ev.skipped ? '評估結果已是最新，略過' : `規則引擎已完成評估（新增 ${ev.added}、移除 ${ev.removed}）`}
                </span>
                <span className="text-amber-400 font-bold text-sm">共 {ev.total} 個清理候選項目</span>
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
