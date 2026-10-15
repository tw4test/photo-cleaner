import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Filter,
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import { formatBytes } from '../services/workflow'
import {
  BulkFilter,
  BulkPreview,
  CategoryKey,
  GroupInfo,
  GroupMember,
  KeeperMode,
  ReviewItem,
  ReviewState,
  ReviewSummary,
  SortKey,
  previewUrl,
  reviewApi,
} from '../services/reviewApi'
import { ReviewCard, CARD_PAD, cardMetaHeight } from '../components/review/ReviewCard'
import { ConfirmDialog, SampleThumb } from '../components/review/ConfirmDialog'
import { PreviewModal } from '../components/review/PreviewModal'
import {
  CATEGORIES,
  WAIVABLE_CATEGORIES,
  defaultKeepers,
  defaultSort,
  isAi,
} from '../components/review/reviewMeta'
import { REVIEW_FILTERS_COLLAPSED_KEY, useLayoutPref } from '../hooks/useLayoutPref'

/* ────────────────────────────────────────────────────────────
 * 審核工作台（v0.6.5）
 * - 一次只向後端要一頁（200 張），捲到底再載入下一頁；統計與分頁都在後端完成
 * - 狀態變更直接更新畫面（不重新載入、不丟失捲動位置），並提供「復原」
 * - 「選取符合條件的全部 N 張」：依條件在後端批次處理，先預覽（張數、容量、隨機抽樣）再確認
 * - 螢幕截圖 / 文件分類：不受「群組至少保留一張」保護（使用者決定）
 * - 左側篩選欄可收合成窄欄；主選單的收合在 AppShell，按 [ 可一次收合 / 展開兩者
 * - v0.6.5：大圖預覽（雙擊卡片 / 縮圖右上角放大鏡 / 空白鍵）：左右鍵切換、D / K / R 直接決定並跳到下一張、
 *   100% 檢視、檔案與偵測資訊、重複群組成員（點選即可切換比對）
 * ──────────────────────────────────────────────────────────── */

const PAGE = 200
const CONFIRM_OVER = 200
const GAP = 12
const MIN_OFF = 0.3          // 分數滑桿的最小值 = 不篩選
const COLS_KEY = 'pc.review.cols'

type Selection = { mode: 'ids'; ids: Set<number> } | { mode: 'filter'; excluded: Set<number> }

interface Toast {
  text: string
  tone: 'ok' | 'warn' | 'err'
  undo: string | null
}

interface DialogState {
  target: ReviewState
  count: number
  sizeBytes: number
  sample: SampleThumb[]
  sampleLabel: string
  waived: boolean
  mode: 'ids' | 'filter'
  ids: number[]
  excluded: number[]
  /** 預覽當下的篩選條件：確認時用同一份，避免條件改變後套到不同的相片 */
  filter: BulkFilter | null
}

interface ActionDef {
  target: ReviewState
  label: string
  key: string
  tone: 'danger' | 'ok' | 'info'
}

const ACTIONS: Record<ReviewState, ActionDef[]> = {
  candidate: [
    { target: 'kept', label: '排除保留', key: 'K', tone: 'ok' },
    { target: 'queued', label: '加入待刪', key: 'D', tone: 'danger' },
  ],
  queued: [
    { target: 'candidate', label: '移出待刪', key: 'R', tone: 'info' },
    { target: 'kept', label: '排除保留', key: 'K', tone: 'ok' },
  ],
  kept: [
    { target: 'candidate', label: '恢復候選', key: 'R', tone: 'info' },
    { target: 'queued', label: '加入待刪', key: 'D', tone: 'danger' },
  ],
}

const TONE_CLASS: Record<ActionDef['tone'], string> = {
  danger: 'bg-rose-600 hover:bg-rose-500 text-white shadow-md shadow-rose-600/20 border-transparent',
  ok: 'bg-slate-800 hover:bg-slate-700 text-emerald-400 border-slate-700',
  info: 'bg-slate-800 hover:bg-slate-700 text-sky-400 border-slate-700',
}

const STATE_TABS: Array<{ key: ReviewState; label: string; short: string; active: string }> = [
  { key: 'candidate', label: '候選', short: '候', active: 'bg-sky-600 text-white shadow' },
  { key: 'queued', label: '待刪', short: '刪', active: 'bg-rose-600 text-white shadow' },
  { key: 'kept', label: '已排除', short: '留', active: 'bg-emerald-600 text-white shadow' },
]

const TARGET_TEXT: Record<ReviewState, string> = {
  queued: '加入待刪清單',
  kept: '標記為排除保留',
  candidate: '恢復為待審核',
}

const errMsg = (e: unknown, fallback = '操作失敗') => (e instanceof Error && e.message ? e.message : fallback)

