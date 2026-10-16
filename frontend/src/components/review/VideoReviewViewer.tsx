import React, { useRef } from 'react'
import { CheckCircle2, Loader2, RotateCcw, ShieldCheck, Trash2 } from 'lucide-react'
import { formatBytes } from '../../services/workflow'
import { GroupInfo, GroupMember, ReviewItem, ReviewState } from '../../services/reviewApi'
import { VideoViewer } from '../video/VideoViewer'
import { matchActionKey } from '../video/videoReviewLogic'
import { PreviewAction } from './PreviewModal'
import { ruleBadge } from './reviewMeta'

/* ────────────────────────────────────────────────────────────
 * 審核頁的影片播放視窗（v0.7.0，VD-20 至 VD-23）
 * 與「所有影片」頁共用同一個播放器（VideoViewer / VideoPlayer）；這裡只加上審核頁才有的東西：
 * - D / K / R：與大圖預覽相同，只處理**目前這一支**，成功後由 Review 自動跳到下一個
 * - 上一支 / 下一支：Shift+← → 或 PageUp / PageDown（← → 在播放器裡是快轉）
 * - 資訊欄：偵測規則與原因（影片不顯示分數：它是規則固定值，不是 AI 信心度）、重複群組成員（點選可比對路徑）
 * - 不在目前審核清單的群組成員（比對中）是唯讀：不能操作
 * ──────────────────────────────────────────────────────────── */

const STATE_LABEL: Record<ReviewState, string> = { candidate: '待審核', queued: '待刪', kept: '已排除' }
const STATE_CHIP: Record<ReviewState, string> = {
  candidate: 'bg-sky-500/20 text-sky-300 border-sky-500/30',
  queued: 'bg-rose-500/20 text-rose-300 border-rose-500/30',
  kept: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
}
const TONE: Record<PreviewAction['tone'], string> = {
  danger: 'bg-rose-600 hover:bg-rose-500 text-white border-transparent',
  ok: 'bg-slate-800 hover:bg-slate-700 text-emerald-400 border-slate-700',
  info: 'bg-slate-800 hover:bg-slate-700 text-sky-400 border-slate-700',
}

interface Props {
  item: ReviewItem
  position: { index: number; total: number } | null
  comparing: boolean
  hasPrev: boolean
  hasNext: boolean
  actions: PreviewAction[]
  actionsEnabled: boolean
  busy: boolean
  group: GroupInfo | null
  groupLoading: boolean
  onClose: () => void
  onPrev: () => void
  onNext: () => void
  onAction: (target: ReviewState) => void
  onPickMember: (m: GroupMember) => void
}

