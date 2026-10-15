import React, { useState } from 'react'
import { Check, Maximize2, ShieldCheck } from 'lucide-react'
import { formatBytes } from '../../services/workflow'
import { ReviewItem } from '../../services/reviewApi'
import { ruleBadge, SCORE_AS_PERCENT_RULES } from './reviewMeta'

/** 卡片固定高度 = 縮圖高 + 資訊區高 + 內距，讓虛擬捲動可以精確計算列高 */
export const CARD_PAD = 18
export const cardMetaHeight = (compact: boolean) => (compact ? 56 : 128)

interface Props {
  item: ReviewItem
  index: number
  selected: boolean
  compact: boolean
  thumbH: number
  height: number
  onToggle: (item: ReviewItem, index: number, e: React.MouseEvent) => void
  /** v0.6.5：開啟大圖預覽（雙擊卡片、縮圖右上角的放大鏡、或按空白鍵） */
  onPreview: (item: ReviewItem) => void
}

const Thumb: React.FC<{ url: string; name: string }> = ({ url, name }) => {
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)
  if (failed) {
    return <div className="absolute inset-0 flex items-center justify-center text-[11px] text-slate-500">預覽不可用</div>
  }
  return (
    <>
      {!loaded && <div className="absolute inset-0 animate-pulse bg-slate-800/60" />}
      <img
        src={url}
        alt={name}
        loading="lazy"
        draggable={false}
        onLoad={() => setLoaded(true)}
        onError={() => setFailed(true)}
        className={`absolute inset-0 h-full w-full object-contain transition-opacity duration-200 ${loaded ? 'opacity-100' : 'opacity-0'}`}
      />
    </>
  )
}

export const ReviewCard: React.FC<Props> = ({ item, index, selected, compact, thumbH, height, onToggle, onPreview }) => {
  const rules = item.rules.length > 0 ? item.rules : [item.rule]
  const shownRules = rules.slice(0, compact ? 1 : 2)
  const extraRules = rules.length - shownRules.length
  const showScore = SCORE_AS_PERCENT_RULES.has(item.rule)
  const resolution = item.width && item.height ? `${item.width}×${item.height}` : null
  const taken = item.taken_at ? new Date(item.taken_at).toLocaleDateString('zh-TW') : null
  const allReasons = item.reasons.length > 0 ? item.reasons.join('\n') : item.reason

  const frame = selected
    ? 'border-sky-500 ring-2 ring-sky-500/50 bg-sky-950/30'
    : item.is_keeper
    ? 'border-emerald-500/60 bg-emerald-950/15'
    : 'border-slate-800 bg-slate-900 hover:border-slate-700'

  return (
    <div
      onClick={(e) => onToggle(item, index, e)}
      onDoubleClick={() => onPreview(item)}
      style={{ height }}
      data-card="1"
      data-asset-id={item.asset_id}
      className={`group relative flex select-none flex-col gap-2 overflow-hidden rounded-2xl border p-[8px] cursor-pointer transition-colors ${frame}`}
      title={`${item.primary_path}\n\n${allReasons}\n\n雙擊或按空白鍵預覽大圖`}
    >
      {/* 縮圖 */}
      <div className="relative w-full shrink-0 overflow-hidden rounded-xl bg-slate-950" style={{ height: thumbH }}>
        <Thumb url={item.thumbnail_url} name={item.filename} />

        <div className="absolute left-1.5 top-1.5 flex items-center gap-1">
          {selected ? (
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-sky-500 text-white shadow">
              <Check className="h-3.5 w-3.5" />
            </span>
          ) : (
            <span className="rounded-md bg-slate-900/80 px-1.5 py-0.5 text-[10px] font-semibold text-slate-200">
              {item.media_type.toUpperCase()}
            </span>
          )}
        </div>

        <div className="absolute right-1.5 top-1.5 flex items-center gap-1">
          {item.is_keeper && (
            <span className="flex items-center gap-1 rounded-md bg-emerald-600/90 px-1.5 py-0.5 text-[10px] font-semibold text-white">
              <ShieldCheck className="h-3 w-3" />
              {compact ? '' : '建議保留'}
            </span>
          )}
          <button
            type="button"
            aria-label="預覽大圖"
            title="預覽大圖（雙擊卡片或按空白鍵）"
            data-preview-btn="1"
            onClick={(e) => {
              e.stopPropagation()
              onPreview(item)
            }}
            onDoubleClick={(e) => e.stopPropagation()}
            className="flex h-6 w-6 items-center justify-center rounded-md bg-slate-900/80 text-slate-200 opacity-0 transition-opacity hover:bg-sky-600 focus:opacity-100 group-hover:opacity-100 cursor-pointer"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
        </div>

        {item.group_id && (
          <span className="absolute bottom-1.5 left-1.5 rounded-md bg-slate-900/85 px-1.5 py-0.5 text-[10px] font-medium text-slate-200">
            組 #{item.group_id}
            {item.group_size ? ` · ${item.group_size} 張` : ''}
          </span>
        )}

        {showScore && (
          <span className="absolute bottom-1.5 right-1.5 rounded-md bg-slate-900/85 px-1.5 py-0.5 text-[10px] font-semibold text-sky-300">
            {Math.round(item.score * 100)}%
          </span>
        )}
      </div>

      {/* 資訊 */}
      <div className="flex min-h-0 flex-1 flex-col gap-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-xs font-semibold text-slate-100">{item.filename}</span>
          <span className="shrink-0 text-[11px] text-slate-400">{formatBytes(item.size_total)}</span>
        </div>

        {!compact && <div className="truncate font-mono text-[10px] text-slate-500">{item.primary_path}</div>}

        <div className="flex items-center gap-1 overflow-hidden">
          {shownRules.map((r) => {
            const b = ruleBadge(r)
            const Icon = b.icon
            return (
              <span key={r} className={`flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${b.color}`}>
                <Icon className="h-3 w-3" />
                {compact ? '' : b.label}
              </span>
            )
          })}
          {extraRules > 0 && <span className="shrink-0 text-[10px] text-slate-500">+{extraRules}</span>}
        </div>

        {!compact && (
          <>
            <div className="line-clamp-2 text-[11px] leading-snug text-slate-400">{item.reason}</div>
            <div className="mt-auto text-[10px] text-slate-500">
              {[resolution, taken].filter(Boolean).join(' · ') || ' '}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
