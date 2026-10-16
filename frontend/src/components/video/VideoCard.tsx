import React, { useState } from 'react'
import { Play, ShieldCheck } from 'lucide-react'
import { formatBytes } from '../../services/workflow'
import { VideoListItem } from '../../services/videoApi'
import { ruleBadge } from '../review/reviewMeta'
import { formatClock, formatResolution, resolutionLabel } from './videoPlayerLogic'

/** 卡片固定高度 = 縮圖高 + 資訊區高 + 內距，讓虛擬捲動可以精確計算列高 */
export const VIDEO_CARD_PAD = 18
export const VIDEO_META_HEIGHT = 96

export const STATUS_CHIP: Record<string, { label: string; cls: string }> = {
  candidate: { label: '待審核', cls: 'bg-sky-500/20 text-sky-300 border-sky-500/30' },
  queued: { label: '待刪', cls: 'bg-rose-500/20 text-rose-300 border-rose-500/30' },
  kept: { label: '已排除', cls: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' },
}

const Thumb: React.FC<{ url: string; name: string }> = ({ url, name }) => {
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)
  if (failed) {
    return <div className="absolute inset-0 flex items-center justify-center text-[11px] text-slate-500">沒有縮圖</div>
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

interface Props {
  item: VideoListItem
  thumbH: number
  height: number
  onOpen: (item: VideoListItem) => void
}

export const VideoCard: React.FC<Props> = ({ item, thumbH, height, onOpen }) => {
  const res = formatResolution(item.width, item.height)
  const resName = resolutionLabel(item.width, item.height)
  const date = item.modified_at ? new Date(item.modified_at).toLocaleDateString('zh-TW') : null
  const status = STATUS_CHIP[item.status]
  const rules = item.rules.slice(0, 1)
  const extraRules = item.rules.length - rules.length
  const line3 = [res, item.codec, date].filter(Boolean).join(' · ')

  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      data-asset-id={item.asset_id}
      data-testid="video-card"
      title={`${item.filename}\n${item.primary_path}`}
      style={{ height }}
      className={`group flex flex-col gap-1.5 overflow-hidden rounded-xl border p-[9px] text-left transition-colors cursor-pointer ${
        item.is_keeper ? 'border-emerald-500/60 bg-emerald-950/15' : 'border-slate-800 bg-slate-900 hover:border-slate-600'
      }`}
    >
      <div className="relative w-full shrink-0 overflow-hidden rounded-lg bg-black" style={{ height: thumbH }}>
        <Thumb url={item.thumbnail_url} name={item.filename} />
        <span className="absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition-all group-hover:bg-black/30 group-hover:opacity-100">
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-black/65 text-white">
            <Play className="ml-0.5 h-5 w-5" />
          </span>
        </span>
        {resName && (
          <span className="absolute left-1.5 top-1.5 rounded bg-slate-900/85 px-1.5 py-0.5 text-[10px] font-semibold text-slate-200">{resName}</span>
        )}
        <span className="absolute bottom-1.5 right-1.5 rounded bg-black/75 px-1.5 py-0.5 font-mono text-[10px] text-slate-100">
          {formatClock(item.duration)}
        </span>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-xs font-semibold text-slate-100">{item.filename}</span>
          <span className="shrink-0 text-[11px] text-slate-400">{formatBytes(item.size_total)}</span>
        </div>
        <div className="truncate font-mono text-[10px] text-slate-500">{item.primary_path}</div>
        <div className="truncate text-[11px] text-slate-400">{line3 || '尚無影片資訊'}</div>
        <div className="mt-auto flex items-center gap-1 overflow-hidden">
          {item.is_keeper && (
            <span className="flex shrink-0 items-center gap-0.5 rounded bg-emerald-600/90 px-1.5 py-0.5 text-[10px] font-semibold text-white">
              <ShieldCheck className="h-3 w-3" />
              建議保留
            </span>
          )}
          {status && <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium ${status.cls}`}>{status.label}</span>}
          {rules.map((r) => {
            const b = ruleBadge(r)
            const Icon = b.icon
            return (
              <span key={r} className={`flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium ${b.color}`}>
                <Icon className="h-3 w-3" />
                {b.label}
              </span>
            )
          })}
          {extraRules > 0 && <span className="shrink-0 text-[10px] text-slate-500">+{extraRules}</span>}
        </div>
      </div>
    </button>
  )
}
