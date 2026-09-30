import React, { useState } from 'react'
import { FolderPlus, Play, Folder, Trash2, Info } from 'lucide-react'
import { RootItem, ScanSummary } from '../../services/types'

interface ScanPanelProps {
  roots: RootItem[]
  loading: boolean
  isScanning: boolean
  scanSummary?: ScanSummary | null
  onAddRoot: (path: string) => Promise<void>
  onDeleteRoot: (id: number) => Promise<void>
  onTriggerScan: (mode: 'full' | 'new_only', rootId?: number) => Promise<void>
}

export const ScanPanel: React.FC<ScanPanelProps> = ({
  roots,
  loading,
  isScanning,
  scanSummary,
  onAddRoot,
  onDeleteRoot,
  onTriggerScan,
}) => {
  const [newPath, setNewPath] = useState('')
  const [scanMode, setScanMode] = useState<'full' | 'new_only'>('full')

  const handleAdd = async () => {
    if (!newPath.trim()) return
    await onAddRoot(newPath.trim())
    setNewPath('')
  }

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <h3 className="text-lg font-bold text-slate-100 flex items-center gap-2">
            <Folder className="w-5 h-5 text-sky-400" />
            <span>1. 目錄管理與檔案掃描（Scan）</span>
          </h3>
          <p className="text-xs text-slate-400 mt-1">
            純目錄結構同步，快速掃描磁碟，不讀取圖片內容，不消耗 GPU 資源。
          </p>
        </div>

        {/* Scan Mode Toggle & Action */}
        <div className="flex items-center gap-3">
          <div className="flex bg-slate-950 p-1 rounded-xl border border-slate-800 text-xs">
            <button
              onClick={() => setScanMode('full')}
              className={`px-3 py-1.5 rounded-lg font-medium transition-colors ${
                scanMode === 'full'
                  ? 'bg-sky-600 text-white shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
              title="完整掃描：新增資產、比對修改、標記遺失"
            >
              完整掃描
            </button>
            <button
              onClick={() => setScanMode('new_only')}
              className={`px-3 py-1.5 rounded-lg font-medium transition-colors ${
                scanMode === 'new_only'
                  ? 'bg-sky-600 text-white shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
              title="只加入新檔案：快速增量新增，不清除已修改檔案分析結果"
            >
              只加入新檔案
            </button>
          </div>

          <button
            disabled={roots.length === 0 || isScanning}
            onClick={() => onTriggerScan(scanMode)}
            className="flex items-center gap-2 px-4 py-2 bg-sky-600 hover:bg-sky-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl text-xs font-semibold transition-all shadow-md shadow-sky-600/20 cursor-pointer"
          >
            <Play className="w-4 h-4" />
            <span>{isScanning ? '掃描進行中...' : `執行${scanMode === 'full' ? '完整' : '增量'}掃描`}</span>
          </button>
        </div>
      </div>

      {/* Scan Summary Banner (if available) */}
      {scanSummary && (
        <div className="p-4 bg-slate-950/80 border border-sky-900/50 rounded-xl space-y-2 text-xs font-mono">
          <div className="flex items-center gap-1.5 text-sky-400 font-semibold">
            <Info className="w-4 h-4" />
            <span>上次掃描結果摘要：</span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-slate-300 pt-1">
            <div className="p-2 bg-slate-900/60 rounded-lg border border-slate-800/80">
              新增資產：<strong className="text-emerald-400 text-sm">{scanSummary.added}</strong> 個
            </div>
            <div className="p-2 bg-slate-900/60 rounded-lg border border-slate-800/80">
              偵測修改：<strong className="text-amber-400 text-sm">{scanSummary.modified}</strong> 個
            </div>
            <div className="p-2 bg-slate-900/60 rounded-lg border border-slate-800/80">
              標記消失：<strong className="text-rose-400 text-sm">{scanSummary.missing}</strong> 個
            </div>
            <div className="p-2 bg-slate-900/60 rounded-lg border border-slate-800/80">
              未變更：<strong className="text-slate-400 text-sm">{scanSummary.unchanged}</strong> 個
            </div>
          </div>
        </div>
      )}

      {/* Add Path Input */}
      <div className="flex gap-3">
        <input
          type="text"
          value={newPath}
          onChange={(e) => setNewPath(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
          placeholder="請輸入本機絕對路徑，例如：D:\Photos\2023 或 D:\Pictures"
          className="flex-1 bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500 font-mono"
        />
        <button
          onClick={handleAdd}
          className="flex items-center gap-1.5 px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white rounded-xl text-xs font-medium border border-slate-700 transition-colors shadow-sm cursor-pointer"
        >
          <FolderPlus className="w-4 h-4 text-sky-400" />
          <span>加入路徑</span>
        </button>
      </div>

      {/* Path List */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-slate-400">
          <span>已登錄路徑（{roots.length} 個）</span>
          <span className="text-[11px] text-slate-500">優先序越上方在重複比對時優先保留</span>
        </div>

        {loading ? (
          <div className="text-center py-6 text-slate-500 text-xs">載入路徑中...</div>
        ) : roots.length === 0 ? (
          <div className="text-center py-8 border border-dashed border-slate-800 rounded-xl space-y-2">
            <Folder className="w-8 h-8 text-slate-600 mx-auto" />
            <p className="text-xs text-slate-400">目前尚無已登錄路徑，請在上方輸入資料夾路徑</p>
          </div>
        ) : (
          <div className="divide-y divide-slate-800/80 border border-slate-800/80 rounded-xl bg-slate-950/40 max-h-56 overflow-y-auto">
            {roots.map((root, index) => (
              <div key={root.id} className="p-3 flex items-center justify-between text-xs">
                <div className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    <span className="px-1.5 py-0.5 bg-slate-800 text-sky-400 rounded text-[10px] font-mono font-bold">
                      #{index + 1}
                    </span>
                    <span className="font-mono text-slate-200 font-medium">{root.path}</span>
                    {!root.available && (
                      <span className="px-1.5 py-0.5 bg-rose-950/80 text-rose-400 border border-rose-900 rounded text-[10px]">
                        離線/不可用
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-slate-500">
                    上次掃描：{root.last_scan_at ? new Date(root.last_scan_at).toLocaleString('zh-TW') : '尚未掃描'}
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => onTriggerScan(scanMode, root.id)}
                    disabled={isScanning || !root.available}
                    className="flex items-center gap-1 px-2.5 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-300 rounded-lg text-[11px] font-medium border border-slate-700 transition-colors cursor-pointer"
                  >
                    <Play className="w-3 h-3 text-sky-400" />
                    <span>單獨掃描</span>
                  </button>
                  <button
                    onClick={() => onDeleteRoot(root.id)}
                    disabled={isScanning}
                    className="p-1 text-slate-500 hover:text-rose-400 hover:bg-rose-950/20 disabled:opacity-40 rounded-lg transition-colors cursor-pointer"
                    title="移除路徑"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
