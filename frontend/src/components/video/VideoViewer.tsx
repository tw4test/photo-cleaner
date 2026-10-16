import React, { useEffect, useRef, useState } from 'react'
import { Info, X } from 'lucide-react'
import { formatBytes } from '../../services/workflow'
import { VideoInfo } from '../../services/videoApi'
import { VideoPlayer, VideoPlayerHandle } from './VideoPlayer'
import { formatClock, formatResolution, resolutionLabel } from './videoPlayerLogic'

/* ────────────────────────────────────────────────────────────
 * 影片播放視窗（v0.7.0）：「所有影片」頁與審核頁共用。
 * - 全螢幕覆蓋視窗：上方標題列、中間播放器、下方動作列（footer，審核頁放 D / K / R）、右側資訊欄
 * - 劇院模式：隱藏資訊欄與動作列，播放器佔滿視窗（記住設定）
 * - 鍵盤：onKeyDown（外層，例如 D / K / R）先處理，其次是播放器（空白鍵、← →、Shift+← →、PageUp / PageDown…），
 *   最後是本視窗的 Esc（關閉）與 I（資訊欄）
 * ──────────────────────────────────────────────────────────── */

export interface ViewerFallbackMeta {
  size: number
  duration: number | null
  width: number | null
  height: number | null
  codec: string | null
  modifiedAt: string | null
}

export interface VideoViewerProps {
  assetId: number
  title: string
  path: string
  thumbnailUrl?: string
  /** 清單裡的位置（1 起算）與總數 */
  position?: { index: number; total: number } | null
  hasPrev: boolean
  hasNext: boolean
  onPrev: () => void
  onNext: () => void
  onClose: () => void
  /** 標題旁的徽章（審核狀態、建議保留…） */
  badges?: React.ReactNode
  /** 資訊欄裡額外的區塊（偵測結果、群組…） */
  infoExtra?: React.ReactNode
  /** 下方動作列（審核頁的 D / K / R 按鈕） */
  footer?: React.ReactNode
  /** 外層先處理的按鍵；回傳 true = 已處理，不再交給播放器 */
  onKeyDown?: (e: KeyboardEvent) => boolean
  /** 還沒讀到 ffprobe 資訊時，資訊欄先顯示的資料（來自清單） */
  fallback?: ViewerFallbackMeta
}

const INFO_KEY = 'pc.video.viewer.info'
const THEATER_KEY = 'pc.video.viewer.theater'

function readFlag(key: string, fallback: boolean): boolean {
  try {
    const v = window.localStorage.getItem(key)
    return v === null ? fallback : v === '1'
  } catch {
    return fallback
  }
}

function writeFlag(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, value ? '1' : '0')
  } catch {
    /* ignore */
  }
}

const Row: React.FC<{ label: string; mono?: boolean; children: React.ReactNode }> = ({ label, mono, children }) => (
  <div className="flex gap-3">
    <dt className="w-14 shrink-0 text-slate-500">{label}</dt>
    <dd className={`min-w-0 flex-1 break-all text-slate-200 ${mono ? 'font-mono text-[11px]' : ''}`}>{children}</dd>
  </div>
)

