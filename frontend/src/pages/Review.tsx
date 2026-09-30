import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useVirtualizer } from '@tanstack/react-virtual'
import { 
  Filter, 
  Trash2, 
  ShieldAlert, 
  Check, 
  RefreshCw,
  Sparkles,
  Smartphone,
  FileText,
  Laugh,
  EyeOff,
  Copy,
  Layers,
  Camera,
  Sun,
  RotateCcw,
  ArrowRight,
  ShieldCheck
} from 'lucide-react'
import { api } from '../services/api'
import { CandidateItem } from '../services/types'

const COLUMNS = 4

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0.00 MB'
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(2)} ${sizes[i]}`
}

function getRuleBadge(rule: string) {
  switch (rule) {
    case 'screenshot':
      return { label: '螢幕截圖', color: 'bg-sky-500/20 text-sky-400 border-sky-500/30', icon: Smartphone }
    case 'document':
      return { label: '文件/收據', color: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30', icon: FileText }
    case 'meme':
      return { label: '梗圖迷因', color: 'bg-purple-500/20 text-purple-400 border-purple-500/30', icon: Laugh }
    case 'mistouch':
      return { label: '誤拍廢片', color: 'bg-rose-500/20 text-rose-400 border-rose-500/30', icon: EyeOff }
    case 'exact_dup':
      return { label: '完全重複', color: 'bg-amber-500/20 text-amber-400 border-amber-500/30', icon: Copy }
    case 'near_dup':
      return { label: '近似重複', color: 'bg-indigo-500/20 text-indigo-400 border-indigo-500/30', icon: Layers }
    case 'blur':
      return { label: '模糊相片', color: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30', icon: Camera }
    case 'over_exposure':
    case 'under_exposure':
      return { label: '曝光不良', color: 'bg-orange-500/20 text-orange-400 border-orange-500/30', icon: Sun }
    default:
      return { label: rule, color: 'bg-slate-800 text-slate-300 border-slate-700', icon: Sparkles }
  }
}

const ReviewCardImage: React.FC<{ url: string; filename: string }> = ({ url, filename }) => {
  const [loaded, setLoaded] = useState(false)
  const [hasError, setHasError] = useState(false)

  return (
    <div className="relative w-full h-full flex items-center justify-center bg-slate-950">
      {!loaded && !hasError && (
        <div className="absolute inset-0 bg-slate-900/60 animate-pulse flex items-center justify-center">
          <Sparkles className="w-5 h-5 text-slate-700 animate-spin" />
        </div>
      )}
      {hasError ? (
        <div className="flex flex-col items-center justify-center p-2 text-center text-slate-500">
          <Camera className="w-6 h-6 text-slate-600 mb-1" />
          <span className="text-[10px] text-slate-500 font-mono">預覽不可用</span>
        </div>
      ) : (
        <img
          src={url}
          alt={filename}
          loading="lazy"
          onLoad={() => setLoaded(true)}
          onError={() => setHasError(true)}
          className={`w-full h-full object-cover group-hover:scale-105 transition-all duration-200 ${
            loaded ? 'opacity-100' : 'opacity-0'
          }`}
        />
      )}
    </div>
  )
}

export const Review: React.FC = () => {
  const navigate = useNavigate()
  const [items, setItems] = useState<CandidateItem[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [activeCategory, setActiveCategory] = useState<string>('all')
  const [activeState, setActiveState] = useState<'candidate' | 'queued' | 'kept'>('candidate')
  const [actionMessage, setActionMessage] = useState<{ text: string; type: 'success' | 'warn' } | null>(null)

  const parentRef = useRef<HTMLDivElement>(null)

  const loadCandidates = useCallback(async () => {
    try {
      setLoading(true)
      const data = await api.getCandidates({ 
        rule: activeCategory === 'all' ? undefined : activeCategory,
        state: activeState 
      })
      setItems(data)
      setSelectedIds(new Set())
    } catch (err) {
      console.error('Failed to load candidates:', err)
    } finally {
      setLoading(false)
    }
  }, [activeCategory, activeState])

  useEffect(() => {
    loadCandidates()
  }, [loadCandidates])

  // Calculate rows for TanStack Virtual
  const rowCount = Math.ceil(items.length / COLUMNS)

  const rowVirtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 320,
    overscan: 3,
  })

  const toggleSelect = (id: number) => {
    const next = new Set(selectedIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelectedIds(next)
  }

  // Select all handler
  const handleSelectAll = (skipKeepers = false) => {
    if (skipKeepers) {
      const nonKeepers = items.filter(i => !i.is_keeper).map(i => i.id)
      setSelectedIds(new Set(nonKeepers))
    } else {
      setSelectedIds(new Set(items.map(i => i.id)))
    }
  }

  const handleUpdateStatus = async (targetState: 'queued' | 'kept' | 'candidate') => {
    if (selectedIds.size === 0) return
    try {
      const idsArray = Array.from(selectedIds)
      const res: any = await api.updateBatchStatus(idsArray, targetState)
      
      let msg = ''
      let msgType: 'success' | 'warn' = 'success'

      if (targetState === 'queued') {
        msg = `已將 ${res.updated_count} 項加入待刪清單`
      } else if (targetState === 'kept') {
        msg = `已將 ${res.updated_count} 項標記為排除保留`
      } else {
        msg = `已將 ${res.updated_count} 項恢復至待審核候選`
      }

      if (res.protected_count > 0) {
        msg += `（🛡️ 觸發防呆保護：已為您保留 ${res.protected_count} 張 Keeper 避免重複組全滅）`
        msgType = 'warn'
      }

      setActionMessage({ text: msg, type: msgType })
      setTimeout(() => setActionMessage(null), 5000)
      loadCandidates()
    } catch (err) {
      console.error('Failed to batch update candidates:', err)
    }
  }

  // Keyboard shortcut listener
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ignore if in input
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement).tagName)) return

      if (e.key === 'Escape') {
        setSelectedIds(new Set())
      } else if ((e.key === 'd' || e.key === 'D') && (activeState === 'candidate' || activeState === 'kept')) {
        handleUpdateStatus('queued')
      } else if ((e.key === 'k' || e.key === 'K') && (activeState === 'candidate' || activeState === 'queued')) {
        handleUpdateStatus('kept')
      } else if ((e.key === 'r' || e.key === 'R') && (activeState === 'queued' || activeState === 'kept')) {
        handleUpdateStatus('candidate')
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [selectedIds, activeState])

  const categories = [
    { id: 'all', label: '全部候選', icon: Sparkles },
    // AI 語義分類
    { id: 'screenshot', label: '螢幕截圖', icon: Smartphone, tag: 'AI' },
    { id: 'document', label: '文件 / 收據', icon: FileText, tag: 'AI' },
    { id: 'meme', label: '梗圖 / 迷因', icon: Laugh, tag: 'AI' },
    { id: 'mistouch', label: '誤拍 / 廢片', icon: EyeOff, tag: 'AI' },
    // 傳統特徵分類
    { id: 'exact_dup', label: '完全重複', icon: Copy },
    { id: 'near_dup', label: '近似重複', icon: Layers },
    { id: 'blur', label: '模糊相片', icon: Camera },
    { id: 'exposure', label: '曝光不良', icon: Sun },
  ]

  const keeperCountInView = items.filter(i => i.is_keeper).length

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Left Filter Sidebar */}
      <aside className="w-72 border-r border-slate-800 bg-slate-900/40 p-5 space-y-6 flex flex-col shrink-0">
        <div>
          <h3 className="font-semibold text-slate-200 text-sm flex items-center gap-2">
            <Filter className="w-4 h-4 text-sky-400" />
            <span>內容與規則篩選</span>
          </h3>
          <div className="mt-3 space-y-1">
            {categories.map(cat => {
              const Icon = cat.icon
              return (
                <button
                  key={cat.id}
                  onClick={() => setActiveCategory(cat.id)}
                  className={`w-full flex items-center justify-between px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all cursor-pointer ${
                    activeCategory === cat.id
                      ? 'bg-sky-500/20 text-sky-400 border border-sky-500/30 font-semibold'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
                  }`}
                >
                  <div className="flex items-center gap-2.5">
                    <Icon className="w-4 h-4" />
                    <span>{cat.label}</span>
                  </div>
                  {cat.tag && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 border border-amber-500/30 font-mono font-bold">
                      {cat.tag}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        </div>

        {/* State Filter */}
        <div className="border-t border-slate-800 pt-4">
          <h4 className="text-xs font-semibold text-slate-400 mb-2">審核狀態切換</h4>
          <div className="grid grid-cols-3 gap-1 bg-slate-950 p-1 rounded-xl border border-slate-800 text-xs">
            <button
              onClick={() => setActiveState('candidate')}
              className={`py-1.5 rounded-lg font-medium transition-all cursor-pointer ${
                activeState === 'candidate' ? 'bg-sky-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              候選
            </button>
            <button
              onClick={() => setActiveState('queued')}
              className={`py-1.5 rounded-lg font-medium transition-all cursor-pointer ${
                activeState === 'queued' ? 'bg-rose-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              待刪
            </button>
            <button
              onClick={() => setActiveState('kept')}
              className={`py-1.5 rounded-lg font-medium transition-all cursor-pointer ${
                activeState === 'kept' ? 'bg-emerald-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              已排除
            </button>
          </div>
        </div>

        {/* Safety reminder */}
        <div className="mt-auto p-4 bg-slate-950/60 border border-slate-800 rounded-xl text-xs space-y-1.5 text-slate-400">
          <div className="font-semibold text-slate-300 flex items-center gap-1.5">
            <ShieldAlert className="w-4 h-4 text-emerald-400" />
            <span>安全防護鐵則</span>
          </div>
          <p className="leading-relaxed">
            審核操作僅變更資料庫狀態，絕不刪除實體檔案。重複組享有 Keeper 嚴格防呆保護。
          </p>
        </div>
      </aside>

      {/* Main Review Workbench */}
      <section className="flex-1 flex flex-col overflow-hidden">
        {/* Top Control Bar (Context-Aware Action Toolbar) */}
        <div className="p-4 border-b border-slate-800 bg-slate-900/30 flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-4 text-sm min-w-0">
            <span className="text-slate-400">
              篩選結果：<strong className="text-slate-100 font-mono">{items.length}</strong> 張不重複相片
            </span>
            {selectedIds.size > 0 && (
              <span className="text-sky-400 font-medium">
                已選取 <strong className="font-mono">{selectedIds.size}</strong> 張
              </span>
            )}
            {actionMessage && (
              <span className={`text-xs px-2.5 py-1 rounded-lg border animate-fade-in ${
                actionMessage.type === 'warn'
                  ? 'bg-amber-950/70 border-amber-800 text-amber-300'
                  : 'bg-emerald-950/70 border-emerald-800 text-emerald-300'
              }`}>
                {actionMessage.text}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={loadCandidates}
              className="p-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium border border-slate-700 transition-colors cursor-pointer"
              title="重新整理列表"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>

            {/* Selection Buttons */}
            {activeState === 'candidate' && keeperCountInView > 0 ? (
              <>
                <button
                  onClick={() => handleSelectAll(true)}
                  className="px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium border border-slate-700 cursor-pointer"
                  title="全選候選刪除相片（自動跳過建議保留的 Keeper）"
                >
                  全選 (跳過 Keeper)
                </button>
                <button
                  onClick={() => handleSelectAll(false)}
                  className="px-2 py-1.5 bg-slate-800/60 hover:bg-slate-700 text-slate-400 rounded-lg text-xs font-medium border border-slate-800 cursor-pointer"
                  title="全選畫面中的所有相片（包含 Keeper）"
                >
                  全部
                </button>
              </>
            ) : (
              <button
                onClick={() => handleSelectAll(false)}
                className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium border border-slate-700 cursor-pointer"
              >
                全選
              </button>
            )}

            <button
              disabled={selectedIds.size === 0}
              onClick={() => setSelectedIds(new Set())}
              className="px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-400 rounded-lg text-xs font-medium border border-slate-700 cursor-pointer"
            >
              取消選取
            </button>

            <div className="h-4 w-px bg-slate-700 mx-1" />

            {/* Context-Aware Actions for 'candidate' tab */}
            {activeState === 'candidate' && (
              <>
                <button
                  disabled={selectedIds.size === 0}
                  onClick={() => handleUpdateStatus('kept')}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-emerald-400 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-medium border border-slate-700 transition-colors cursor-pointer"
                  title="排除保留選取相片 (快速鍵: K)"
                >
                  <Check className="w-3.5 h-3.5" />
                  <span>排除保留 (K)</span>
                </button>
                <button
                  disabled={selectedIds.size === 0}
                  onClick={() => handleUpdateStatus('queued')}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 bg-rose-600 hover:bg-rose-500 text-white disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-medium shadow-md shadow-rose-600/20 transition-all cursor-pointer"
                  title="將選取相片加入待刪清單 (快速鍵: D)"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>加入待刪 (D)</span>
                </button>
              </>
            )}

            {/* Context-Aware Actions for 'queued' tab */}
            {activeState === 'queued' && (
              <>
                <button
                  disabled={selectedIds.size === 0}
                  onClick={() => handleUpdateStatus('candidate')}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-sky-400 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-medium border border-slate-700 transition-colors cursor-pointer"
                  title="移出待刪並恢復至待審核候選 (快速鍵: R)"
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                  <span>移出待刪 / 恢復候選 (R)</span>
                </button>
                <button
                  disabled={selectedIds.size === 0}
                  onClick={() => handleUpdateStatus('kept')}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-emerald-400 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-medium border border-slate-700 transition-colors cursor-pointer"
                  title="標記為排除保留 (快速鍵: K)"
                >
                  <Check className="w-3.5 h-3.5" />
                  <span>排除保留 (K)</span>
                </button>
                <button
                  onClick={() => navigate('/process')}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 bg-gradient-to-r from-rose-600 to-red-600 hover:from-rose-500 hover:to-red-500 text-white rounded-lg text-xs font-medium shadow-md shadow-rose-600/20 transition-all cursor-pointer"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>前往執行刪除</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </>
            )}

            {/* Context-Aware Actions for 'kept' tab */}
            {activeState === 'kept' && (
              <>
                <button
                  disabled={selectedIds.size === 0}
                  onClick={() => handleUpdateStatus('candidate')}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-sky-400 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-medium border border-slate-700 transition-colors cursor-pointer"
                  title="恢復至待審核候選狀態 (快速鍵: R)"
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                  <span>恢復至候選 (R)</span>
                </button>
                <button
                  disabled={selectedIds.size === 0}
                  onClick={() => handleUpdateStatus('queued')}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 bg-rose-600 hover:bg-rose-500 text-white disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-medium shadow-md shadow-rose-600/20 transition-all cursor-pointer"
                  title="將選取項目加入待刪清單 (快速鍵: D，受 Keeper 防呆保護)"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>加入待刪 (D)</span>
                </button>
              </>
            )}
          </div>
        </div>

        {/* Virtualized Grid or Empty State */}
        {loading ? (
          <div className="flex-1 flex items-center justify-center text-slate-500">
            <RefreshCw className="w-6 h-6 animate-spin mr-2" />
            <span>載入相片列表中...</span>
          </div>
        ) : items.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center p-8 text-center space-y-4">
            <div className="p-4 bg-slate-900 border border-slate-800 rounded-2xl">
              <Sparkles className="w-10 h-10 text-sky-400 mx-auto" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-slate-200">
                {activeState === 'candidate'
                  ? '目前此分類無待審核候選相片'
                  : activeState === 'queued'
                  ? '目前待刪清單為空'
                  : '目前無已排除保留的相片'}
              </h3>
              <p className="text-slate-500 text-sm mt-1">
                {activeState === 'candidate'
                  ? '若尚未掃描或評估資料夾，請至「資料庫掃描」頁面執行規則評估。'
                  : '您可在「候選」分頁審核並標記欲處理的相片。'}
              </p>
            </div>
            {activeState === 'candidate' && (
              <Link
                to="/library"
                className="flex items-center gap-2 px-5 py-2.5 bg-sky-600 hover:bg-sky-500 text-white rounded-xl text-sm font-medium transition-all shadow-lg shadow-sky-600/20"
              >
                <span>前往資料庫與分析工作台</span>
              </Link>
            )}
          </div>
        ) : (
          <div ref={parentRef} className="flex-1 overflow-y-auto p-6">
            <div
              style={{
                height: `${rowVirtualizer.getTotalSize()}px`,
                width: '100%',
                position: 'relative',
              }}
            >
              {rowVirtualizer.getVirtualItems().map((virtualRow) => {
                const startIndex = virtualRow.index * COLUMNS
                const rowItems = items.slice(startIndex, startIndex + COLUMNS)

                return (
                  <div
                    key={virtualRow.key}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      height: `${virtualRow.size}px`,
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                    className="grid grid-cols-4 gap-5 pb-5"
                  >
                    {rowItems.map((item) => {
                      const isSelected = selectedIds.has(item.id)
                      const allRules = item.rules && item.rules.length > 0 ? item.rules : [item.rule]

                      return (
                        <div
                          key={item.id}
                          onClick={() => toggleSelect(item.id)}
                          className={`group relative bg-slate-900 border rounded-2xl p-3 flex flex-col justify-between cursor-pointer transition-all ${
                            isSelected
                              ? 'border-sky-500 ring-2 ring-sky-500/50 bg-sky-950/20 shadow-lg'
                              : item.is_keeper
                              ? 'border-emerald-500/60 bg-emerald-950/15 shadow-sm shadow-emerald-500/10'
                              : 'border-slate-800 hover:border-slate-700'
                          }`}
                        >
                          {/* Top Section: Real Thumbnail Image */}
                          <div>
                            <div className="relative aspect-[4/3] bg-slate-950 rounded-xl overflow-hidden flex flex-col items-center justify-center border border-slate-800/80">
                              <ReviewCardImage
                                key={item.thumbnail_url}
                                url={item.thumbnail_url}
                                filename={item.filename}
                              />

                              {/* Media tag */}
                              <span className="absolute top-2 left-2 px-2 py-0.5 rounded-md bg-slate-900/85 backdrop-blur-sm text-[10px] font-mono font-bold text-slate-300 border border-slate-700">
                                {item.media_type.toUpperCase()}
                              </span>

                              {/* Keeper Badge */}
                              {item.is_keeper && (
                                <span className="absolute top-2 right-2 flex items-center gap-1 px-2 py-0.5 rounded-md bg-emerald-600/90 backdrop-blur-sm text-[10px] font-bold text-white shadow ring-1 ring-emerald-400/40">
                                  <ShieldCheck className="w-3 h-3" />
                                  <span>建議保留</span>
                                </span>
                              )}

                              {/* Group Tag */}
                              {item.group_id && (
                                <span className="absolute bottom-2 left-2 px-1.5 py-0.5 rounded bg-slate-900/85 backdrop-blur-sm text-[9px] font-mono text-slate-400 border border-slate-700">
                                  組 #{item.group_id}
                                </span>
                              )}

                              {/* Status Tag */}
                              {item.state !== 'candidate' && (
                                <span className={`absolute bottom-2 right-2 px-2 py-0.5 rounded-md text-[10px] font-bold shadow ${
                                  item.state === 'queued' ? 'bg-rose-600 text-white' : 'bg-emerald-600 text-white'
                                }`}>
                                  {item.state === 'queued' ? '待刪' : '已排除'}
                                </span>
                              )}
                            </div>

                            {/* Filename, Path & Size */}
                            <div className="mt-3 flex items-center justify-between text-xs gap-2">
                              <span className="font-mono text-slate-200 truncate font-semibold" title={item.primary_path}>
                                {item.filename}
                              </span>
                              <span className="text-slate-400 font-mono text-[11px] shrink-0">
                                {formatBytes(item.size_total)}
                              </span>
                            </div>
                            <div className="text-[10px] text-slate-500 font-mono truncate mt-0.5" title={item.primary_path}>
                              {item.primary_path}
                            </div>
                          </div>

                          {/* Bottom Section: Multi-Rule Badges & Reason */}
                          <div className="mt-2 pt-2 border-t border-slate-800/80 space-y-1.5">
                            {/* Badges Container */}
                            <div className="flex flex-wrap gap-1">
                              {allRules.map(r => {
                                const b = getRuleBadge(r)
                                const BIcon = b.icon
                                return (
                                  <span
                                    key={r}
                                    className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-medium ${b.color}`}
                                  >
                                    <BIcon className="w-2.5 h-2.5" />
                                    <span>{b.label}</span>
                                  </span>
                                )
                              })}
                            </div>

                            {/* Reason summary */}
                            <div className="text-slate-400 text-[11px] font-mono truncate" title={item.reason}>
                              {item.reason}
                            </div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