/** 窄欄裡的數量：1234 → 1.2k，12345 → 12k */
function compactCount(n: number): string {
  if (n >= 10000) return `${Math.round(n / 1000)}k`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

function readCols(): number {
  try {
    const n = parseInt(window.localStorage.getItem(COLS_KEY) ?? '', 10)
    if (n >= 3 && n <= 8) return n
  } catch {
    /* ignore */
  }
  return 5
}

/** 群組成員（可能不在目前的審核清單裡）轉成預覽用的 ReviewItem */
function memberToItem(m: GroupMember, g: GroupInfo | null): ReviewItem {
  return {
    id: m.asset_id,
    asset_id: m.asset_id,
    filename: m.filename,
    primary_path: m.primary_path,
    media_type: m.media_type,
    size_total: m.size_total,
    rule: m.rules[0] ?? '',
    rules: m.rules,
    reason: '',
    reasons: [],
    score: m.score,
    group_id: g ? g.group_id : null,
    group_size: g ? g.members.length : null,
    is_keeper: m.is_keeper,
    state: m.state,
    thumbnail_url: m.thumbnail_url,
    width: m.width,
    height: m.height,
    taken_at: null,
  }
}

export const Review: React.FC = () => {
  /* ───────── 篩選 / 檢視 ───────── */
  const [activeState, setActiveState] = useState<ReviewState>('candidate')
  const [category, setCategory] = useState<CategoryKey>('all')
  const [sortChoice, setSortChoice] = useState<SortKey | null>(null)
  const [keepersChoice, setKeepersChoice] = useState<KeeperMode | null>(null)
  const [minScore, setMinScore] = useState(MIN_OFF)
  const [debouncedMin, setDebouncedMin] = useState(MIN_OFF)
  const [cols, setCols] = useState<number>(readCols)
  const [filtersCollapsed, , toggleFilters] = useLayoutPref(REVIEW_FILTERS_COLLAPSED_KEY)

  const sort: SortKey = sortChoice ?? defaultSort(category)
  const keepers: KeeperMode = keepersChoice ?? defaultKeepers(category)
  const effMin = isAi(category) && debouncedMin > MIN_OFF + 1e-9 ? debouncedMin : undefined

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedMin(minScore), 350)
    return () => window.clearTimeout(t)
  }, [minScore])

  /* ───────── 清單 ───────── */
  const [items, setItems] = useState<ReviewItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const epoch = useRef(0)            // 任何重新載入 / 本機變更都會 +1，讓進行中的舊請求作廢
  const inflight = useRef(false)
  const itemsRef = useRef<ReviewItem[]>([])
  const totalRef = useRef(0)
  itemsRef.current = items
  totalRef.current = total
  const hasMore = items.length < total

  const query = useMemo(
    () => ({ category, state: activeState, sort, keepers, min_score: effMin }),
    [category, activeState, sort, keepers, effMin],
  )

  const fetchFirst = useCallback(
    async (opts: { reset: boolean; limit?: number }) => {
      const my = ++epoch.current
      setLoading(true)
      setError(null)
      if (opts.reset) setItems([])
      try {
        const p = await reviewApi.page({ ...query, offset: 0, limit: opts.limit ?? PAGE })
        if (my !== epoch.current) return
        setItems(p.items)
        setTotal(p.total)
      } catch (e) {
        if (my === epoch.current) setError(errMsg(e, '無法載入候選清單'))
      } finally {
        if (my === epoch.current) setLoading(false)
      }
    },
    [query],
  )

  const loadMore = useCallback(async () => {
    if (inflight.current) return
    const have = itemsRef.current.length
    if (have >= totalRef.current) return
    inflight.current = true
    const my = epoch.current
    setLoadingMore(true)
    try {
      const p = await reviewApi.page({ ...query, offset: have, limit: PAGE })
      if (my !== epoch.current) return
      setItems((prev) => {
        const seen = new Set(prev.map((i) => i.asset_id))
        return prev.concat(p.items.filter((i) => !seen.has(i.asset_id)))
      })
      setTotal(p.items.length === 0 ? have : p.total)
    } catch (e) {
      if (my === epoch.current) setError(errMsg(e, '無法載入更多'))
    } finally {
      inflight.current = false
      setLoadingMore(false)
    }
  }, [query])

  /** 不閃爍地重新載入目前這一段（復原後使用）：取回 offset 0 起至少目前已載入的張數 */
  const refreshWindow = useCallback(
    async (extra: number) => {
      const limit = Math.min(1000, Math.max(PAGE, itemsRef.current.length + extra))
      const my = ++epoch.current
      try {
        const p = await reviewApi.page({ ...query, offset: 0, limit })
        if (my !== epoch.current) return
        setItems(p.items)
        setTotal(p.total)
      } catch (e) {
        if (my === epoch.current) setError(errMsg(e, '無法重新載入'))
      }
    },
    [query],
  )

  /* ───────── 統計 ───────── */
  const [summary, setSummary] = useState<ReviewSummary | null>(null)
  const loadSummary = useCallback(async () => {
    try {
      setSummary(await reviewApi.summary(activeState))
    } catch {
      /* 統計失敗不影響審核 */
    }
  }, [activeState])
  const summaryTimer = useRef<number | null>(null)
  const refreshSummarySoon = useCallback(() => {
    if (summaryTimer.current !== null) window.clearTimeout(summaryTimer.current)
    summaryTimer.current = window.setTimeout(() => void loadSummary(), 400)
  }, [loadSummary])
  useEffect(() => {
    void loadSummary()
  }, [loadSummary])
  useEffect(
    () => () => {
      if (summaryTimer.current !== null) window.clearTimeout(summaryTimer.current)
    },
    [],
  )

  const catStat = (k: CategoryKey) => summary?.categories.find((c) => c.key === k)
  const currentStat = catStat(category)

  /* ───────── 選取 ───────── */
  const [selection, setSelection] = useState<Selection>({ mode: 'ids', ids: new Set() })
  const lastIndex = useRef<number | null>(null)
  const clearSel = useCallback(() => {
    setSelection({ mode: 'ids', ids: new Set() })
    lastIndex.current = null
  }, [])

  const isSel = (id: number) => (selection.mode === 'ids' ? selection.ids.has(id) : !selection.excluded.has(id))
  const selectedCount = selection.mode === 'ids' ? selection.ids.size : Math.max(0, total - selection.excluded.size)
  const selectedBytes = useMemo(() => {
    if (selection.mode !== 'ids') return null
    let s = 0
    for (const i of items) if (selection.ids.has(i.asset_id)) s += i.size_total
    return s
  }, [selection, items])

  const [previewId, setPreviewId] = useState<number | null>(null)
  // 最後一張「在清單裡」的預覽相片：左右鍵、位置顯示都從這裡接著走（從群組點到清單外的成員比對時也一樣）
  const [anchorId, setAnchorId] = useState<number | null>(null)

  // 篩選條件改變：清掉選取、關閉預覽、回到頂端、重新載入第一頁
  const parentRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    clearSel()
    setPreviewId(null)
    setAnchorId(null)
    parentRef.current?.scrollTo({ top: 0 })
    void fetchFirst({ reset: true })
  }, [fetchFirst, clearSel])

  const onToggle = (item: ReviewItem, index: number, e: React.MouseEvent) => {
    if (selection.mode === 'filter') {
      const ex = new Set(selection.excluded)
      if (ex.has(item.asset_id)) ex.delete(item.asset_id)
      else ex.add(item.asset_id)
      setSelection({ mode: 'filter', excluded: ex })
      return
    }
    const ids = new Set(selection.ids)
    if (e.shiftKey && lastIndex.current !== null) {
      const [a, b] = [Math.min(lastIndex.current, index), Math.max(lastIndex.current, index)]
      for (let k = a; k <= b; k++) {
        const it = items[k]
        if (it) ids.add(it.asset_id)
      }
    } else {
      if (ids.has(item.asset_id)) ids.delete(item.asset_id)
      else ids.add(item.asset_id)
      lastIndex.current = index
    }
    setSelection({ mode: 'ids', ids })
  }

  const selectLoaded = (skipKeepers: boolean) => {
    setSelection({ mode: 'ids', ids: new Set(items.filter((i) => !(skipKeepers && i.is_keeper)).map((i) => i.asset_id)) })
  }

  const loadedNonKeepers = useMemo(() => items.filter((i) => !i.is_keeper).length, [items])
  const offerAll =
    selection.mode === 'ids' && selection.ids.size > 0 && hasMore && keepers === 'hide' && selection.ids.size >= loadedNonKeepers

  /* ───────── 訊息 / 復原 ───────── */
  const [toast, setToast] = useState<Toast | null>(null)
  const toastTimer = useRef<number | null>(null)
  const dismissToast = useCallback(() => {
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current)
    toastTimer.current = null
    setToast(null)
  }, [])
  const showToast = useCallback((t: Toast, ms = 12000) => {
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current)
    setToast(t)
    toastTimer.current = window.setTimeout(() => setToast(null), ms)
  }, [])
  useEffect(
    () => () => {
      if (toastTimer.current !== null) window.clearTimeout(toastTimer.current)
    },
    [],
  )

  const [busy, setBusy] = useState(false)
  const [dialog, setDialog] = useState<DialogState | null>(null)

  /* ───────── 大圖預覽 ───────── */
  const [previewExtra, setPreviewExtra] = useState<ReviewItem | null>(null)
  const [group, setGroup] = useState<GroupInfo | null>(null)
  const [groupLoading, setGroupLoading] = useState(false)
  const [groupVersion, setGroupVersion] = useState(0)

  const previewItem = useMemo(() => {
    if (previewId === null) return null
    return (
      items.find((i) => i.asset_id === previewId) ??
      (previewExtra && previewExtra.asset_id === previewId ? previewExtra : null)
    )
  }, [previewId, items, previewExtra])
  const anchorIndex = useMemo(() => (anchorId === null ? -1 : items.findIndex((i) => i.asset_id === anchorId)), [anchorId, items])
  const previewInList = previewItem !== null && items.some((i) => i.asset_id === previewItem.asset_id)
  const comparing = previewItem !== null && previewItem.asset_id !== anchorId

  // 目前顯示的相片不在清單了（例如換了篩選）→ 關閉
  useEffect(() => {
    if (previewId !== null && previewItem === null) setPreviewId(null)
  }, [previewId, previewItem])

  const openPreview = useCallback((item: ReviewItem) => {
    setPreviewExtra(null)
    setPreviewId(item.asset_id)
    setAnchorId(item.asset_id)
  }, [])
  const closePreview = useCallback(() => {
    setPreviewId(null)
    setAnchorId(null)
    setPreviewExtra(null)
  }, [])
  const stepPreview = (delta: number) => {
    if (anchorIndex < 0) return
    const next = items[anchorIndex + delta]
    if (!next) return
    setPreviewExtra(null)
    setPreviewId(next.asset_id)
    setAnchorId(next.asset_id)
  }
  const pickMember = (m: GroupMember) => {
    const inList = items.find((i) => i.asset_id === m.asset_id)
    if (inList) {
      setPreviewExtra(null)
      setAnchorId(inList.asset_id)
      setPreviewId(inList.asset_id)
      return
    }
    setPreviewExtra(memberToItem(m, group))
    setPreviewId(m.asset_id)
  }
  const openLast = () => {
    const list = itemsRef.current
    if (list.length === 0) return
    const it = list[Math.min(lastIndex.current ?? 0, list.length - 1)]
    if (it) openPreview(it)
  }

  // 群組成員（目前這張相片屬於某個重複群組時）
  const groupId = previewItem ? previewItem.group_id : null
  useEffect(() => {
    if (groupId === null) {
      setGroup(null)
      setGroupLoading(false)
      return
    }
    let dead = false
    setGroup((prev) => (prev && prev.group_id === groupId ? prev : null))
    setGroupLoading(true)
    reviewApi
      .group(groupId)
      .then((g) => {
        if (!dead) setGroup(g)
      })
      .catch(() => {
        if (!dead) setGroup(null)
      })
      .finally(() => {
        if (!dead) setGroupLoading(false)
      })
    return () => {
      dead = true
    }
  }, [groupId, groupVersion])

  // 快看到清單尾端時先載入下一頁
  useEffect(() => {
    if (previewId === null || anchorIndex < 0) return
    if (hasMore && !loading && !loadingMore && anchorIndex >= items.length - 4) void loadMore()
  }, [previewId, anchorIndex, items.length, hasMore, loading, loadingMore, loadMore])

  const neighborUrls = useMemo(() => {
    if (anchorIndex < 0) return []
    return [1, 2, -1]
      .map((d) => items[anchorIndex + d])
      .filter((i): i is ReviewItem => !!i)
      .map((i) => previewUrl(i.asset_id, i.thumbnail_url))
  }, [anchorIndex, items])

  /* ───────── 動作 ───────── */
  const announce = (target: ReviewState, updated: number, protectedCount: number, undo: string | null) => {
    let text = `已將 ${updated.toLocaleString()} 張${TARGET_TEXT[target]}`
    if (protectedCount > 0) text += `（🛡️ ${protectedCount} 張因「群組至少保留一張」而保留）`
    showToast({ text, tone: protectedCount > 0 ? 'warn' : 'ok', undo })
  }

  const doUndo = useCallback(
    async (token: string) => {
      setBusy(true)
      try {
        const r = await reviewApi.undo(token)
        dismissToast()
        await refreshWindow(r.restored_count)
        setGroupVersion((v) => v + 1)
        refreshSummarySoon()
        showToast({ text: `已復原 ${r.restored_count.toLocaleString()} 張`, tone: 'ok', undo: null }, 4000)
      } catch (e) {
        showToast({ text: errMsg(e, '復原失敗'), tone: 'err', undo: null }, 6000)
      } finally {
        setBusy(false)
      }
    },
    [dismissToast, refreshWindow, refreshSummarySoon, showToast],
  )

  const waiveFor = (target: ReviewState): 'screenshot' | 'document' | undefined =>
    target === 'queued' && WAIVABLE_CATEGORIES.includes(category) ? (category as 'screenshot' | 'document') : undefined

  const bulkFilter = (): BulkFilter => ({ category, state: activeState, min_score: effMin })

  /** 變更狀態並直接更新畫面；回傳「已從清單移除」的資產（被群組保護擋下的不在內） */
  const applyIds = async (ids: number[], target: ReviewState, opts?: { keepSelection?: boolean }): Promise<Set<number>> => {
    setBusy(true)
    try {
      const res = await reviewApi.setState(ids, target, waiveFor(target))
      const blocked = new Set(res.protected_asset_ids)
      const removed = new Set(ids.filter((id) => !blocked.has(id)))
      const inList = new Set(itemsRef.current.map((i) => i.asset_id))
      let removedInList = 0
      removed.forEach((id) => {
        if (inList.has(id)) removedInList++
      })
      epoch.current++
      setItems((prev) => prev.filter((i) => !removed.has(i.asset_id)))
      setTotal((t) => Math.max(0, t - removedInList))
      if (opts?.keepSelection) {
        setSelection((prev) =>
          prev.mode === 'ids' ? { mode: 'ids', ids: new Set(Array.from(prev.ids).filter((id) => !removed.has(id))) } : prev,
        )
      } else {
        clearSel()
      }
      refreshSummarySoon()
      announce(target, res.updated_count, res.protected_count, res.undo_token)
      return removed
    } catch (e) {
      showToast({ text: errMsg(e), tone: 'err', undo: null }, 8000)
      return new Set<number>()
    } finally {
      setBusy(false)
    }
  }

  /** 在預覽裡決定這一張：成功後自動跳到下一張（沒有下一張就跳到上一張，都沒有就關閉） */
  const previewAct = async (target: ReviewState) => {
    if (busy || dialog || !previewItem || anchorIndex < 0 || comparing) return
    const cur = previewItem
    const nextItem = items[anchorIndex + 1] ?? items[anchorIndex - 1] ?? null
    const removed = await applyIds([cur.asset_id], target, { keepSelection: true })
    setGroupVersion((v) => v + 1)
    if (removed.has(cur.asset_id)) {
      if (nextItem) {
        setPreviewExtra(null)
        setPreviewId(nextItem.asset_id)
        setAnchorId(nextItem.asset_id)
      } else {
        closePreview()
      }
    }
  }

  const startFilterAction = async (target: ReviewState) => {
    if (selection.mode !== 'filter') return
    setBusy(true)
    try {
      const excluded = Array.from(selection.excluded)
      const pv: BulkPreview = await reviewApi.bulkPreview(bulkFilter(), target, excluded)
      if (pv.count === 0) {
        showToast({ text: '沒有符合條件的相片', tone: 'warn', undo: null }, 4000)
        return
      }
      setDialog({
        target, count: pv.count, sizeBytes: pv.size_bytes, waived: pv.waived, mode: 'filter', ids: [], excluded,
        filter: bulkFilter(), sample: pv.sample, sampleLabel: `隨機抽樣 ${pv.sample.length} 張（共 ${pv.count.toLocaleString()} 張）`,
      })
    } catch (e) {
      showToast({ text: errMsg(e), tone: 'err', undo: null }, 8000)
    } finally {
      setBusy(false)
    }
  }

  const runAction = (target: ReviewState) => {
    if (busy || dialog || selectedCount === 0 || target === activeState) return
    if (selection.mode === 'filter') {
      void startFilterAction(target)
      return
    }
    const ids = Array.from(selection.ids)
    if (ids.length <= CONFIRM_OVER) {
      void applyIds(ids, target)
      return
    }
    const picked = items.filter((i) => selection.ids.has(i.asset_id))
    setDialog({
      target, count: ids.length, sizeBytes: selectedBytes ?? 0, waived: waiveFor(target) !== undefined, mode: 'ids', ids, excluded: [],
      filter: null, sample: picked.slice(0, 12).map((i) => ({ asset_id: i.asset_id, filename: i.filename, thumbnail_url: i.thumbnail_url })),
      sampleLabel: `已選取的前 ${Math.min(12, picked.length)} 張`,
    })
  }

  const confirmDialog = async () => {
    if (!dialog) return
    const d = dialog
    if (d.mode === 'ids') {
      setDialog(null)
      await applyIds(d.ids, d.target)
      return
    }
    if (!d.filter) return
    setBusy(true)
    try {
      const res = await reviewApi.bulkApply(d.filter, d.target, d.excluded, d.count)
      setDialog(null)
      clearSel()
      refreshSummarySoon()
      await fetchFirst({ reset: false })
      announce(d.target, res.updated_count, res.protected_count, res.undo_token)
    } catch (e) {
      setDialog(null)
      showToast({ text: errMsg(e), tone: 'err', undo: null }, 9000)
      clearSel()
      refreshSummarySoon()
      await fetchFirst({ reset: false })
    } finally {
      setBusy(false)
    }
  }

  /* ───────── 鍵盤 ───────── */
  const kb = useRef({ runAction, doUndo, toast, dialogOpen: false, previewOpen: false, selectLoaded, clearSel, openLast })
  kb.current = { runAction, doUndo, toast, dialogOpen: dialog !== null, previewOpen: previewItem !== null, selectLoaded, clearSel, openLast }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) return
      const k = kb.current
      if (k.dialogOpen) return
      const key = e.key.toLowerCase()
      // 復原在預覽開著時也要能用；其他按鍵在預覽開著時由預覽自己處理
      if ((e.ctrlKey || e.metaKey) && key === 'z') {
        if (k.toast?.undo) {
          e.preventDefault()
          void k.doUndo(k.toast.undo)
        }
        return
      }
      if (k.previewOpen) return
      if ((e.ctrlKey || e.metaKey) && key === 'a') {
        e.preventDefault()
        k.selectLoaded(true)
        return
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return
      // 焦點在按鈕 / 連結上時，空白鍵與 Enter 留給它們自己
      if ((key === ' ' || key === 'enter') && el && ['BUTTON', 'A'].includes(el.tagName)) return
      if (key === 'escape') k.clearSel()
      else if (key === ' ' || key === 'enter') {
        e.preventDefault()
        k.openLast()
      } else if (key === 'd') k.runAction('queued')
      else if (key === 'k') k.runAction('kept')
      else if (key === 'r') k.runAction('candidate')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

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

  const compact = cols >= 7
  const innerW = Math.max(240, gridW - 8)
  const cardW = (innerW - GAP * (cols - 1)) / cols
  const thumbH = Math.max(60, Math.round((cardW - CARD_PAD) * 0.75))
  const cardH = thumbH + cardMetaHeight(compact) + CARD_PAD
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
    if (hasMore && !loading && !loadingMore && lastRow >= itemRows - 3) void loadMore()
  }, [lastRow, itemRows, hasMore, loading, loadingMore, loadMore])

  // 預覽切到別張時，讓清單裡對應的卡片保持在畫面內（關閉預覽後就停在這裡）
  useEffect(() => {
    if (previewId === null || anchorIndex < 0) return
    const el = parentRef.current
    if (!el) return
    const top = Math.floor(anchorIndex / cols) * rowH
    if (top < el.scrollTop || top + cardH > el.scrollTop + el.clientHeight) {
      el.scrollTo({ top: Math.max(0, top - (el.clientHeight - cardH) / 2) })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorId])

  const changeCols = (n: number) => {
    setCols(n)
    try {
      window.localStorage.setItem(COLS_KEY, String(n))
    } catch {
      /* ignore */
    }
  }

  const pickCategory = (c: CategoryKey) => {
    setCategory(c)
    setMinScore(MIN_OFF)
    setDebouncedMin(MIN_OFF)
  }
  const pickState = (s: ReviewState) => {
    setActiveState(s)
    setMinScore(MIN_OFF)
    setDebouncedMin(MIN_OFF)
  }

  const showKeeperToggle = category === 'all' || (currentStat?.keepers ?? 0) > 0
  // 顯示 Keeper 時，清單張數比側邊欄多出來的部分就是「建議保留」的相片
  const extraKeepers = keepers === 'show' && currentStat ? Math.max(0, total - currentStat.count) : 0
  const actions = ACTIONS[activeState]
  const waivedHere = WAIVABLE_CATEGORIES.includes(category)

  /* ───────── 畫面 ───────── */
  return (
    <div className="flex h-full min-h-0 gap-4">
      {/* 左側：篩選（可收合成窄欄） */}
      {filtersCollapsed ? (
        <aside
          data-testid="review-filters"
          data-collapsed="true"
          className="flex w-14 shrink-0 flex-col items-center gap-1 overflow-y-auto"
        >
          <button
            type="button"
            onClick={toggleFilters}
            aria-label="展開篩選欄"
            title="展開篩選欄（快速鍵 [ 可同時收合 / 展開主選單）"
            className="mb-1 rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100 cursor-pointer"
          >
            <PanelLeftOpen className="h-5 w-5" />
          </button>

          {CATEGORIES.map((cat, idx) => {
            const Icon = cat.icon
            const st = catStat(cat.key)
            const prev = CATEGORIES[idx - 1]
            const active = category === cat.key
            const tip = `${cat.label}${st ? `：${st.count.toLocaleString()} 張（${formatBytes(st.size_bytes)}）` : ''}`
            return (
              <React.Fragment key={cat.key}>
                {prev && prev.section !== cat.section && <div className="my-1 w-6 border-t border-slate-800" />}
                <button
                  type="button"
                  onClick={() => pickCategory(cat.key)}
                  title={tip}
                  aria-label={tip}
                  data-category={cat.key}
                  className={`flex h-11 w-11 shrink-0 flex-col items-center justify-center gap-0.5 rounded-xl border transition-all cursor-pointer ${
                    active
                      ? 'border-sky-500/30 bg-sky-500/20 text-sky-400'
                      : 'border-transparent text-slate-400 hover:bg-slate-800/40 hover:text-slate-200'
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  <span className="font-mono text-[9px] leading-none">{st ? compactCount(st.count) : '—'}</span>
                </button>
              </React.Fragment>
            )
          })}

          <div className="my-1 w-6 border-t border-slate-800" />
          {STATE_TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => pickState(t.key)}
              title={`${t.label}：${summary ? summary.states[t.key].count.toLocaleString() : '—'} 張`}
              aria-label={t.label}
              data-state={t.key}
              className={`flex h-11 w-11 shrink-0 flex-col items-center justify-center gap-0.5 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
                activeState === t.key ? t.active : 'text-slate-400 hover:bg-slate-800/40 hover:text-slate-200'
              }`}
            >
              <span>{t.short}</span>
              <span className="font-mono text-[9px] font-normal leading-none opacity-80">
                {summary ? compactCount(summary.states[t.key].count) : '—'}
              </span>
            </button>
          ))}
        </aside>
      ) : (
        <aside
          data-testid="review-filters"
          data-collapsed="false"
          className="flex w-64 shrink-0 flex-col gap-5 overflow-y-auto pr-1"
        >
          <div>
            <div className="mb-3 flex items-center justify-between gap-2">
              <h4 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                <Filter className="h-4 w-4 text-sky-400" />
                內容與規則篩選
              </h4>
              <button
                type="button"
                onClick={toggleFilters}
                aria-label="收合篩選欄"
                title="收合篩選欄（快速鍵 [ 可同時收合 / 展開主選單）"
                className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100 cursor-pointer"
              >
                <PanelLeftClose className="h-5 w-5" />
              </button>
            </div>
            <nav className="space-y-1">
              {CATEGORIES.map((cat, idx) => {
                const Icon = cat.icon
                const st = catStat(cat.key)
                const prev = CATEGORIES[idx - 1]
                const active = category === cat.key
                return (
                  <React.Fragment key={cat.key}>
                    {prev && prev.section !== cat.section && <div className="my-2 border-t border-slate-800" />}
                    <button
                      type="button"
                      onClick={() => pickCategory(cat.key)}
                      data-category={cat.key}
                      className={`flex w-full items-center gap-2.5 rounded-xl border px-3 py-2 text-left text-xs font-medium transition-all cursor-pointer ${
                        active
                          ? 'border-sky-500/30 bg-sky-500/20 font-semibold text-sky-400'
                          : 'border-transparent text-slate-400 hover:bg-slate-800/40 hover:text-slate-200'
                      }`}
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate">{cat.label}</span>
                          {cat.tag && <span className="rounded bg-amber-500/20 px-1 text-[9px] font-bold text-amber-400">{cat.tag}</span>}
                        </span>
                        {st && st.size_bytes > 0 && <span className="block text-[10px] font-normal text-slate-500">{formatBytes(st.size_bytes)}</span>}
                      </span>
                      <span className="shrink-0 font-mono text-[11px]">{st ? st.count.toLocaleString() : '—'}</span>
                    </button>
                  </React.Fragment>
                )
              })}
            </nav>
            <p className="mt-2 px-1 text-[10px] leading-relaxed text-slate-600">
              一張相片可能同時符合多個分類，所以分類數量加總可能大於「全部」。
            </p>
          </div>

          <div>
            <h5 className="mb-2 text-xs font-semibold text-slate-400">審核狀態</h5>
            <div className="grid grid-cols-3 gap-1 rounded-xl border border-slate-800 bg-slate-900 p-1 text-xs">
              {STATE_TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => pickState(t.key)}
                  data-state={t.key}
                  className={`rounded-lg py-1.5 font-medium transition-all cursor-pointer ${activeState === t.key ? t.active : 'text-slate-400 hover:text-slate-200'}`}
                >
                  <div>{t.label}</div>
                  <div className="font-mono text-[10px] opacity-80">{summary ? summary.states[t.key].count.toLocaleString() : '—'}</div>
                </button>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 text-[11px] leading-relaxed text-slate-400">
            <div className="mb-1 flex items-center gap-1.5 font-semibold text-emerald-400">
              <ShieldCheck className="h-3.5 w-3.5" />
              安全防護
            </div>
            審核只改變資料庫狀態，不會刪除實體檔案；真正刪除要到「執行刪除」頁（預設移入資源回收筒）。
            重複群組預設至少保留一張
            {waivedHere ? '；但「螢幕截圖」與「文件」不受此限制。' : '（螢幕截圖、文件除外）。'}
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 text-[11px] leading-relaxed text-slate-500">
            <div className="mb-1 font-semibold text-slate-400">快速鍵</div>
            <div><kbd className="text-slate-300">雙擊</kbd> 卡片或 <kbd className="text-slate-300">空白鍵</kbd>：預覽大圖</div>
            <div><kbd className="text-slate-300">←</kbd> <kbd className="text-slate-300">→</kbd>：預覽中切換；<kbd className="text-slate-300">F</kbd> 放大 <kbd className="text-slate-300">I</kbd> 資訊</div>
            <div><kbd className="text-slate-300">[</kbd>：收合 / 展開所有側邊欄（專注模式）</div>
            <div><kbd className="text-slate-300">Shift</kbd>＋點選：範圍選取</div>
            <div><kbd className="text-slate-300">Ctrl</kbd>＋<kbd className="text-slate-300">A</kbd>：選取已載入（跳過 Keeper）</div>
            <div><kbd className="text-slate-300">D</kbd> 加入待刪　<kbd className="text-slate-300">K</kbd> 保留　<kbd className="text-slate-300">R</kbd> 恢復</div>
            <div><kbd className="text-slate-300">Ctrl</kbd>＋<kbd className="text-slate-300">Z</kbd>：復原　<kbd className="text-slate-300">Esc</kbd>：取消選取 / 關閉預覽</div>
          </div>
        </aside>
      )}

      {/* 右側：工作區 */}
      <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
        {/* 計數與動作 */}
        <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-slate-800 bg-slate-900 px-4 py-2.5">
          <div className="text-sm text-slate-300">
            符合 <b className="text-slate-100">{total.toLocaleString()}</b> 張
            {extraKeepers > 0 && <span className="ml-1 text-xs text-emerald-400">（含 {extraKeepers.toLocaleString()} 張建議保留）</span>}
            <span className="ml-2 text-xs text-slate-500">已載入 {items.length.toLocaleString()}</span>
          </div>
          <button
            type="button"
            onClick={() => {
              void fetchFirst({ reset: false })
              void loadSummary()
            }}
            className="rounded-lg border border-slate-700 bg-slate-800 p-1.5 text-slate-400 hover:text-slate-200 cursor-pointer"
            title="重新整理"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => selectLoaded(true)}
              disabled={items.length === 0}
              className="rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-700 disabled:opacity-40 cursor-pointer"
              title="選取目前已載入的相片（自動跳過建議保留的 Keeper）"
            >
              全選已載入
            </button>
            <button
              type="button"
              onClick={clearSel}
              disabled={selectedCount === 0}
              className="rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs font-medium text-slate-400 hover:bg-slate-700 disabled:opacity-40 cursor-pointer"
            >
              取消選取
            </button>
            <span className="mx-1 h-5 w-px bg-slate-700" />
            {actions.map((a) => (
              <button
                key={a.target}
                type="button"
                disabled={busy || selectedCount === 0}
                onClick={() => runAction(a.target)}
                className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer ${TONE_CLASS[a.tone]}`}
                title={`${a.label}（快速鍵 ${a.key}）`}
              >
                {a.target === 'queued' ? <Trash2 className="h-3.5 w-3.5" /> : a.target === 'candidate' ? <RotateCcw className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                {a.label} ({a.key})
              </button>
            ))}
            {activeState === 'queued' && (
              <Link
                to="/process"
                className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-rose-600 to-red-600 px-3.5 py-1.5 text-xs font-medium text-white shadow-md shadow-rose-600/20 hover:from-rose-500 hover:to-red-500"
              >
                前往執行刪除
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            )}
          </div>
        </div>

        {/* 檢視控制 */}
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-2xl border border-slate-800 bg-slate-900/60 px-4 py-2 text-xs text-slate-400">
          <label className="flex items-center gap-2">
            排序
            <select
              value={sort}
              onChange={(e) => setSortChoice(e.target.value as SortKey)}
              className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200 focus:border-sky-500 focus:outline-none cursor-pointer"
            >
              <option value="score">分數（高 → 低）</option>
              <option value="size">檔案大小（大 → 小）</option>
              <option value="group">重複群組</option>
              <option value="path">路徑</option>
            </select>
          </label>

          {showKeeperToggle && (
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={keepers === 'show'}
                onChange={(e) => setKeepersChoice(e.target.checked ? 'show' : 'hide')}
                className="rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500"
              />
              顯示建議保留的相片
              {currentStat && <span className="font-mono text-slate-500">({currentStat.keepers.toLocaleString()})</span>}
            </label>
          )}

          {isAi(category) && (
            <label className="flex items-center gap-2">
              分數 ≥
              <input
                type="range"
                min={MIN_OFF}
                max={1}
                step={0.01}
                value={minScore}
                onChange={(e) => setMinScore(parseFloat(e.target.value))}
                className="w-40 accent-sky-500 cursor-pointer"
              />
              <span className="w-12 font-mono text-slate-200">{minScore <= MIN_OFF + 1e-9 ? '不限' : `${Math.round(minScore * 100)}%`}</span>
            </label>
          )}

          <label className="ml-auto flex items-center gap-2">
            卡片大小
            <input
              type="range"
              min={3}
              max={8}
              step={1}
              value={11 - cols}
              onChange={(e) => changeCols(11 - parseInt(e.target.value, 10))}
              className="w-24 accent-sky-500 cursor-pointer"
            />
          </label>
        </div>

        {/* 選取列 */}
        {selectedCount > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-sky-800/60 bg-sky-950/40 px-4 py-2 text-xs text-sky-200">
            <span>
              已選 <b>{selectedCount.toLocaleString()}</b> 張
              {selectedBytes !== null && <> · 約 {formatBytes(selectedBytes)}</>}
              {selection.mode === 'filter' && <>（符合條件的全部相片，不含建議保留；點選卡片可排除個別相片）</>}
            </span>
            <span className="flex items-center gap-3">
              {offerAll && (
                <button
                  type="button"
                  onClick={() => setSelection({ mode: 'filter', excluded: new Set() })}
                  className="font-semibold underline hover:text-white cursor-pointer"
                >
                  選取符合條件的全部 {total.toLocaleString()} 張
                </button>
              )}
              <button type="button" onClick={clearSel} className="underline hover:text-white cursor-pointer">
                清除選取
              </button>
            </span>
          </div>
        )}

        {/* 卡片區 */}
        <div ref={parentRef} data-testid="review-grid" className="relative min-h-0 flex-1 overflow-y-auto rounded-2xl">
          {loading && items.length === 0 ? (
            <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-400">
              <Loader2 className="h-4 w-4 animate-spin" /> 載入相片列表中...
            </div>
          ) : error && items.length === 0 ? (
            <div className="mx-auto mt-10 flex max-w-md flex-col items-center gap-3 rounded-2xl border border-rose-800/60 bg-rose-950/30 p-6 text-center text-sm text-rose-300">
              <AlertTriangle className="h-5 w-5" />
              {error}
              <button type="button" onClick={() => void fetchFirst({ reset: true })} className="rounded-lg border border-rose-700 px-3 py-1 text-xs hover:bg-rose-900/40 cursor-pointer">
                重試
              </button>
            </div>
          ) : items.length === 0 ? (
            <div className="mx-auto mt-10 flex max-w-md flex-col items-center gap-3 rounded-2xl border border-slate-800 bg-slate-900/60 p-8 text-center">
              <div className="text-sm font-semibold text-slate-200">
                {effMin !== undefined
                  ? '沒有符合分數門檻的相片'
                  : activeState === 'candidate'
                  ? '目前此分類沒有待審核的相片'
                  : activeState === 'queued'
                  ? '待刪清單是空的'
                  : '目前沒有已排除保留的相片'}
              </div>
              <div className="text-xs text-slate-500">
                {activeState === 'candidate' ? '尚未掃描或評估時，請到「資料庫掃描」頁執行。' : '你可以在「候選」頁籤審核並標記相片。'}
              </div>
              {activeState === 'candidate' && effMin === undefined && (
                <Link to="/library" className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-500">
                  前往資料庫與分析工作台
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
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> 載入更多...
                    </div>
                  )
                }
                const start = vr.index * cols
                const rowItems = items.slice(start, start + cols)
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
                    {rowItems.map((item, k) => (
                      <ReviewCard
                        key={item.asset_id}
                        item={item}
                        index={start + k}
                        selected={isSel(item.asset_id)}
                        compact={compact}
                        thumbH={thumbH}
                        height={cardH}
                        onToggle={onToggle}
                        onPreview={openPreview}
                      />
                    ))}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </section>

      {/* 通知與復原（在預覽的上層，預覽裡按 D / K / R 後一樣看得到「復原」） */}
      {toast && (
        <div className={`fixed left-1/2 z-[60] -translate-x-1/2 ${previewItem ? 'bottom-20' : 'bottom-6'}`}>
          <div
            className={`flex items-center gap-3 rounded-xl border px-4 py-2.5 text-xs shadow-2xl ${
              toast.tone === 'ok'
                ? 'border-emerald-800/60 bg-emerald-950/90 text-emerald-200'
                : toast.tone === 'warn'
                ? 'border-amber-800/60 bg-amber-950/90 text-amber-200'
                : 'border-rose-800/60 bg-rose-950/90 text-rose-200'
            }`}
          >
            <span>{toast.text}</span>
            {toast.undo && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void doUndo(toast.undo as string)}
                className="flex items-center gap-1 rounded-md border border-current px-2 py-0.5 font-semibold hover:bg-white/10 disabled:opacity-40 cursor-pointer"
              >
                <RotateCcw className="h-3 w-3" /> 復原 (Ctrl+Z)
              </button>
            )}
            <button type="button" onClick={dismissToast} className="opacity-60 hover:opacity-100 cursor-pointer" aria-label="關閉">
              ✕
            </button>
          </div>
        </div>
      )}

      {/* 大圖預覽 */}
      {previewItem && (
        <PreviewModal
          item={previewItem}
          position={anchorIndex >= 0 ? { index: anchorIndex + 1, total } : null}
          comparing={comparing}
          hasPrev={anchorIndex > 0}
          hasNext={anchorIndex >= 0 && anchorIndex < items.length - 1}
          actions={ACTIONS[previewItem.state]}
          actionsEnabled={previewInList && !comparing}
          busy={busy}
          group={group}
          groupLoading={groupLoading}
          neighborUrls={neighborUrls}
          onClose={closePreview}
          onPrev={() => stepPreview(-1)}
          onNext={() => stepPreview(1)}
          onAction={(t) => void previewAct(t)}
          onPickMember={pickMember}
        />
      )}

      {/* 批次確認 */}
      {dialog && (
        <ConfirmDialog
          title={`確認${TARGET_TEXT[dialog.target]}：${dialog.count.toLocaleString()} 張`}
          confirmLabel={`確認${TARGET_TEXT[dialog.target]}`}
          danger={dialog.target === 'queued'}
          busy={busy}
          sample={dialog.sample}
          sampleLabel={dialog.sampleLabel}
          onConfirm={() => void confirmDialog()}
          onCancel={() => setDialog(null)}
        >
          <p>
            共 <b className="text-slate-100">{dialog.count.toLocaleString()}</b> 張，約{' '}
            <b className="text-slate-100">{formatBytes(dialog.sizeBytes)}</b>。
            {dialog.target === 'queued' && '加入待刪清單後，仍須到「執行刪除」頁才會真正處理（預設移入資源回收筒）。'}
          </p>
          {dialog.waived && (
            <p className="rounded-lg border border-amber-800/60 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">
              螢幕截圖 / 文件不受「重複群組至少保留一張」保護：同一個群組的相片可能全部被加入待刪。
            </p>
          )}
          <p className="text-xs text-slate-500">操作後可在畫面下方按「復原」還原。</p>
        </ConfirmDialog>
      )}
    </div>
  )
}