export const VideoReviewViewer: React.FC<Props> = ({
  item, position, comparing, hasPrev, hasNext, actions, actionsEnabled, busy, group, groupLoading,
  onClose, onPrev, onNext, onAction, onPickMember,
}) => {
  const live = useRef({ actions, actionsEnabled, busy, onAction })
  live.current = { actions, actionsEnabled, busy, onAction }

  // D / K / R：排在播放器按鍵之前處理（播放器本來就不使用這三個鍵）
  const onKeyDown = (e: KeyboardEvent): boolean => {
    const a = live.current
    const target = matchActionKey(e, a.actions)
    if (target === null) return false
    e.preventDefault()
    if (!e.repeat && a.actionsEnabled && !a.busy) a.onAction(target)   // 按住不放不連續送出
    return true
  }

  const rules = item.rules.length > 0 ? item.rules : item.rule ? [item.rule] : []

  return (
    <VideoViewer
      assetId={item.asset_id}
      title={item.filename}
      path={item.primary_path}
      thumbnailUrl={item.thumbnail_url}
      position={position}
      hasPrev={hasPrev}
      hasNext={hasNext}
      onPrev={onPrev}
      onNext={onNext}
      onClose={onClose}
      onKeyDown={onKeyDown}
      fallback={{
        size: item.size_total,
        duration: null,
        width: item.width,
        height: item.height,
        codec: null,
        modifiedAt: null,
      }}
      badges={
        <>
          <span className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${STATE_CHIP[item.state]}`}>
            {STATE_LABEL[item.state]}
          </span>
          {item.is_keeper && (
            <span className="flex shrink-0 items-center gap-1 rounded-md bg-emerald-600/90 px-1.5 py-0.5 text-[10px] font-semibold text-white">
              <ShieldCheck className="h-3 w-3" />
              建議保留
            </span>
          )}
          {comparing && (
            <span className="shrink-0 rounded-md border border-amber-700/60 bg-amber-950/40 px-1.5 py-0.5 text-[10px] font-medium text-amber-300">
              比對中
            </span>
          )}
        </>
      }
      infoExtra={
        <>
          {rules.length > 0 && (
            <section data-testid="video-review-rules">
              <h4 className="mb-2 text-[11px] font-semibold tracking-wide text-slate-500">偵測結果</h4>
              <div className="mb-2 flex flex-wrap gap-1.5">
                {rules.map((r) => {
                  const b = ruleBadge(r)
                  const Icon = b.icon
                  return (
                    <span key={r} className={`flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${b.color}`}>
                      <Icon className="h-3 w-3" />
                      {b.label}
                    </span>
                  )
                })}
              </div>
              {item.reasons.length > 0 && (
                <ul className="space-y-1.5 leading-relaxed text-slate-300">
                  {item.reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              )}
              <p className="mt-2 text-[11px] leading-relaxed text-slate-600">
                影片沒有 AI 分數：規則只看檔案本身（大小完全相同、或小於門檻），所以不會顯示信心度。
              </p>
            </section>
          )}

          {item.group_id !== null && (
            <section data-testid="video-review-group">
              <h4 className="mb-2 text-[11px] font-semibold tracking-wide text-slate-500">
                重複群組 #{item.group_id}
                {group ? ` · ${group.members.length} 支` : ''}
              </h4>
              {!group && groupLoading && (
                <div className="flex items-center gap-2 text-slate-500">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> 載入群組成員...
                </div>
              )}
              {!group && !groupLoading && <div className="text-slate-500">讀不到群組成員。</div>}
              {group && (
                <>
                  <div className="space-y-1.5">
                    {group.members.map((m) => {
                      const current = m.asset_id === item.asset_id
                      return (
                        <button
                          key={m.asset_id}
                          type="button"
                          tabIndex={-1}
                          onMouseDown={(e) => e.preventDefault()}
                          onClick={() => onPickMember(m)}
                          data-member={m.asset_id}
                          title={`${m.filename}\n${m.primary_path}`}
                          className={`flex w-full items-center gap-2 rounded-lg border p-1.5 text-left cursor-pointer ${
                            current ? 'border-sky-500 ring-1 ring-sky-500' : 'border-slate-800 hover:border-slate-600'
                          }`}
                        >
                          <img src={m.thumbnail_url} alt="" loading="lazy" draggable={false} className="h-10 w-14 shrink-0 rounded bg-black object-contain" />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[11px] text-slate-200">{m.filename}</span>
                            <span className="block truncate font-mono text-[10px] text-slate-500">{m.primary_path}</span>
                          </span>
                          <span className="flex shrink-0 flex-col items-end gap-0.5">
                            <span className="text-[10px] text-slate-400">{formatBytes(m.size_total)}</span>
                            {m.is_keeper ? (
                              <span className="rounded bg-emerald-600/90 px-1 text-[9px] font-semibold text-white">保留</span>
                            ) : (
                              m.state !== 'candidate' && (
                                <span className={`rounded border px-1 text-[9px] font-medium ${STATE_CHIP[m.state]}`}>{STATE_LABEL[m.state]}</span>
                              )
                            )}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                  <p className="mt-2 text-[10px] leading-relaxed text-slate-600">
                    這些影片的內容完全相同（SHA-256 一致）；點選可切換播放，用路徑判斷要保留哪一份。
                  </p>
                </>
              )}
            </section>
          )}
        </>
      }
      footer={
        actionsEnabled ? (
          <>
            {actions.map((a) => (
              <button
                key={a.target}
                type="button"
                tabIndex={-1}
                disabled={busy}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => onAction(a.target)}
                data-testid={`preview-action-${a.target}`}
                className={`flex items-center gap-1.5 rounded-lg border px-3.5 py-1.5 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer ${TONE[a.tone]}`}
              >
                {a.target === 'queued' ? <Trash2 className="h-3.5 w-3.5" /> : a.target === 'candidate' ? <RotateCcw className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                {a.label} ({a.key})
              </button>
            ))}
          </>
        ) : (
          <span className="text-xs text-amber-300" data-testid="preview-readonly">
            這支影片不在目前的審核清單裡，只能用來比對；按 Shift+← → 回到清單繼續審核。
          </span>
        )
      }
    />
  )
}