export const VideoViewer: React.FC<VideoViewerProps> = ({
  assetId, title, path, thumbnailUrl, position, hasPrev, hasNext, onPrev, onNext, onClose, badges, infoExtra, footer,
  onKeyDown, fallback,
}) => {
  const playerRef = useRef<VideoPlayerHandle | null>(null)
  const [info, setInfo] = useState<VideoInfo | null>(null)
  const [showInfo, setShowInfo] = useState<boolean>(() => readFlag(INFO_KEY, true))
  const [theater, setTheater] = useState<boolean>(() => readFlag(THEATER_KEY, false))

  const toggleTheater = () =>
    setTheater((v) => {
      writeFlag(THEATER_KEY, !v)
      return !v
    })
  const toggleInfo = () =>
    setShowInfo((v) => {
      writeFlag(INFO_KEY, !v)
      return !v
    })

  // 開啟時讓搜尋框 / 按鈕失去焦點，鍵盤才會直接進到播放器
  useEffect(() => {
    const el = document.activeElement as HTMLElement | null
    if (el && typeof el.blur === 'function') el.blur()
  }, [])

  const live = useRef({ onClose, onPrev, onNext, hasPrev, hasNext, onKeyDown, toggleInfo })
  live.current = { onClose, onPrev, onNext, hasPrev, hasNext, onKeyDown, toggleInfo }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) return
      const a = live.current
      if (a.onKeyDown && a.onKeyDown(e)) return
      if (playerRef.current && playerRef.current.handleKey(e)) return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      if (e.key === 'Escape') {
        e.preventDefault()
        a.onClose()
      } else if (e.key === 'i' || e.key === 'I') {
        e.preventDefault()
        a.toggleInfo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const size = info?.size ?? fallback?.size
  const duration = info?.duration ?? fallback?.duration ?? null
  const width = info?.width ?? fallback?.width ?? null
  const height = info?.height ?? fallback?.height ?? null
  const res = formatResolution(width, height)
  const resName = resolutionLabel(width, height)
  const vcodec = info?.video_codec ?? fallback?.codec ?? null
  const modified = fallback?.modifiedAt ? new Date(fallback.modifiedAt).toLocaleString('zh-TW', { hour12: false }) : null
  const playMode = !info
    ? '—'
    : info.transcode.status === 'done' && info.needs_transcode
    ? '已轉檔（H.264 MP4 快取）'
    : info.playback === 'try_first'
    ? '先試播原檔（不行再轉檔）'
    : info.direct_playable
    ? '原檔直接播放'
    : '需要轉檔'

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="影片播放"
      data-testid="video-viewer"
      className="fixed inset-0 z-50 flex bg-black/95 text-slate-200"
    >
      <div className="relative flex min-w-0 flex-1 flex-col">
        {/* 上方列 */}
        <div className="flex items-center gap-3 border-b border-white/10 px-4 py-2.5">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-slate-100" data-testid="video-title">{title}</span>
              {badges}
            </div>
            {!theater && <div className="truncate font-mono text-[11px] text-slate-500">{path}</div>}
          </div>
          {position && (
            <div className="shrink-0 text-xs text-slate-400" data-testid="video-counter">
              {position.index.toLocaleString()} / {position.total.toLocaleString()}
            </div>
          )}
          <button
            type="button"
            onClick={toggleInfo}
            title="顯示 / 隱藏資訊欄（I）"
            aria-label="顯示或隱藏資訊欄"
            className={`shrink-0 rounded-lg border p-1.5 cursor-pointer ${
              showInfo && !theater ? 'border-sky-600 bg-sky-950/50 text-sky-300' : 'border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700'
            }`}
          >
            <Info className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onClose}
            title="關閉（Esc）"
            aria-label="關閉播放"
            data-testid="video-close"
            className="shrink-0 rounded-lg border border-slate-700 bg-slate-800 p-1.5 text-slate-300 hover:bg-slate-700 cursor-pointer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 播放器 */}
        <div className="relative min-h-0 flex-1">
          <VideoPlayer
            ref={playerRef}
            assetId={assetId}
            poster={thumbnailUrl}
            theater={theater}
            onToggleTheater={toggleTheater}
            onPrev={hasPrev ? onPrev : undefined}
            onNext={hasNext ? onNext : undefined}
            onInfo={setInfo}
          />
        </div>

        {/* 下方列 */}
        {!theater && (
          <div className="flex flex-wrap items-center justify-center gap-2 border-t border-white/10 px-4 py-2.5">
            {footer}
            <span className="ml-2 hidden text-[11px] text-slate-500 lg:inline">
              空白鍵 播放　← → 快轉　Shift+← → 或 PageUp / PageDown 上一支 / 下一支　F 全螢幕　T 劇院　P 子母畫面　Esc 關閉
            </span>
          </div>
        )}
      </div>

      {/* 資訊欄 */}
      {showInfo && !theater && (
        <aside
          data-testid="video-info"
          className="flex w-80 shrink-0 flex-col gap-5 overflow-y-auto border-l border-white/10 bg-slate-950 p-4 text-xs"
        >
          <section>
            <h4 className="mb-2 text-[11px] font-semibold tracking-wide text-slate-500">影片資料</h4>
            <dl className="space-y-1.5">
              <Row label="檔名">{title}</Row>
              <Row label="路徑" mono>{path}</Row>
              <Row label="大小">{size !== undefined ? formatBytes(size) : '—'}</Row>
              <Row label="時長">{duration ? formatClock(duration) : '—'}</Row>
              <Row label="解析度">{res ? `${res}${resName ? `（${resName}）` : ''}` : '—'}</Row>
              <Row label="影片編碼">{vcodec ?? '—'}</Row>
              <Row label="音訊編碼">{info ? info.audio_codec ?? '無音訊' : '—'}</Row>
              <Row label="容器">{info?.container ?? '—'}</Row>
              {modified && <Row label="修改時間">{modified}</Row>}
              <Row label="播放方式">{playMode}</Row>
            </dl>
            {info && info.playback !== 'direct' && info.reason && (
              <p className="mt-2 text-[11px] leading-relaxed text-slate-500">{info.reason}</p>
            )}
          </section>
          {infoExtra}
        </aside>
      )}
    </div>
  )
}
