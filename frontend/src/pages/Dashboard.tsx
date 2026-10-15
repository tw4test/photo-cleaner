import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Images, Sparkles, ListChecks, Trash2, RefreshCw, ArrowRight, AlertTriangle, Filter, Loader2,
} from 'lucide-react'
import { useWorkflowStatus } from '../hooks/useWorkflowStatus'
import { WorkflowSteps } from '../components/workflow/WorkflowSteps'
import { workflowApi, formatBytes } from '../services/workflow'

// 主要分類顏色（與 Review 頁的徽章色系一致）
const CATEGORY_COLOR: Record<string, string> = {
  exact_dup: 'bg-amber-500',
  near_dup: 'bg-indigo-500',
  burst: 'bg-cyan-500',
  screenshot: 'bg-sky-500',
  document: 'bg-emerald-500',
  meme: 'bg-purple-500',
  mistouch: 'bg-rose-500',
  blur: 'bg-yellow-500',
  exposure: 'bg-orange-500',
  video: 'bg-slate-400',
}

const StatCard: React.FC<{
  title: string
  icon: any
  iconClass: string
  value: string
  sub: string
  to?: string
}> = ({ title, icon: Icon, iconClass, value, sub, to }) => {
  const body = (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 h-full hover:border-slate-700 transition-colors">
      <div className="flex items-center justify-between">
        <span className="text-slate-400 text-sm font-medium">{title}</span>
        <Icon className={`w-5 h-5 ${iconClass}`} />
      </div>
      <div className="mt-3 text-3xl font-bold text-slate-100 font-mono">{value}</div>
      <div className="text-xs text-slate-500 mt-1">{sub}</div>
    </div>
  )
  return to ? <Link to={to}>{body}</Link> : body
}

