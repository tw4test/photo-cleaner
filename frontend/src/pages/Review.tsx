import React, { useState, useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { 
  Filter, 
  Trash2, 
  ShieldAlert, 
  Check, 
  Image as ImageIcon
} from 'lucide-react'

// Generate 500 fake mock candidate items for virtual scrolling demonstration
interface MockItem {
  id: number
  filename: string
  rule: string
  reason: string
  score: number
  size: string
  isKeeper: boolean
  status: 'candidate' | 'queued' | 'kept'
  mediaType: 'photo' | 'raw' | 'video'
}

const mockCandidates: MockItem[] = Array.from({ length: 500 }, (_, i) => ({
  id: i + 1,
  filename: `IMG_${20240000 + i}.${i % 5 === 0 ? 'CR3' : i % 8 === 0 ? 'MOV' : 'JPG'}`,
  rule: i % 4 === 0 ? '完全重複' : i % 4 === 1 ? '模糊' : i % 4 === 2 ? '過曝' : '連拍多餘',
  reason: i % 4 === 0 ? 'SHA-256 完全相同' : i % 4 === 1 ? '模糊分數 32.4 < 100' : i % 4 === 2 ? '曝光值 245 > 225' : '與鄰近相片相似度 96%',
  score: Number((Math.random() * 0.9 + 0.1).toFixed(2)),
  size: `${(Math.random() * 12 + 1.5).toFixed(1)} MB`,
  isKeeper: i % 10 === 0,
  status: 'candidate',
  mediaType: i % 5 === 0 ? 'raw' : i % 8 === 0 ? 'video' : 'photo',
}))

const COLUMNS = 4

export const Review: React.FC = () => {
  const [items, setItems] = useState<MockItem[]>(mockCandidates)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [activeCategory, setActiveCategory] = useState<string>('all')

  const parentRef = useRef<HTMLDivElement>(null)

  // Filter items based on activeCategory
  const filteredItems = items.filter(item => {
    if (activeCategory === 'all') return true
    if (activeCategory === 'exact') return item.rule === '完全重複'
    if (activeCategory === 'blur') return item.rule === '模糊'
    if (activeCategory === 'exposure') return item.rule === '過曝'
    if (activeCategory === 'burst') return item.rule === '連拍多餘'
    return true
  })

  // Calculate rows for TanStack Virtual
  const rowCount = Math.ceil(filteredItems.length / COLUMNS)

  const rowVirtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 290,
    overscan: 3,
  })

  const toggleSelect = (id: number) => {
    const next = new Set(selectedIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelectedIds(next)
  }

  const markSelectedAsQueued = () => {
    setItems(prev => prev.map(item => selectedIds.has(item.id) ? { ...item, status: 'queued' } : item))
    setSelectedIds(new Set())
  }

  const markSelectedAsKept = () => {
    setItems(prev => prev.map(item => selectedIds.has(item.id) ? { ...item, status: 'kept' } : item))
    setSelectedIds(new Set())
  }

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Left Filter Sidebar */}
      <aside className="w-72 border-r border-slate-800 bg-slate-900/40 p-5 space-y-6 flex flex-col">
        <div>
          <h3 className="font-semibold text-slate-200 text-sm flex items-center gap-2">
            <Filter className="w-4 h-4 text-sky-400" />
            <span>候選分類篩選</span>
          </h3>
          <div className="mt-3 space-y-1.5">
            {[
              { id: 'all', label: '全部候選', count: items.length },
              { id: 'exact', label: '完全重複', count: items.filter(i => i.rule === '完全重複').length },
              { id: 'blur', label: '模糊相片', count: items.filter(i => i.rule === '模糊').length },
              { id: 'exposure', label: '過暗 / 過曝', count: items.filter(i => i.rule === '過曝').length },
              { id: 'burst', label: '連拍多餘', count: items.filter(i => i.rule === '連拍多餘').length },
            ].map(cat => (
              <button
                key={cat.id}
                onClick={() => setActiveCategory(cat.id)}
                className={`w-full flex items-center justify-between px-3.5 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                  activeCategory === cat.id
                    ? 'bg-sky-500/20 text-sky-400 border border-sky-500/30'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
                }`}
              >
                <span>{cat.label}</span>
                <span className="text-xs px-2 py-0.5 rounded-md bg-slate-800 font-mono text-slate-400">
                  {cat.count}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Safety reminder */}
        <div className="mt-auto p-4 bg-slate-950/60 border border-slate-800 rounded-xl text-xs space-y-1.5 text-slate-400">
          <div className="font-semibold text-slate-300 flex items-center gap-1.5">
            <ShieldAlert className="w-4 h-4 text-emerald-400" />
            <span>安全防護鐵則</span>
          </div>
          <p>Review 頁的任何動作僅變更資料庫狀態，不會刪除任何實體檔案。</p>
        </div>
      </aside>

      {/* Main Review Workbench */}
      <section className="flex-1 flex flex-col overflow-hidden">
        {/* Top Control Bar */}
        <div className="p-4 border-b border-slate-800 bg-slate-900/30 flex items-center justify-between">
          <div className="flex items-center gap-4 text-sm">
            <span className="text-slate-400">
              篩選結果：<strong className="text-slate-100 font-mono">{filteredItems.length}</strong> 項
            </span>
            {selectedIds.size > 0 && (
              <span className="text-sky-400 font-medium">
                已選取 <strong className="font-mono">{selectedIds.size}</strong> 項
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setSelectedIds(new Set(filteredItems.map(i => i.id)))}
              className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium border border-slate-700"
            >
              全選本頁
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
              onClick={markSelectedAsKept}
              className="flex items-center gap-1.5 px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-emerald-400 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-medium border border-slate-700"
            >
              <Check className="w-3.5 h-3.5" />
              <span>排除保留 (K)</span>
            </button>
            <button
              disabled={selectedIds.size === 0}
              onClick={markSelectedAsQueued}
              className="flex items-center gap-1.5 px-4 py-1.5 bg-rose-600 hover:bg-rose-500 text-white disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-medium shadow-md shadow-rose-600/20"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>加入待刪 (D)</span>
            </button>
          </div>
        </div>

        {/* Virtualized Grid */}
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
              const rowItems = filteredItems.slice(startIndex, startIndex + COLUMNS)

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
                    return (
                      <div
                        key={item.id}
                        onClick={() => toggleSelect(item.id)}
                        className={`group relative bg-slate-900 border rounded-2xl p-3 flex flex-col cursor-pointer transition-all ${
                          isSelected
                            ? 'border-sky-500 ring-2 ring-sky-500/50 bg-sky-950/20 shadow-lg'
                            : item.isKeeper
                            ? 'border-emerald-500/60 bg-emerald-950/10'
                            : 'border-slate-800 hover:border-slate-700'
                        }`}
                      >
                        {/* Mock Image Placeholder */}
                        <div className="relative aspect-[4/3] bg-slate-950 rounded-xl overflow-hidden flex flex-col items-center justify-center border border-slate-800/80">
                          <ImageIcon className="w-10 h-10 text-slate-700 group-hover:scale-110 transition-transform" />
                          <span className="text-[11px] font-mono text-slate-600 mt-2">
                            Mock 縮圖 #{item.id}
                          </span>

                          {/* Media tag */}
                          <span className="absolute top-2 left-2 px-2 py-0.5 rounded-md bg-slate-900/80 backdrop-blur-sm text-[10px] font-mono font-bold text-slate-300 border border-slate-700">
                            {item.mediaType.toUpperCase()}
                          </span>

                          {/* Keeper Badge */}
                          {item.isKeeper && (
                            <span className="absolute top-2 right-2 px-2 py-0.5 rounded-md bg-emerald-600/90 text-[10px] font-bold text-white shadow">
                              建議保留
                            </span>
                          )}

                          {/* Status Tag */}
                          {item.status !== 'candidate' && (
                            <span className={`absolute bottom-2 right-2 px-2 py-0.5 rounded-md text-[10px] font-bold ${
                              item.status === 'queued' ? 'bg-rose-600 text-white' : 'bg-emerald-600 text-white'
                            }`}>
                              {item.status === 'queued' ? '待刪' : '已排除'}
                            </span>
                          )}
                        </div>

                        {/* Card Info */}
                        <div className="mt-3 space-y-1">
                          <div className="flex items-center justify-between text-xs">
                            <span className="font-mono text-slate-200 truncate font-semibold">
                              {item.filename}
                            </span>
                            <span className="text-slate-500 font-mono text-[11px]">{item.size}</span>
                          </div>
                          <div className="flex items-center justify-between text-[11px]">
                            <span className="text-amber-400 font-medium">{item.rule}</span>
                            <span className="text-slate-500 font-mono">{item.reason}</span>
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
      </section>
    </div>
  )
}
