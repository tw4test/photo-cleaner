import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useVirtualizer } from '@tanstack/react-virtual'
import { AlertTriangle, ArrowDown, ArrowUp, Film, Loader2, RefreshCw, Search, X } from 'lucide-react'
import { formatBytes } from '../services/workflow'
import {
  FfmpegStatus,
  SortOrder,
  VideoListItem,
  VideoSortKey,
  VideoSummary,
  videoApi,
} from '../services/videoApi'
import { VIDEO_CARD_PAD, VIDEO_META_HEIGHT, STATUS_CHIP, VideoCard } from '../components/video/VideoCard'
import { VideoViewer } from '../components/video/VideoViewer'
import { ruleBadge } from '../components/review/reviewMeta'

/* ────────────────────────────────────────────────────────────
 * 所有影片（v0.7.0，VD-01 至 VD-05）
 * - 列出資料庫裡所有影片（不論有沒有命中規則；排除已刪除 / 遺失）。**只讀**：不能加入待刪、不能改任何狀態。
 * - 搜尋（路徑關鍵字）、排序（名稱 / 日期 / 大小 / 時長 / 解析度）、無限捲動（一次向後端要 100 支）
 * - 點卡片開啟播放視窗；播放視窗裡可以上一支 / 下一支（沿著目前的清單）
 * - 頁面自己負責捲動（UI-05）
 * ──────────────────────────────────────────────────────────── */

const PAGE = 100
const GAP = 12
const MIN_CARD_W = 230
const DEFAULT_ORDER: Record<VideoSortKey, SortOrder> = { name: 'asc', date: 'desc', size: 'desc', duration: 'desc', resolution: 'desc' }
const SORTS: Array<{ key: VideoSortKey; label: string }> = [
  { key: 'name', label: '名稱' },
  { key: 'date', label: '日期（修改時間）' },
  { key: 'size', label: '大小' },
  { key: 'duration', label: '時長' },
  { key: 'resolution', label: '解析度' },
]

const errMsg = (e: unknown, fallback = '載入失敗') => (e instanceof Error && e.message ? e.message : fallback)