export const Dashboard: React.FC = () => {
  const { status, loading, error, reload } = useWorkflowStatus()
  const [busy, setBusy] = useState(false)
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  const handleEvaluate = async () => {
    try {
      setBusy(true)
      setActionMsg(null)
      await workflowApi.evaluate(false)
      await reload()
    } catch (e: any) {
      setActionMsg(e?.message || '啟動評估失敗')
    } finally {
      setBusy(false)
    }
  }

  if (!status) {
    return (
      <div className="p-8 max-w-7xl mx-auto w-full text-slate-400 text-sm flex items-center gap-2">
        {error ? (
          <><AlertTriangle className="w-4 h-4 text-rose-400" /> 無法連線到後端：{error}</>
        ) : (
          <><Loader2 className="w-4 h-4 animate-spin" /> 載入中...</>
        )}
      </div>
    )
  }

  const a = status.library.assets
  const r = status.review
  const ev = status.evaluation
  const job = status.job
  const total = status.breakdown_total
  const nonEmpty = status.breakdown.filter((b) => b.count > 0)
  const needsEvaluate = ev.is_stale && status.analysis.stages.basic.done > 0 && !job.is_running

  return (
    <div className="p-8 space-y-6 overflow-y-auto max-w-7xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-100">首頁總覽</h2>
          <p className="text-slate-400 text-sm mt-1">相片庫狀態、待清理項目與處理進度</p>
        </div>
        <button
          onClick={reload}
          className="flex items-center gap-2 px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-medium border border-slate-700 transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          <span>重新整理</span>
        </button>
      </div>

      {/* Running job banner */}
      {job.is_running && (
        <Link
          to="/library"
          className="flex items-center gap-3 p-4 rounded-xl border border-purple-800/60 bg-purple-950/30 text-sm text-purple-200 hover:bg-purple-950/50"
        >
          <Loader2 className="w-4 h-4 animate-spin shrink-0" />
          <span className="flex-1 truncate">{job.stage_label}</span>
          <span className="font-mono text-xs">{Math.round(job.percentage)}%</span>
          <ArrowRight className="w-4 h-4" />
        </Link>
      )}

      {/* Stale evaluation banner */}
      {needsEvaluate && (
        <div className="flex flex-wrap items-center gap-3 p-4 rounded-xl border border-amber-800/60 bg-amber-950/30 text-sm text-amber-200">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span className="flex-1">
            {ev.last_evaluated_at
              ? `候選清單不是最新的${ev.analysed_since > 0 ? `：有 ${ev.analysed_since} 張相片在上次評估後完成分析` : '（設定或相片庫已變更）'}。`
              : '相片已分析完成，但尚未執行規則評估。'}
          </span>
          <button
            disabled={busy}
            onClick={handleEvaluate}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-slate-950 font-semibold rounded-lg text-xs"
          >
            <Filter className="w-3.5 h-3.5" />
            立即評估
          </button>
        </div>
      )}
      {actionMsg && <div className="p-3 rounded-xl border border-rose-800/60 bg-rose-950/30 text-rose-300 text-xs">{actionMsg}</div>}

      {/* KPI cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="相片庫"
          icon={Images}
          iconClass="text-sky-400"
          value={a.total.toLocaleString()}
          sub={`相片 ${a.photo_count}｜RAW ${a.raw_count}｜影片 ${a.video_count}・${formatBytes(a.total_size_bytes)}`}
          to="/library"
        />
        <StatCard
          title="待審核"
          icon={Sparkles}
          iconClass="text-amber-400"
          value={r.candidate.count.toLocaleString()}
          sub={`可釋放 ${formatBytes(r.candidate.size_bytes)}`}
          to="/review"
        />
        <StatCard
          title="待刪清單"
          icon={ListChecks}
          iconClass="text-rose-400"
          value={r.queued.count.toLocaleString()}
          sub={`${formatBytes(r.queued.size_bytes)}・已保留 ${r.kept.count} 張`}
          to="/process"
        />
        <StatCard
          title="已清理"
          icon={Trash2}
          iconClass="text-purple-400"
          value={formatBytes(status.deleted.size_bytes)}
          sub={`${status.deleted.count} 張・${status.deleted.batches} 個批次`}
          to="/history"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Category breakdown */}
        <div className="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-5">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-slate-100">待審核分類</h3>
              <p className="text-xs text-slate-500 mt-0.5">每張相片只歸入一個主要類別，各類別加總 = {total.toLocaleString()} 張</p>
            </div>
            <Link to="/review" className="text-xs text-sky-400 hover:underline flex items-center gap-1">
              前往審核 <ArrowRight className="w-3 h-3" />
            </Link>
          </div>

          {total === 0 ? (
            <div className="py-10 text-center text-sm text-slate-500 border border-dashed border-slate-800 rounded-xl">
              {status.analysis.stages.basic.done === 0 ? '尚未分析任何相片。請先到「資料庫掃描」執行掃描與分析。' : '目前沒有待審核的相片。'}
            </div>
          ) : (
            <>
              <div className="flex h-3 w-full overflow-hidden rounded-full bg-slate-800">
                {nonEmpty.map((b) => (
                  <div
                    key={b.key}
                    className={CATEGORY_COLOR[b.key] || 'bg-slate-500'}
                    style={{ width: `${(b.count / total) * 100}%` }}
                    title={`${b.label} ${b.count}`}
                  />
                ))}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
                {status.breakdown.map((b) => (
                  <div
                    key={b.key}
                    className={`flex items-center gap-3 py-1.5 text-sm ${b.count === 0 ? 'opacity-40' : ''}`}
                  >
                    <span className={`w-2.5 h-2.5 rounded-sm ${CATEGORY_COLOR[b.key] || 'bg-slate-500'}`} />
                    <span className="flex-1 text-slate-300">{b.label}</span>
                    <span className="font-mono text-slate-100 w-14 text-right">{b.count.toLocaleString()}</span>
                    <span className="font-mono text-xs text-slate-500 w-20 text-right">{formatBytes(b.size_bytes)}</span>
                  </div>
                ))}
              </div>
              <p className="text-[11px] text-slate-500">
                同一張相片命中多個規則時，依「完全重複 → 近似重複 → 連拍 → 截圖 → 文件 → 梗圖 → 誤拍 → 模糊 → 曝光」順序歸類；
                審核頁各分類標籤則會列出所有命中該規則的相片。
              </p>
            </>
          )}
        </div>

        {/* Workflow status */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
          <h3 className="font-semibold text-slate-100">處理流程</h3>
          <WorkflowSteps status={status} layout="vertical" />
        </div>
      </div>
    </div>
  )
}
