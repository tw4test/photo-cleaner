import React, { useState, useEffect, useRef } from 'react'
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
  Sun
} from 'lucide-react'
import { Link } from 'react-router-dom'
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

export const Review: React.FC = () => {
  const [items, setItems] = useState<CandidateItem[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [activeCategory, setActiveCategory] = useState<string>('all')
  const [activeState, setActiveState] = useState<'candidate' | 'queued' | 'kept'>('candidate')
  const [actionMessage, setActionMessage] = useState<string | null>(null)

  const parentRef = useRef<HTMLDivElement>(null)

  const loadCandidates = async () => {
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
  }

  useEffect(() => {
    loadCandidates()
  }, [activeCategory, activeState])

  // Calculate rows for TanStack Virtual
  const rowCount = Math.ceil(items.length / COLUMNS)

  const rowVirtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 310,
    overscan: 3,
  })

  const toggleSelect = (id: number) => {
    const next = new Set(selectedIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelectedIds(next)
  }

  const handleUpdateStatus = async (targetState: 'queued' | 'kept' | 'candidate') => {
    if (selectedIds.size === 0) return
    try {
      const idsArray = Array.from(selectedIds)
      const res: any = await api.updateBatchStatus(idsArray, targetState)
      
      let msg = targetState === 'queued' ? `已將 ${res.updated_count} 項加入待刪清單` : `已將 ${res.updated_count} 項標記為排除保留`
      if (res.protected_count > 0) {
        msg += `（觸發鐵則 3 保護：已為您保留 ${res.protected_count} 張 Keeper 避免重複組全滅）`
      }
      setActionMessage(msg)
      setTimeout(() => setActionMessage(null), 4000)
      loadCandidates()
    } catch (err) {
      console.error('Failed to batch update candidates:', err)
    }
  }

  const categories = [
    { id: 'all', label: '全部候選', icon: Sparkles },
    // AI 語義分類 (核心亮點)
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

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Left Filter Sidebar */}
      <aside className="w-72 border-r border-slate-800 bg-slate-900/40 p-5 space-y-6 flex flex-col">
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
                  className={`w-full flex items-center justify-between px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all ${
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
              className={`py-1.5 rounded-lg font-medium transition-all ${
                activeState === 'candidate' ? 'bg-sky-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              候選
            </button>
            <button
              onClick={() => setActiveState('queued')}
              className={`py-1.5 rounded-lg font-medium transition-all ${
                activeState === 'queued' ? 'bg-rose-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              待刪
            </button>
            <button
              onClick={() => setActiveState('kept')}
              className={`py-1.5 rounded-lg font-medium transition-all ${
                activeState === 'kept' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-slate-200'
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
          <p>Review 頁的所有審核動作僅變更資料庫狀態，絕不對實體檔案進行刪除。</p>
        </div>
      </aside>

      {/* Main Review Workbench */}
      <section className="flex-1 flex flex-col overflow-hidden">
        {/* Top Control Bar */}
        <div className="p-4 border-b border-slate-800 bg-slate-900/30 flex items-center justify-between">
          <div className="flex items-center gap-4 text-sm">
            <span className="text-slate-400">
              篩選結果：<strong className="text-slate-100 font-mono">{items.length}</strong> 項
            </span>
            {selectedIds.size > 0 && (
              <span className="text-sky-400 font-medium">
                已選取 <strong className="font-mono">{selectedIds.size}</strong> 項
              </span>
            )}
            {actionMessage && (
              <span className="text-xs px-2.5 py-1 bg-sky-950/60 border border-sky-800 text-sky-300 rounded-lg animate-fade-in">
                {actionMessage}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={loadCandidates}
              className="p-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium border border-slate-700 transition-colors"
              title="重新整理"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => setSelectedIds(new Set(items.map(i => i.id)))}
              className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium border border-slate-700"
            >
              全選
            </button>
            <button
              onClick={() => setSelectedIds(new Set())}
              className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium border border-slate-700"
            >
              取消選取
            </button>
            <div className="h-4 w-px bg-slate-700 mx-1" />
            <button
              disabled={selectedIds.size === 0}
              onClick={() => handleUpdateStatus('kept')}
              className="flex items-center gap-1.5 px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-emerald-400 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-medium border border-slate-700"
            >
              <Check className="w-3.5 h-3.5" />
              <span>排除保留 (K)</span>
            </button>
            <button
              disabled={selectedIds.size === 0}
              onClick={() => handleUpdateStatus('queued')}
              className="flex items-center gap-1.5 px-4 py-1.5 bg-rose-600 hover:bg-rose-500 text-white disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-medium shadow-md shadow-rose-600/20"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>加入待刪 (D)</span>
            </button>
          </div>
        </div>

        {/* Virtualized Grid or Empty State */}
        {loading ? (
          <div className="flex-1 flex items-center justify-center text-slate-500">
            <RefreshCw className="w-6 h-6 animate-spin mr-2" />
            <span>載入候選相片中...</span>
          </div>
        ) : items.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center p-8 text-center space-y-4">
            <div className="p-4 bg-slate-900 border border-slate-800 rounded-2xl">
              <Sparkles className="w-10 h-10 text-sky-400 mx-auto" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-slate-200">目前此分類無任何候選相片</h3>
              <p className="text-slate-500 text-sm mt-1">
                若尚未掃描資料夾，請先至「資料庫掃描」頁面加入路徑並執行掃描。
              </p>
            </div>
            <Link
              to="/library"
              className="flex items-center gap-2 px-5 py-2.5 bg-sky-600 hover:bg-sky-500 text-white rounded-xl text-sm font-medium transition-all shadow-lg shadow-sky-600/20"
            >
              <span>前往資料庫掃描</span>
            </Link>
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
                      const badge = getRuleBadge(item.rule)
                      const BadgeIcon = badge.icon

                      return (
                        <div
                          key={item.id}
                          onClick={() => toggleSelect(item.id)}
                          className={`group relative bg-slate-900 border rounded-2xl p-3 flex flex-col cursor-pointer transition-all ${
                            isSelected
                              ? 'border-sky-500 ring-2 ring-sky-500/50 bg-sky-950/20 shadow-lg'
                              : item.is_keeper
                              ? 'border-emerald-500/60 bg-emerald-950/10'
                              : 'border-slate-800 hover:border-slate-700'
                          }`}
                        >
                          {/* Real Thumbnail Image */}
                          <div className="relative aspect-[4/3] bg-slate-950 rounded-xl overflow-hidden flex flex-col items-center justify-center border border-slate-800/80">
                            <img
                              src={item.thumbnail_url}
                              alt={item.filename}
                              loading="lazy"
                              className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                              onError={(e) => {
                                (e.target as HTMLElement).style.display = 'none'
                              }}
                            />

                            {/* Media tag */}
                            <span className="absolute top-2 left-2 px-2 py-0.5 rounded-md bg-slate-900/80 backdrop-blur-sm text-[10px] font-mono font-bold text-slate-300 border border-slate-700">
                              {item.media_type.toUpperCase()}
                            </span>

                            {/* Keeper Badge */}
                            {item.is_keeper && (
                              <span className="absolute top-2 right-2 px-2 py-0.5 rounded-md bg-emerald-600/90 text-[10px] font-bold text-white shadow">
                                建議保留
                              </span>
                            )}

                            {/* Group Tag */}
                            {item.group_id && (
                              <span className="absolute bottom-2 left-2 px-1.5 py-0.5 rounded bg-slate-900/80 backdrop-blur-sm text-[9px] font-mono text-slate-400 border border-slate-700">
                                組 #{item.group_id}
                              </span>
                            )}

                            {/* Status Tag */}
                            {item.state !== 'candidate' && (
                              <span className={`absolute bottom-2 right-2 px-2 py-0.5 rounded-md text-[10px] font-bold ${
                                item.state === 'queued' ? 'bg-rose-600 text-white' : 'bg-emerald-600 text-white'
                              }`}>
                                {item.state === 'queued' ? '待刪' : '已排除'}
                              </span>
                            )}
                          </div>

                          {/* Card Info */}
                          <div className="mt-3 space-y-1">
                            <div className="flex items-center justify-between text-xs">
                              <span className="font-mono text-slate-200 truncate font-semibold" title={item.filename}>
                                {item.filename}
                              </span>
                              <span className="text-slate-500 font-mono text-[11px]">
                                {formatBytes(item.size_total)}
                              </span>
                            </div>

                            <div className="flex items-center justify-between text-[11px]">
                              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[10px] font-medium ${badge.color}`}>
                                <BadgeIcon className="w-3 h-3" />
                                <span>{badge.label}</span>
                              </span>
                              <span className="text-slate-500 font-mono truncate max-w-[130px]" title={item.reason}>
                                {item.reason}
                              </span>
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
