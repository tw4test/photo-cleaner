import React from 'react'
import { Filter, ArrowRight, Play, Sparkles, CheckCircle2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

interface EvaluatePanelProps {
  isRunning: boolean
  onTriggerEvaluate: () => Promise<void>
  onTriggerFullPipeline: () => Promise<void>
}

export const EvaluatePanel: React.FC<EvaluatePanelProps> = ({
  isRunning,
  onTriggerEvaluate,
  onTriggerFullPipeline,
}) => {
  const navigate = useNavigate()

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <h3 className="text-lg font-bold text-slate-100 flex items-center gap-2">
            <Filter className="w-5 h-5 text-emerald-400" />
            <span>3. 規則評估與一鍵流水線（Evaluate & Pipeline）</span>
          </h3>
          <p className="text-xs text-slate-400 mt-1">
            根據特徵與 CLIP 向量，套用重複、清晰度、曝光、AI 語義規則產出待清理候選相片。
          </p>
        </div>

        {/* Quick Review Navigation */}
        <button
          onClick={() => navigate('/review')}
          className="flex items-center gap-1.5 px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-xl text-xs font-medium border border-slate-700 transition-colors shadow-sm cursor-pointer"
        >
          <span>前往審核工作台</span>
          <ArrowRight className="w-3.5 h-3.5 text-sky-400" />
        </button>
      </div>

      {/* Action Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Evaluate Rules Alone */}
        <div className="p-4 bg-slate-950/40 border border-slate-800/80 rounded-xl flex flex-col justify-between space-y-3">
          <div className="space-y-1">
            <h4 className="text-sm font-semibold text-slate-200 flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              <span>重新評估規則（Re-evaluate Rules）</span>
            </h4>
            <p className="text-xs text-slate-500">
              僅重新套用目前設定門檻計算候選項目，不重跑目錄掃描或特徵分析。適合調整設定值後重新評估。
            </p>
          </div>

          <button
            disabled={isRunning}
            onClick={onTriggerEvaluate}
            className="flex items-center justify-center gap-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-200 rounded-xl text-xs font-semibold border border-slate-700 transition-colors cursor-pointer"
          >
            <Filter className="w-4 h-4 text-emerald-400" />
            <span>立即重新評估規則</span>
          </button>
        </div>

        {/* One-Click Automated Pipeline */}
        <div className="p-4 bg-gradient-to-br from-slate-950/80 to-sky-950/30 border border-sky-800/50 rounded-xl flex flex-col justify-between space-y-3">
          <div className="space-y-1">
            <h4 className="text-sm font-semibold text-sky-300 flex items-center gap-1.5">
              <Sparkles className="w-4 h-4 text-sky-400" />
              <span>一鍵全自動流水線（One-Click Pipeline）</span>
            </h4>
            <p className="text-xs text-slate-400">
              依序全自動執行：【完整掃描 → 全部分析 (GPU優先) → 規則評估】，全流程一鍵搞定。
            </p>
          </div>

          <button
            disabled={isRunning}
            onClick={onTriggerFullPipeline}
            className="flex items-center justify-center gap-2 px-5 py-2 bg-sky-600 hover:bg-sky-500 disabled:opacity-40 text-white rounded-xl text-xs font-bold shadow-lg shadow-sky-600/20 transition-all cursor-pointer"
          >
            <Play className="w-4 h-4" />
            <span>{isRunning ? '流水線任務執行中...' : '開始一鍵全自動執行'}</span>
          </button>
        </div>
      </div>
    </div>
  )
}