export const Videos: React.FC = () => {
  const [qInput, setQInput] = useState('')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<VideoSortKey>('name')
  const [orderChoice, setOrderChoice] = useState<SortOrder | null>(null)
  const order: SortOrder = orderChoice ?? DEFAULT_ORDER[sort]

  const [items, setItems] = useState<VideoListItem[]>([])
  const [total, setTotal] = useState(0)
  const [totalSize, setTotalSize] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [summary, setSummary] = useState<VideoSummary | null>(null)
  const [ffmpeg, setFfmpeg] = useState<FfmpegStatus | null>(null)
  const [showFfmpegHelp, setShowFfmpegHelp] = useState(false)
  const [viewerId, setViewerId] = useState<number | null>(null)

  const parentRef = useRef<HTMLDivElement | null>(null)
  const epoch = useRef(0)                    // 重新載入時 +1，讓進行中的舊請求作廢
  const loadingMoreRef = useRef(false)
  const itemsRef = useRef<VideoListItem[]>([])
  itemsRef.current = items
  const hasMore = items.length < total

  // 搜尋輸入防抖
  useEffect(() => {
    const t = window.setTimeout(() => setQ(qInput), 300)
    return () => window.clearTimeout(t)
  }, [qInput])

  const loadSummary = useCallback(async () => {
    try {
      setSummary(await videoApi.summary())
    } catch {
      /* 數量只是輔助資訊，失敗不影響清單 */
    }
  }, [])

  useEffect(() => {
    void loadSummary()
    videoApi.ffmpeg().then(setFfmpeg).catch(() => {})
  }, [loadSummary])

  const fetchFirst = useCallback(async () => {
    const my = ++epoch.current
    setLoading(true)
    setError(null)
    try {
      const p = await videoApi.list({ q, sort, order, offset: 0, limit: PAGE })
      if (my !== epoch.current) return
      setItems(p.items)
      setTotal(p.total)
      setTotalSize(p.total_size)
    } catch (e) {
      if (my === epoch.current) setError(errMsg(e))
    } finally {
      if (my === epoch.current) setLoading(false)
    }
  }, [q, sort, order])

  // 搜尋 / 排序改變：關閉播放視窗、回到最上面、重新載入
  useEffect(() => {
    setViewerId(null)
    parentRef.current?.scrollTo({ top: 0 })
    void fetchFirst()
  }, [fetchFirst])

  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current) return
    const my = epoch.current
    loadingMoreRef.current = true
    setLoadingMore(true)
    try {
      const p = await videoApi.list({ q, sort, order, offset: itemsRef.current.length, limit: PAGE })
      if (my !== epoch.current) return
      setItems((prev) => {
        const seen = new Set(prev.map((i) => i.asset_id))
        return prev.concat(p.items.filter((i) => !seen.has(i.asset_id)))
      })
      setTotal(p.total)
      setTotalSize(p.total_size)
    } catch (e) {
      if (my === epoch.current) setError(errMsg(e))
    } finally {
      loadingMoreRef.current = false
      setLoadingMore(false)
    }
  }, [q, sort, order])

  const refresh = () => {
    void fetchFirst()
    void loadSummary()
  }

  /* ───────── 虛擬捲動 ───────── */
  const [gridW, setGridW] = useState(900)
  useEffect(() => {
    const el = parentRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setGridW(el.clientWidth))
    ro.observe(el)
    setGridW(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const innerW = Math.max(240, gridW - 8)
  const cols = Math.max(2, Math.min(8, Math.floor((innerW + GAP) / (MIN_CARD_W + GAP))))
  const cardW = (innerW - GAP * (cols - 1)) / cols
  const thumbH = Math.max(60, Math.round((cardW - VIDEO_CARD_PAD) * 0.5625))
  const cardH = thumbH + VIDEO_META_HEIGHT + VIDEO_CARD_PAD
  const rowH = cardH + GAP
  const itemRows = Math.ceil(items.length / cols)
  const rowCount = itemRows + (hasMore ? 1 : 0)

  const rowVirtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowH,
    overscan: 3,
  })
  useEffect(() => {
    rowVirtualizer.measure()
  }, [rowH, rowVirtualizer])

  const virtualRows = rowVirtualizer.getVirtualItems()
  const lastRow = virtualRows.length > 0 ? virtualRows[virtualRows.length - 1].index : -1
  useEffect(() => {
    if (hasMore && !loading && !loadingMore && !error && lastRow >= itemRows - 3) void loadMore()
  }, [lastRow, itemRows, hasMore, loading, loadingMore, error, loadMore])

  /* ───────── 播放視窗：沿著目前的清單切換 ───────── */
  const viewerIndex = viewerId === null ? -1 : items.findIndex((i) => i.asset_id === viewerId)
  const viewerItem = viewerIndex >= 0 ? items[viewerIndex] : null
  const pendingNext = useRef(false)

  const goPrev = () => {
    if (viewerIndex > 0) setViewerId(items[viewerIndex - 1].asset_id)
  }
  const goNext = () => {
    if (viewerIndex < 0) return
    if (viewerIndex < items.length - 1) setViewerId(items[viewerIndex + 1].asset_id)
    else if (hasMore) {
      pendingNext.current = true      // 已經在最後一支但後面還有：載入下一頁後自動前進
      void loadMore()
    }
  }
  useEffect(() => {
    if (pendingNext.current && viewerIndex >= 0 && viewerIndex < items.length - 1) {
      pendingNext.current = false
      setViewerId(items[viewerIndex + 1].asset_id)
    }
  }, [items, viewerIndex])
  useEffect(() => {
    // 快看到清單尾端時先載入下一頁
    if (viewerIndex >= 0 && hasMore && !loadingMore && viewerIndex >= items.length - 4) void loadMore()
  }, [viewerIndex, items.length, hasMore, loadingMore, loadMore])

  // 切到別支時，讓清單裡對應的卡片保持在畫面內（關閉播放視窗後就停在這裡）
  useEffect(() => {
    if (viewerIndex < 0) return
    const el = parentRef.current
    if (!el) return
    const top = Math.floor(viewerIndex / cols) * rowH
    if (top < el.scrollTop || top + cardH > el.scrollTop + el.clientHeight) {
      el.scrollTo({ top: Math.max(0, top - (el.clientHeight - cardH) / 2) })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerId])

  const pickSort = (s: VideoSortKey) => {
    setSort(s)
    setOrderChoice(null)       // 換排序欄位時回到該欄位的預設方向
  }

  const searching = q.trim() !== ''
  const overallCount = summary?.count
  const noVideosAtAll = !loading && !error && !searching && items.length === 0

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-5">
      {/* 標題與摘要 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-xl font-bold text-slate-100">
            <Film className="h-5 w-5 text-sky-400" />
            所有影片
          </h2>
          <p className="mt-0.5 text-xs text-slate-400" data-testid="videos-summary">
            {overallCount !== undefined ? (
              <>
                資料庫共 <b className="text-slate-200">{overallCount.toLocaleString()}</b> 支（{formatBytes(summary?.total_size ?? 0)}）
              </>
            ) : (
              '讀取中...'
            )}
            {searching && !loading && (
              <>
                ；符合搜尋 <b className="text-slate-200">{total.toLocaleString()}</b> 支（{formatBytes(totalSize)}）
              </>
            )}
            <span className="ml-2 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400" title="這個頁面不會改變任何影片的狀態，也不能加入待刪">
              只讀
            </span>
          </p>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {ffmpeg && (
            <button
              type="button"
              onClick={() => setShowFfmpegHelp((v) => !v)}
              data-testid="videos-ffmpeg-chip"
              title={ffmpeg.available ? (ffmpeg.version ?? 'ffmpeg 可用') : '點一下看安裝說明'}
              className={`rounded-lg border px-2.5 py-1 text-[11px] cursor-pointer ${
                ffmpeg.available
                  ? 'border-emerald-800/60 bg-emerald-950/30 text-emerald-300 hover:bg-emerald-950/50'
                  : 'border-amber-800/60 bg-amber-950/30 text-amber-300 hover:bg-amber-950/50'
              }`}
            >
              {ffmpeg.available ? 'ffmpeg 可用' : '未偵測到 ffmpeg（選用）'}
            </button>
          )}
          <button
            type="button"
            onClick={refresh}
            title="重新整理"
            aria-label="重新整理"
            className="rounded-lg border border-slate-700 bg-slate-800 p-1.5 text-slate-400 hover:text-slate-200 cursor-pointer"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {showFfmpegHelp && ffmpeg && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3 text-xs leading-relaxed text-slate-300" data-testid="videos-ffmpeg-help">
          {ffmpeg.available ? (
            <>
              ffmpeg 可用：{ffmpeg.version ?? ffmpeg.ffmpeg_path}。AVI / WMV / FLV / 部分 MKV / HEVC 會在播放時詢問是否轉成 H.264 MP4；
              轉檔快取 {formatBytes(ffmpeg.cache.bytes)} / {formatBytes(ffmpeg.cache.limit_bytes)}。
            </>
          ) : (
            <>
              {ffmpeg.install_hint}
              <div className="mt-1 text-slate-500">沒有 ffmpeg 時其他功能不受影響；瀏覽器能直接播放的影片（H.264 MP4 等）照常播放。</div>
            </>
          )}
        </div>
      )}

      {/* 搜尋與排序 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-2xl border border-slate-800 bg-slate-900/60 px-4 py-2 text-xs text-slate-400">
        <label className="relative flex min-w-[220px] flex-1 items-center">
          <Search className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-slate-500" />
          <input
            type="text"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="搜尋檔名或資料夾（空白分隔多個關鍵字）"
            aria-label="搜尋影片"
            data-testid="videos-search"
            className="w-full rounded-lg border border-slate-700 bg-slate-900 py-1.5 pl-8 pr-7 text-xs text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none"
          />
          {qInput && (
            <button
              type="button"
              onClick={() => setQInput('')}
              aria-label="清除搜尋"
              className="absolute right-1.5 rounded p-0.5 text-slate-500 hover:text-slate-200 cursor-pointer"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </label>

        <label className="flex items-center gap-2">
          排序
          <select
            value={sort}
            onChange={(e) => pickSort(e.target.value as VideoSortKey)}
            data-testid="videos-sort"
            className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200 focus:border-sky-500 focus:outline-none cursor-pointer"
          >
            {SORTS.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() => setOrderChoice(order === 'asc' ? 'desc' : 'asc')}
          data-testid="videos-order"
          title="切換升冪 / 降冪"
          className="flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1 text-xs text-slate-200 hover:bg-slate-700 cursor-pointer"
        >
          {order === 'asc' ? <ArrowUp className="h-3.5 w-3.5" /> : <ArrowDown className="h-3.5 w-3.5" />}
          {order === 'asc' ? '升冪' : '降冪'}
        </button>
        <span className="text-slate-500">
          已載入 {items.length.toLocaleString()}
          {total > 0 ? ` / ${total.toLocaleString()}` : ''}
        </span>
        {(sort === 'duration' || sort === 'resolution') && (
          <span className="text-[11px] text-slate-600">還沒有影片資訊的影片會排在最後（到資料庫掃描頁執行「影片」分析階段）。</span>
        )}
      </div>

      {/* 卡片區 */}
      <div ref={parentRef} data-testid="videos-grid" className="relative min-h-0 flex-1 overflow-y-auto rounded-2xl">
        {loading && items.length === 0 ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin" /> 載入影片列表中...
          </div>
        ) : error && items.length === 0 ? (
          <div className="mx-auto mt-10 flex max-w-md flex-col items-center gap-3 rounded-2xl border border-rose-800/60 bg-rose-950/30 p-6 text-center text-sm text-rose-300">
            <AlertTriangle className="h-5 w-5" />
            {error}
            <button type="button" onClick={refresh} className="rounded-lg border border-rose-700 px-3 py-1 text-xs hover:bg-rose-900/40 cursor-pointer">
              重試
            </button>
          </div>
        ) : items.length === 0 ? (
          <div className="mx-auto mt-10 flex max-w-md flex-col items-center gap-3 rounded-2xl border border-slate-800 bg-slate-900/60 p-8 text-center">
            <div className="text-sm font-semibold text-slate-200">
              {noVideosAtAll ? '資料庫裡還沒有影片' : `沒有符合「${q.trim()}」的影片`}
            </div>
            <div className="text-xs text-slate-500">
              {noVideosAtAll ? '掃描資料夾之後，影片會出現在這裡（支援 mp4 / mov / avi / mkv / wmv / flv / webm / m4v）。' : '試試其他關鍵字，或清除搜尋。'}
            </div>
            {noVideosAtAll && (
              <Link to="/library" className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-500">
                前往資料庫掃描
              </Link>
            )}
          </div>
        ) : (
          <div style={{ height: rowVirtualizer.getTotalSize(), position: 'relative' }}>
            {virtualRows.map((vr) => {
              if (vr.index >= itemRows) {
                return (
                  <div
                    key="loader"
                    className="absolute left-0 right-0 flex items-center justify-center gap-2 text-xs text-slate-500"
                    style={{ top: 0, height: rowH, transform: `translateY(${vr.start}px)` }}
                  >
                    {error ? (
                      <>
                        <AlertTriangle className="h-3.5 w-3.5 text-rose-400" /> {error}
                        <button type="button" onClick={() => { setError(null); void loadMore() }} className="underline hover:text-slate-300 cursor-pointer">
                          重試
                        </button>
                      </>
                    ) : (
                      <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin" /> 載入更多...
                      </>
                    )}
                  </div>
                )
              }
              const start = vr.index * cols
              return (
                <div
                  key={vr.key}
                  className="absolute left-0 right-0 grid px-1"
                  style={{
                    top: 0,
                    height: cardH,
                    transform: `translateY(${vr.start}px)`,
                    gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
                    gap: GAP,
                  }}
                >
                  {items.slice(start, start + cols).map((item) => (
                    <VideoCard key={item.asset_id} item={item} thumbH={thumbH} height={cardH} onOpen={(it) => setViewerId(it.asset_id)} />
                  ))}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {viewerItem && (
        <VideoViewer
          assetId={viewerItem.asset_id}
          title={viewerItem.filename}
          path={viewerItem.primary_path}
          thumbnailUrl={viewerItem.thumbnail_url}
          position={{ index: viewerIndex + 1, total }}
          hasPrev={viewerIndex > 0}
          hasNext={viewerIndex < items.length - 1 || hasMore}
          onPrev={goPrev}
          onNext={goNext}
          onClose={() => setViewerId(null)}
          fallback={{
            size: viewerItem.size_total,
            duration: viewerItem.duration,
            width: viewerItem.width,
            height: viewerItem.height,
            codec: viewerItem.codec,
            modifiedAt: viewerItem.modified_at,
          }}
          badges={
            STATUS_CHIP[viewerItem.status] ? (
              <span className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${STATUS_CHIP[viewerItem.status].cls}`}>
                {STATUS_CHIP[viewerItem.status].label}
              </span>
            ) : null
          }
          infoExtra={
            viewerItem.rules.length > 0 ? (
              <section>
                <h4 className="mb-2 text-[11px] font-semibold tracking-wide text-slate-500">偵測結果</h4>
                <div className="flex flex-wrap gap-1.5">
                  {viewerItem.rules.map((r) => {
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
                <p className="mt-2 text-[11px] leading-relaxed text-slate-500">影片的分數是規則固定值，不是 AI 信心度。要加入待刪請到審核工作台的「影片」分類。</p>
              </section>
            ) : null
          }
          footer={<span className="text-xs text-slate-500">這個頁面只能瀏覽與播放，不能加入待刪；要審核影片請到審核工作台的「影片」分類。</span>}
        />
      )}
    </div>
  )
}
