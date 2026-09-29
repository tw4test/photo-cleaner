import React, { useState } from 'react'
import { FolderPlus, Play, Folder } from 'lucide-react'

export const Library: React.FC = () => {
  const [newPath, setNewPath] = useState('')

  return (
    <div className="p-8 space-y-8 overflow-y-auto max-w-7xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-100">資料庫掃描（Library）</h2>
          <p className="text-slate-400 text-sm mt-1">管理相片掃描目錄、路徑優先順序與差異掃描</p>
        </div>
        <button
          className="flex items-center gap-2 px-4 py-2 bg-sky-600 hover:bg-sky-500 text-white rounded-xl text-sm font-medium transition-all shadow-lg shadow-sky-600/20"
        >
          <Play className="w-4 h-4" />
          <span>開始全庫掃描</span>
        </button>
      </div>

      {/* Add Path Input */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
        <h3 className="text-base font-semibold text-slate-200">新增相片來源目錄</h3>
        <div className="flex gap-3">
          <input
            type="text"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            placeholder="請輸入本機絕對路徑，例如：D:\Photos\2023 或 D:\Pictures"
            className="flex-1 bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500 font-mono"
          />
          <button
            className="flex items-center gap-2 px-5 py-2.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-xl text-sm font-medium transition-colors border border-slate-700"
          >
            <FolderPlus className="w-4 h-4 text-sky-400" />
            <span>加入路徑</span>
          </button>
        </div>
      </div>

      {/* Path List */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-slate-200">已登錄掃描路徑清單</h3>
          <span className="text-xs text-slate-500">提示：越上方的路徑在重複比對時享有更高的保留優先權</span>
        </div>

        {/* Empty state */}
        <div className="text-center py-12 border border-dashed border-slate-800 rounded-xl space-y-3">
          <Folder className="w-10 h-10 text-slate-600 mx-auto" />
          <p className="text-sm text-slate-400">目前尚無已登錄路徑，請在上方輸入資料夾路徑</p>
        </div>
      </div>
    </div>
  )
}
