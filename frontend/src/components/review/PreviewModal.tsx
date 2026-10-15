import React, { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Info,
  Loader2,
  RotateCcw,
  ShieldCheck,
  Trash2,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { formatBytes } from '../../services/workflow'
import { GroupInfo, GroupMember, ReviewItem, ReviewState, previewUrl } from '../../services/reviewApi'
import { ruleBadge, SCORE_AS_PERCENT_RULES } from './reviewMeta'

/* ────────────────────────────────────────────────────────────
 * 大圖預覽（v0.6.5）
 * - 左右鍵切換（沿著審核清單的順序）、D / K / R 直接在預覽裡決定，決定後自動跳到下一張
 * - F 切換「適合視窗」/「100%」；I 顯示 / 隱藏資訊欄；Esc 關閉
 * - 資訊欄：檔案資料、偵測原因、重複群組的所有成員（點選即可切換預覽來比較畫質）
 * ──────────────────────────────────────────────────────────── */

export interface PreviewAction {
  target: ReviewState
  label: string
  key: string
  tone: 'danger' | 'ok' | 'info'
}

interface Props {
  item: ReviewItem
  /** 清單裡的位置（1 起算）；total 是符合條件的總張數 */
  position: { index: number; total: number } | null
  /** true = 目前顯示的是群組裡「不在清單」的成員（比對用，不能操作） */
  comparing: boolean
  hasPrev: boolean
  hasNext: boolean
  actions: PreviewAction[]
  actionsEnabled: boolean
  busy: boolean
  group: GroupInfo | null
  groupLoading: boolean
  /** 前後幾張的大圖網址：先載入，切換時才不會等 */
  neighborUrls: string[]
  onClose: () => void
  onPrev: () => void
  onNext: () => void
  onAction: (target: ReviewState) => void
  onPickMember: (m: GroupMember) => void
}

const INFO_KEY = 'pc.review.preview.info'

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

function readInfoPref(): boolean {
  try {
    return window.localStorage.getItem(INFO_KEY) !== '0'
  } catch {
    return true
  }
}

const Row: React.FC<{ label: string; mono?: boolean; children: React.ReactNode }> = ({ label, mono, children }) => (
  <div className="flex gap-3">
    <dt className="w-14 shrink-0 text-slate-500">{label}</dt>
    <dd className={`min-w-0 flex-1 break-all text-slate-200 ${mono ? 'font-mono text-[11px]' : ''}`}>{children}</dd>
  </div>
)

export const PreviewModal: React.FC<Props> = ({
  item, position, comparing, hasPrev, hasNext, actions, actionsEnabled, busy, group, groupLoading, neighborUrls,
  onClose, onPrev, onNext, onAction, onPickMember,
}) => {
  const url = previewUrl(item.asset_id, item.thumbnail_url)
  const [zoom, setZoom] = useState<'fit' | 'actual'>('fit')
  const [showInfo, setShowInfo] = useState<boolean>(readInfoPref)
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading')
  const [errorText, setErrorText] = useState<string | null>(null)
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)
  const urlRef = useRef(url)
  urlRef.current = url

  // 換一張：回到「適合視窗」並重設載入狀態
  useEffect(() => {
    setStatus('loading')
    setErrorText(null)
    setNatural(null)
    setZoom('fit')
  }, [url])

  // 預先載入前後幾張
  const neighborKey = neighborUrls.join('|')
  useEffect(() => {
    neighborUrls.forEach((u) => {
      const img = new Image()
      img.src = u
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [neighborKey])

  const toggleInfo = () => {
    setShowInfo((v) => {
      try {
        window.localStorage.setItem(INFO_KEY, v ? '0' : '1')
      } catch {
        /* ignore */
      }
      return !v
    })
  }

  const onImgError = () => {
    setStatus('error')
    const asked = url
    // 圖片載入失敗時，抓一次網址取得後端的錯誤訊息（檔案不存在 / 無法解碼…）
    fetch(asked)
      .then(async (r) => {
        if (r.ok || urlRef.current !== asked) return
        try {
          const j = await r.json()
          if (typeof j?.detail === 'string') setErrorText(j.detail)
        } catch {
          /* ignore */
        }
      })
      .catch(() => {})
  }

  // 鍵盤
  const live = useRef({ onClose, onPrev, onNext, onAction, actions, actionsEnabled, hasPrev, hasNext, busy })
  live.current = { onClose, onPrev, onNext, onAction, actions, actionsEnabled, hasPrev, hasNext, busy }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const a = live.current
      const k = e.key
      if (k === 'Escape') {
        e.preventDefault()
        a.onClose()
      } else if (k === 'ArrowLeft') {
        e.preventDefault()
        if (a.hasPrev) a.onPrev()
      } else if (k === 'ArrowRight') {
        e.preventDefault()
        if (a.hasNext) a.onNext()
      } else if (k === 'f' || k === 'F') {
        e.preventDefault()
        setZoom((z) => (z === 'fit' ? 'actual' : 'fit'))
      } else if (k === 'i' || k === 'I') {
        e.preventDefault()
        toggleInfo()
      } else {
        const hit = a.actions.find((x) => x.key.toLowerCase() === k.toLowerCase())
        if (hit && a.actionsEnabled && !a.busy) {
          e.preventDefault()
          a.onAction(hit.target)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const rules = item.rules.length > 0 ? item.rules : item.rule ? [item.rule] : []
  const shownScore = SCORE_AS_PERCENT_RULES.has(item.rule) ? `${Math.round(item.score * 100)}%` : item.score.toFixed(2)
  const resolution = item.width && item.height ? `${item.width} × ${item.height}` : '—'
  const taken = item.taken_at ? new Date(item.taken_at).toLocaleString('zh-TW', { hour12: false }) : '—'
  // 非瀏覽器原生格式（HEIC / TIFF / RAW / 影片）會被縮小：提示使用者
  const downscaled = status === 'ok' && natural && item.width ? natural.w < item.width * 0.95 : false

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="相片預覽"
      data-testid="preview-modal"
      className="fixed inset-0 z-50 flex bg-black/95 text-slate-200"
    >
      <div className="relative flex min-w-0 flex-1 flex-col">
        {/* 上方列 */}
        <div className="flex items-center gap-3 border-b border-white/10 px-4 py-2.5">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-slate-100" data-testid="preview-filename">{item.filename}</span>
              <span className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${STATE_CHIP[item.state]}`}>
                {STATE_LABEL[item.state]}
              </span>
              {item.is_keeper && (
                <span className="flex shrink-0 items-center gap-1 rounded-md bg-emerald-600/90 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                  <ShieldCheck className="h-3 w-3" />
                  建議保留
                </span>
              )}
            </div>
            <div className="truncate font-mono text-[11px] text-slate-500">{item.primary_path}</div>
          </div>

          <div className="shrink-0 text-xs text-slate-400" data-testid="preview-counter">
            {position ? `${comparing ? '比對中 · 清單位置 ' : ''}${position.index.toLocaleString()} / ${position.total.toLocaleString()}` : '比對中'}
          </div>

          <button
            type="button"
            onClick={() => setZoom((z) => (z === 'fit' ? 'actual' : 'fit'))}
            title={zoom === 'fit' ? '放大到 100%（F）' : '適合視窗（F）'}
            aria-label={zoom === 'fit' ? '放大到 100%' : '適合視窗'}
            data-testid="preview-zoom"
            className="flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs text-slate-200 hover:bg-slate-700 cursor-pointer"
          >
            {zoom === 'fit' ? <ZoomIn className="h-4 w-4" /> : <ZoomOut className="h-4 w-4" />}
            {zoom === 'fit' ? '100%' : '適合視窗'}
          </button>
          <button
            type="button"
            onClick={toggleInfo}
            title="顯示 / 隱藏資訊欄（I）"
            aria-label="顯示或隱藏資訊欄"
            className={`shrink-0 rounded-lg border p-1.5 cursor-pointer ${showInfo ? 'border-sky-600 bg-sky-950/50 text-sky-300' : 'border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700'}`}
          >
            <Info className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onClose}
            title="關閉（Esc）"
            aria-label="關閉預覽"
            data-testid="preview-close"
            className="shrink-0 rounded-lg border border-slate-700 bg-slate-800 p-1.5 text-slate-300 hover:bg-slate-700 cursor-pointer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 圖片 */}
        <div className="relative min-h-0 flex-1">
          <div
            data-testid="preview-stage"
            className={`absolute inset-0 flex ${zoom === 'actual' ? 'overflow-auto' : 'overflow-hidden'}`}
          >
            {status === 'error' ? (
              <div className="m-auto flex max-w-md flex-col items-center gap-3 px-6 text-center" data-testid="preview-error">
                <AlertTriangle className="h-8 w-8 text-amber-400" />
                <div className="text-sm font-semibold text-slate-100">無法顯示這張相片</div>
                <div className="text-xs leading-relaxed text-slate-400">
                  {errorText ?? '檔案可能已移動、刪除，或所在的磁碟（NAS）目前離線。'}
                </div>
              </div>
            ) : (
              <img
                key={url}
                src={url}
                alt={item.filename}
                draggable={false}
                data-testid="preview-image"
                onLoad={(e) => {
                  const t = e.target as HTMLImageElement
                  setNatural({ w: t.naturalWidth, h: t.naturalHeight })
                  setStatus('ok')
                }}
                onError={onImgError}
                onClick={() => setZoom((z) => (z === 'fit' ? 'actual' : 'fit'))}
                style={
                  zoom === 'fit'
                    ? { maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', margin: 'auto', cursor: 'zoom-in' }
                    : { maxWidth: 'none', maxHeight: 'none', flexShrink: 0, margin: 'auto', cursor: 'zoom-out' }
                }
              />
            )}
          </div>

          {status === 'loading' && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-slate-500" />
            </div>
          )}

          <button
            type="button"
            onClick={onPrev}
            disabled={!hasPrev}
            aria-label="上一張"
            title="上一張（←）"
            data-testid="preview-prev"
            className="absolute left-3 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full border border-white/10 bg-black/50 text-slate-100 hover:bg-black/80 disabled:opacity-20 disabled:cursor-not-allowed cursor-pointer"
          >
            <ChevronLeft className="h-6 w-6" />
          </button>
          <button
            type="button"
            onClick={onNext}
            disabled={!hasNext}
            aria-label="下一張"
            title="下一張（→）"
            data-testid="preview-next"
            className="absolute right-3 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full border border-white/10 bg-black/50 text-slate-100 hover:bg-black/80 disabled:opacity-20 disabled:cursor-not-allowed cursor-pointer"
          >
            <ChevronRight className="h-6 w-6" />
          </button>

          {natural && status === 'ok' && (
            <div className="pointer-events-none absolute bottom-3 left-3 flex flex-col gap-1">
              <span className="rounded-md bg-black/60 px-2 py-1 text-[11px] text-slate-300" data-testid="preview-natural">
                顯示 {natural.w} × {natural.h}
                {zoom === 'actual' ? '（100%）' : ''}
              </span>
              {downscaled && (
                <span className="rounded-md bg-amber-950/80 px-2 py-1 text-[11px] text-amber-200" data-testid="preview-downscaled">
                  這個格式以縮小的預覽顯示，不是原始解析度
                </span>
              )}
            </div>
          )}
        </div>

        {/* 下方列：動作 */}
        <div className="flex flex-wrap items-center justify-center gap-2 border-t border-white/10 px-4 py-2.5">
          {actionsEnabled ? (
            actions.map((a) => (
              <button
                key={a.target}
                type="button"
                disabled={busy}
                onClick={() => onAction(a.target)}
                data-testid={`preview-action-${a.target}`}
                className={`flex items-center gap-1.5 rounded-lg border px-3.5 py-1.5 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer ${TONE[a.tone]}`}
              >
                {a.target === 'queued' ? <Trash2 className="h-3.5 w-3.5" /> : a.target === 'candidate' ? <RotateCcw className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                {a.label} ({a.key})
              </button>
            ))
          ) : (
            <span className="text-xs text-amber-300" data-testid="preview-readonly">
              這張相片不在目前的審核清單裡，只能用來比對；按 ← → 回到清單繼續審核。
            </span>
          )}
          <span className="ml-2 hidden text-[11px] text-slate-500 lg:inline">← → 切換　F 放大　I 資訊　Esc 關閉</span>
        </div>
      </div>

      {/* 資訊欄 */}
      {showInfo && (
        <aside
          data-testid="preview-info"
          className="flex w-80 shrink-0 flex-col gap-5 overflow-y-auto border-l border-white/10 bg-slate-950 p-4 text-xs"
        >
          <section>
            <h4 className="mb-2 text-[11px] font-semibold tracking-wide text-slate-500">基本資料</h4>
            <dl className="space-y-1.5">
              <Row label="檔名">{item.filename}</Row>
              <Row label="路徑" mono>{item.primary_path}</Row>
              <Row label="大小">{formatBytes(item.size_total)}</Row>
              <Row label="解析度">{resolution}</Row>
              <Row label="拍攝時間">{taken}</Row>
              <Row label="類型">{item.media_type.toUpperCase()}</Row>
            </dl>
          </section>

          {rules.length > 0 && (
            <section>
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
              {item.reasons.length > 0 && <div className="mt-2 text-slate-500">分數 {shownScore}</div>}
            </section>
          )}

          {item.group_id !== null && (
            <section data-testid="preview-group">
              <h4 className="mb-2 text-[11px] font-semibold tracking-wide text-slate-500">
                重複群組 #{item.group_id}
                {group ? ` · ${group.members.length} 張` : ''}
              </h4>
              {!group && groupLoading && (
                <div className="flex items-center gap-2 text-slate-500">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> 載入群組成員...
                </div>
              )}
              {!group && !groupLoading && <div className="text-slate-500">讀不到群組成員。</div>}
              {group && (
                <>
                  <div className="grid grid-cols-3 gap-2">
                    {group.members.map((m) => {
                      const current = m.asset_id === item.asset_id
                      const res = m.width && m.height ? `${m.width}×${m.height}` : ''
                      return (
                        <button
                          key={m.asset_id}
                          type="button"
                          onClick={() => onPickMember(m)}
                          data-member={m.asset_id}
                          title={`${m.filename}\n${m.primary_path}`}
                          className={`rounded-lg border p-1 text-left cursor-pointer ${
                            current ? 'border-sky-500 ring-1 ring-sky-500' : 'border-slate-800 hover:border-slate-600'
                          }`}
                        >
                          <div className="relative aspect-square overflow-hidden rounded bg-slate-900">
                            <img src={m.thumbnail_url} alt={m.filename} loading="lazy" draggable={false} className="h-full w-full object-contain" />
                            {m.is_keeper && (
                              <span className="absolute left-1 top-1 rounded bg-emerald-600/90 px-1 text-[9px] font-semibold text-white">保留</span>
                            )}
                            {m.state !== 'candidate' && (
                              <span className={`absolute right-1 top-1 rounded border px-1 text-[9px] font-medium ${STATE_CHIP[m.state]}`}>
                                {STATE_LABEL[m.state]}
                              </span>
                            )}
                          </div>
                          <div className="mt-1 truncate text-[10px] text-slate-300">{formatBytes(m.size_total)}</div>
                          <div className="truncate text-[10px] text-slate-500">
                            {[res, m.sharpness !== null ? `清晰度 ${Math.round(m.sharpness)}` : ''].filter(Boolean).join(' · ') || '—'}
                          </div>
                        </button>
                      )
                    })}
                  </div>
                  <p className="mt-2 text-[10px] leading-relaxed text-slate-600">
                    點選群組裡的相片可以切換預覽，方便比較畫質；清晰度越高越清楚。
                  </p>
                </>
              )}
            </section>
          )}
        </aside>
      )}
    </div>
  )
}
