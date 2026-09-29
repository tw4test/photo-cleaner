import React from 'react'
import { AlertCircle, FileText } from 'lucide-react'

export const History: React.FC = () => {
  return (
    <div className="p-8 space-y-8 overflow-y-auto max-w-7xl mx-auto w-full">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-bold text-slate-100">歷史記錄（History）</h2>
        <p className="text-slate-400 text-sm mt-1">
          檢視歷次清理批次記錄、受影響檔案清單與還原指引
        </p>
      </div>

      {/* Recovery Guidance Note */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-3">
        <div className="flex items-center gap-2 text-amber-400 font-semibold text-sm">
          <AlertCircle className="w-5 h-5" />
          <span>資源回收筒還原提示</span>
        </div>
        <p className="text-xs text-slate-400 leading-relaxed">
          由於系統預設使用 Windows 資源回收筒安全刪除，因 Windows API 限制無法透過網頁自動一鍵還原。若需復原檔案，請直接開啟 Windows 桌面上的「資源回收筒」，尋找對應檔名並點擊「還原」。
        </p>
      </div>

      {/* History Table Container */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
        <h3 className="text-base font-semibold text-slate-200">批次執行記錄</h3>
        <div className="text-center py-12 border border-dashed border-slate-800 rounded-xl space-y-3">
          <FileText className="w-10 h-10 text-slate-600 mx-auto" />
          <p className="text-sm text-slate-400">目前尚無任何刪除批次記錄</p>
        </div>
      </div>
    </div>
  )
}
