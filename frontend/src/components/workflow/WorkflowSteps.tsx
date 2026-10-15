import React from 'react'
import { Link } from 'react-router-dom'
import { FolderSync, Cpu, Filter, Sparkles, Trash2, CheckCircle2, AlertTriangle, Circle, Loader2 } from 'lucide-react'
import { WorkflowStatus, formatBytes, formatTime } from '../../services/workflow'

type StepState = 'done' | 'todo' | 'warn' | 'active'

interface Step {
  key: 'scan' | 'analyze' | 'evaluate' | 'review' | 'process'
  title: string
  to: string
  icon: any
  state: StepState
  headline: string
  details: string[]
}

function activeStepOf(s: WorkflowStatus): string | null {
  const j = s.job
  if (!j?.is_running) return null
  if (j.job_type === 'pipeline') return j.pipeline_step || 'scan'
  if (j.job_type === 'scan') return 'scan'
  if (j.job_type === 'analyze' || j.job_type === 'rebuild_thumbs') return 'analyze'
  if (j.job_type === 'evaluate') return 'evaluate'
  return null
}

export function buildSteps(s: WorkflowStatus): Step[] {
  const lib = s.library
  const st = s.analysis.stages
  const active = activeStepOf(s)
  const analysedPhotos = st.basic.done
  const clipStale = st.clip.stale ?? 0

  const scan: Step = {
    key: 'scan', title: '掃描', to: '/library', icon: FolderSync,
    state: lib.roots === 0 || lib.roots_never_scanned > 0 ? 'todo' : lib.roots_offline > 0 ? 'warn' : 'done',
    headline: lib.roots === 0 ? '尚未加入路徑' : `${lib.assets.total.toLocaleString()} 個資產`,
    details: [
      `${lib.roots} 個路徑${lib.roots_offline ? `（${lib.roots_offline} 個離線）` : ''}${lib.roots_never_scanned ? `，${lib.roots_never_scanned} 個未掃描` : ''}`,
      `上次掃描：${formatTime(lib.last_scan_at)}`,
      ...(lib.missing ? [`${lib.missing} 個檔案已從磁碟消失`] : []),
    ],
  }

  const analyze: Step = {
    key: 'analyze', title: '分析', to: '/library', icon: Cpu,
    state: lib.assets.total === 0 ? 'todo' : s.analysis.pending_total > 0 ? 'todo' : s.analysis.failures > 0 ? 'warn' : 'done',
    headline: lib.assets.total === 0 ? '—' : s.analysis.pending_total > 0
      ? `尚有 ${s.analysis.pending_total.toLocaleString()} 項待處理`
      : '全部完成',
    details: [
      `基本特徵 ${st.basic.done}/${st.basic.total}　CLIP ${st.clip.done}/${st.clip.total}${clipStale ? `（過期 ${clipStale}）` : ''}`,
      `影片 ${st.video.done}/${st.video.total}　雜湊 ${st.hash.done + (st.hash.not_needed ?? 0)}/${st.hash.total}`,
      ...(s.analysis.failures ? [`${s.analysis.failures} 個失敗項目`] : []),
    ],
  }

  const ev = s.evaluation
  const evaluate: Step = {
    key: 'evaluate', title: '評估', to: '/library', icon: Filter,
    state: analysedPhotos === 0 ? 'todo' : ev.is_stale ? 'todo' : 'done',
    headline: analysedPhotos === 0 ? '等待分析' : !ev.last_evaluated_at ? '尚未評估' : ev.is_stale ? '需要重新評估' : '已是最新',
    details: [
      `上次評估：${formatTime(ev.last_evaluated_at)}`,
      ...(ev.is_stale && ev.analysed_since > 0 ? [`${ev.analysed_since} 張相片在上次評估後完成分析`] : []),
      ...(ev.last_result && !ev.last_result.skipped && ev.last_evaluated_at
        ? [`上次結果：新增 ${ev.last_result.added ?? 0}、移除 ${ev.last_result.removed ?? 0}`] : []),
    ],
  }

  const r = s.review
  const review: Step = {
    key: 'review', title: '審核', to: '/review', icon: Sparkles,
    state: r.candidate.count > 0 ? 'todo' : 'done',
    headline: r.candidate.count > 0 ? `${r.candidate.count.toLocaleString()} 張待審核` : '沒有待審核項目',
    details: [`待刪 ${r.queued.count}　已保留 ${r.kept.count}`],
  }

  const process: Step = {
    key: 'process', title: '刪除', to: '/process', icon: Trash2,
    state: r.queued.count > 0 ? 'todo' : 'done',
    headline: r.queued.count > 0 ? `${r.queued.count} 張待刪（${formatBytes(r.queued.size_bytes)}）` : '待刪清單是空的',
    details: [`已刪除 ${s.deleted.count} 張・${formatBytes(s.deleted.size_bytes)}`],
  }

  const steps = [scan, analyze, evaluate, review, process]
  if (active) {
    for (const step of steps) if (step.key === active) step.state = 'active'
  }
  return steps
}

