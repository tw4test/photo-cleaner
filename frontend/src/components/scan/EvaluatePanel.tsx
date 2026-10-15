import React, { useState } from 'react'
import { Filter, ArrowRight, Play, Sparkles, CheckCircle2, AlertTriangle, RotateCcw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { WorkflowStatus, formatTime } from '../../services/workflow'

interface EvaluatePanelProps {
  status: WorkflowStatus | null
  isRunning: boolean
  onTriggerEvaluate: (force: boolean) => Promise<void>
  onTriggerPipeline: (mode: 'full' | 'new_only') => Promise<void>
}

export const EvaluatePanel: React.FC<EvaluatePanelProps> = ({
  status,
  isRunning,
  onTriggerEvaluate,
  onTriggerPipeline,
}) => {
  const navigate = useNavigate()
  const [pipelineMode, setPipelineMode] = useState<'full' | 'new_only'>('new_only')

  const ev = status?.evaluation
  const analysed = status?.analysis.stages.basic.done ?? 0
  const pendingAnalysis = status?.analysis.pending_total ?? 0
  const neverEvaluated = !ev?.last_evaluated_at
  const stale = !!ev?.is_stale && analysed > 0
  const last = ev?.last_result

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-5">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <h3 className="text-lg font-bold text-slate-100 flex items-center gap-2">
            <Filter className="w-5 h-5 text-emerald-400" />
            <span>3. 規則評估（Evaluate）</span>
          </h3>
          <p className="text-xs text-slate-400 mt-1">
            依分析結果與目前設定產生待審核候選。重複 / 近似 / 連拍需要整個相片庫一起比對，評估一次只需數秒。
          </p>
        </div>
        <button
          onClick={() => navigate('/review')}
          className="flex items-center gap-1.5 px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-xl text-xs font-medium border border-slate-700 transition-colors cursor-pointer"
        >
          <span>前往審核工作台</span>
          <ArrowRight className="w-3.5 h-3.5 text-sky-400" />
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Evaluate */}
        <div className="p-4 bg-slate-950/40 border border-slate-800/80 rounded-xl flex flex-col justify-between gap-3">
          <div className="space-y-2">
            {analysed === 0 ? (
              <div className="text-xs text-slate-400">尚未有分析完成的相片，請先執行上方的分析。</div>
            ) : stale ? (
              <div className="flex items-start gap-2 text-xs text-amber-300">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                <span>
                  {neverEvaluated
                    ? '相片已分析，尚未評估。'
                    : ev && ev.analysed_since > 0
                      ? `有 ${ev.analysed_since} 張相片在上次評估後完成分析，候選清單需要更新。`
                      : '設定或相片庫已變更，候選清單需要更新。'}
                </span>
              </div>
            ) : (
              <div className="flex items-start gap-2 text-xs text-emerald-300">
                <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
                <span>候選清單已是最新。</span>
              </div>
            )}
            {pendingAnalysis > 0 && analysed > 0 && (
              <div className="text-[11px] text-slate-500">提示：還有 {pendingAnalysis} 項分析未完成，評估只會使用已完成的結果。</div>
            )}
            <div className="text-[11px] text-slate-500 space-y-0.5">
              <div>上次評估：{formatTime(ev?.last_evaluated_at)}</div>
              {last && !last.skipped && !neverEvaluated && (
                <div>
                  上次結果：共 {last.total ?? 0} 張候選（新增 {last.added ?? 0}、移除 {last.removed ?? 0}）
                </div>
              )}
            </div>
          </div>
          <div className="flex gap-2">
            <button
              disabled={isRunning || analysed === 0 || !stale}
              onClick={() => onTriggerEvaluate(false)}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl text-xs font-semibold transition-colors cursor-pointer"
              title="只在相片或設定有變更時執行"
            >
              <Filter className="w-4 h-4" />
              <span>{stale ? '評估變更' : '已是最新'}</span>
            </button>
            <button
              disabled={isRunning || analysed === 0}
              onClick={() => onTriggerEvaluate(true)}
              className="flex items-center justify-center gap-1.5 px-3 py-2 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-300 rounded-xl text-xs font-medium border border-slate-700 transition-colors cursor-pointer"
              title="不論有無變更，重新計算所有候選（保留你的待刪 / 保留決定）"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>強制重新評估</span>
            </button>
          </div>
        </div>

        {/* Pipeline */}
        <div className="p-4 bg-gradient-to-br from-slate-950/80 to-sky-950/30 border border-sky-800/50 rounded-xl flex flex-col justify-between gap-3">
          <div className="space-y-1.5">
            <h4 className="text-sm font-semibold text-sky-300 flex items-center gap-1.5">
              <Sparkles className="w-4 h-4 text-sky-400" />
              <span>一鍵流水線</span>
            </h4>
            <p className="text-xs text-slate-400">
              掃描所有路徑 → 執行全部分析（GPU 優先）→ 評估規則，作為一個任務依序執行。掃描與分析期間可隨時取消，已完成的部分會保留。
            </p>
            <div className="flex bg-slate-950 p-1 rounded-xl border border-slate-800 text-xs w-fit">
              {(['new_only', 'full'] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setPipelineMode(m)}
                  className={`px-3 py-1 rounded-lg font-medium transition-colors ${pipelineMode === m ? 'bg-sky-600 text-white' : 'text-slate-400 hover:text-slate-200'}`}
                >
                  {m === 'new_only' ? '只加入新檔案' : '完整掃描'}
                </button>
              ))}
            </div>
          </div>
          <button
            disabled={isRunning || (status?.library.roots ?? 0) === 0}
            onClick={() => onTriggerPipeline(pipelineMode)}
            className="flex items-center justify-center gap-2 px-5 py-2 bg-sky-600 hover:bg-sky-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl text-xs font-bold shadow-lg shadow-sky-600/20 transition-all cursor-pointer"
          >
            <Play className="w-4 h-4" />
            <span>{isRunning ? '任務執行中...' : '開始一鍵流水線'}</span>
          </button>
        </div>
      </div>
    </div>
  )
}
