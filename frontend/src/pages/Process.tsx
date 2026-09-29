import React, { useState } from 'react'
import { Trash2, ShieldCheck } from 'lucide-react'

export const Process: React.FC = () => {
  const [confirmed, setConfirmed] = useState(false)

  return (
    <div className="p-8 space-y-8 overflow-y-auto max-w-7xl mx-auto w-full">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-bold text-slate-100">執行刪除（Process）</h2>
        <p className="text-slate-400 text-sm mt-1">
          系統唯一會對本機實體檔案執行刪除操作的頁面，預設移入 Windows 資源回收筒
        </p>
      </div>

      {/* Summary Box */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <h3 className="text-base font-semibold text-slate-200">待刪清單摘要</h3>
          <span className="text-xs px-3 py-1 bg-rose-500/20 text-rose-400 rounded-full border border-rose-500/30 font-medium">
            0 項待處理
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-sm">
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-xl space-y-1">
            <span className="text-slate-500 text-xs">總檔案數</span>
            <div className="text-2xl font-bold text-slate-100 font-mono">0 張</div>
          </div>
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-xl space-y-1">
            <span className="text-slate-500 text-xs">預計釋放磁碟空間</span>
            <div className="text-2xl font-bold text-emerald-400 font-mono">0.00 MB</div>
          </div>
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-xl space-y-1">
            <span className="text-slate-500 text-xs">目前刪除模式</span>
            <div className="text-lg font-bold text-sky-400 flex items-center gap-1.5 mt-1">
              <ShieldCheck className="w-5 h-5 text-sky-400" />
              <span>資源回收筒 (send2trash)</span>
            </div>
          </div>
        </div>

        {/* Action Button & Confirmation */}
        <div className="border-t border-slate-800 pt-6 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="confirm-check"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
              className="rounded border-slate-700 bg-slate-950 text-sky-500 focus:ring-sky-500"
            />
            <label htmlFor="confirm-check" className="text-xs text-slate-400 cursor-pointer">
              我已在 Review 頁確認所有選取的項目無誤，並同意執行刪除
            </label>
          </div>

          <button
            disabled={!confirmed}
            className="flex items-center gap-2 px-6 py-3 bg-rose-600 hover:bg-rose-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl text-sm font-semibold transition-all shadow-lg shadow-rose-600/20"
          >
            <Trash2 className="w-4 h-4" />
            <span>立即執行刪除</span>
          </button>
        </div>
      </div>
    </div>
  )
}