const STATE_STYLE: Record<StepState, { ring: string; text: string; icon: any; chip: string; chipText: string }> = {
  done: { ring: 'border-emerald-700/60 bg-emerald-950/30', text: 'text-emerald-400', icon: CheckCircle2, chip: 'bg-emerald-950/60 text-emerald-300 border-emerald-800/60', chipText: '完成' },
  todo: { ring: 'border-sky-700/60 bg-sky-950/30', text: 'text-sky-400', icon: Circle, chip: 'bg-sky-950/60 text-sky-300 border-sky-800/60', chipText: '待處理' },
  warn: { ring: 'border-amber-700/60 bg-amber-950/30', text: 'text-amber-400', icon: AlertTriangle, chip: 'bg-amber-950/60 text-amber-300 border-amber-800/60', chipText: '需注意' },
  active: { ring: 'border-purple-600/70 bg-purple-950/40', text: 'text-purple-300', icon: Loader2, chip: 'bg-purple-900/60 text-purple-200 border-purple-700/60', chipText: '進行中' },
}

interface WorkflowStepsProps {
  status: WorkflowStatus
  layout?: 'vertical' | 'horizontal'
}

export const WorkflowSteps: React.FC<WorkflowStepsProps> = ({ status, layout = 'vertical' }) => {
  const steps = buildSteps(status)

  if (layout === 'horizontal') {
    return (
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {steps.map((step, i) => {
          const sty = STATE_STYLE[step.state]
          const Icon = step.icon
          const StateIcon = sty.icon
          return (
            <Link key={step.key} to={step.to} className={`rounded-xl border p-3 space-y-1.5 hover:brightness-125 transition ${sty.ring}`}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
                  <span className="text-slate-500 font-mono">{i + 1}</span>
                  <Icon className={`w-3.5 h-3.5 ${sty.text}`} />
                  <span>{step.title}</span>
                </div>
                <StateIcon className={`w-3.5 h-3.5 ${sty.text} ${step.state === 'active' ? 'animate-spin' : ''}`} />
              </div>
              <div className={`text-xs font-medium ${sty.text}`}>{step.headline}</div>
            </Link>
          )
        })}
      </div>
    )
  }

  return (
    <ol className="space-y-3">
      {steps.map((step, i) => {
        const sty = STATE_STYLE[step.state]
        const Icon = step.icon
        const StateIcon = sty.icon
        return (
          <li key={step.key}>
            <Link to={step.to} className={`flex gap-3 rounded-xl border p-3 hover:brightness-125 transition ${sty.ring}`}>
              <div className="flex flex-col items-center pt-0.5">
                <StateIcon className={`w-4 h-4 ${sty.text} ${step.state === 'active' ? 'animate-spin' : ''}`} />
              </div>
              <div className="flex-1 min-w-0 space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 text-sm font-semibold text-slate-100">
                    <span className="text-slate-500 font-mono text-xs">{i + 1}</span>
                    <Icon className={`w-4 h-4 ${sty.text}`} />
                    <span>{step.title}</span>
                  </div>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded border ${sty.chip}`}>{sty.chipText}</span>
                </div>
                <div className={`text-xs font-medium ${sty.text}`}>{step.headline}</div>
                {step.details.map((d, k) => (
                  <div key={k} className="text-[11px] text-slate-500 truncate">{d}</div>
                ))}
              </div>
            </Link>
          </li>
        )
      })}
    </ol>
  )
}
