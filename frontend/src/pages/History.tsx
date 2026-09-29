import React, { useState, useEffect } from 'react'
import { 
  AlertCircle, 
  FileText, 
  RefreshCw, 
  ChevronDown, 
  ChevronRight, 
  Clock 
} from 'lucide-react'
import { api } from '../services/api'
import { BatchSummaryItem, ActionLogItem } from '../services/types'

export const History: React.FC = () => {
  const [batches, setBatches] = useState<BatchSummaryItem[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [expandedBatchId, setExpandedBatchId] = useState<string | null>(null)
  const [batchLogs, setBatchLogs] = useState<Record<string, ActionLogItem[]>>({})
  const [loadingDetails, setLoadingDetails] = useState<boolean>(false)

  const loadBatches = async () => {
    try {
      setLoading(true)
      const data = await api.getHistoryBatches()
      setBatches(data)
    } catch (err) {
      console.error('Failed to load history batches:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadBatches()
  }, [])

  const toggleExpand = async (batchId: string) => {
    if (expandedBatchId === batchId) {
      setExpandedBatchId(null)
      return
    }

    setExpandedBatchId(batchId)
    if (!batchLogs[batchId]) {
      try {
        setLoadingDetails(true)
        const logs = await api.getBatchDetails(batchId)
        setBatchLogs(prev => ({ ...prev, [batchId]: logs }))
      } catch (err) {
        console.error('Failed to load batch logs:', err)
      } finally {
        setLoadingDetails(false)
      }
    }
  }

  return (
    <div className="p-8 space-y-8 overflow-y-auto max-w-7xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-100">歷史記錄（History）</h2>
          <p className="text-slate-400 text-sm mt-1">
            檢視歷次清理批次記錄、受影響檔案清單與還原指引
          </p>
        </div>
        <button
          onClick={loadBatches}
          className="flex items-center gap-2 px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-medium border border-slate-700 transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          <span>重新整理日誌</span>
        </button>
      </div>

      {/* Recovery Guidance Note */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-3">
        <div className="flex items-center gap-2 text-amber-400 font-semibold text-sm">
          <AlertCircle className="w-5 h-5" />
          <span>資源回收筒還原提示</span>
        </div>
        <p className="text-xs text-slate-400 leading-relaxed">
          由於系統預設使用 Windows 資源回收筒安全刪除，因 Windows API 限制無法透過網頁自動一鍵還原。若需復原檔案，請直接開啟 Windows 桌面上的「資源回收筒」，尋找下方記錄中的檔案名稱並點擊「還原」。
        </p>
      </div>

      {/* History Table Container */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
        <h3 className="text-base font-semibold text-slate-200">批次執行記錄清單</h3>
        {loading ? (
          <div className="text-center py-12 text-slate-500">載入歷史記錄中...</div>
        ) : batches.length === 0 ? (
          <div className="text-center py-12 border border-dashed border-slate-800 rounded-xl space-y-3">
            <FileText className="w-10 h-10 text-slate-600 mx-auto" />
            <p className="text-sm text-slate-400">目前尚無任何刪除批次記錄</p>
          </div>
        ) : (
          <div className="space-y-3">
            {batches.map((batch) => {
              const isExpanded = expandedBatchId === batch.batch_id
              const logs = batchLogs[batch.batch_id] || []

              return (
                <div key={batch.batch_id} className="border border-slate-800 rounded-xl bg-slate-950/40 overflow-hidden">
                  <div
                    onClick={() => toggleExpand(batch.batch_id)}
                    className="p-4 flex items-center justify-between cursor-pointer hover:bg-slate-800/40 transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      {isExpanded ? (
                        <ChevronDown className="w-4 h-4 text-sky-400" />
                      ) : (
                        <ChevronRight className="w-4 h-4 text-slate-500" />
                      )}
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs px-2 py-0.5 bg-slate-800 text-slate-200 rounded font-bold">
                            批次 #{batch.batch_id}
                          </span>
                          <span className="text-xs px-2 py-0.5 bg-emerald-950 text-emerald-400 border border-emerald-800 rounded font-medium">
                            {batch.mode === 'trash' ? '資源回收筒' : batch.mode}
                          </span>
                        </div>
                        <div className="text-xs text-slate-500 mt-1 flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          <span>{new Date(batch.at).toLocaleString('zh-TW')}</span>
                        </div>
                      </div>
                    </div>

                    <div className="text-xs text-slate-300 font-mono">
                      已處理 <strong className="text-slate-100">{batch.total_files}</strong> 個檔案
                    </div>
                  </div>

                  {/* Expanded File Details */}
                  {isExpanded && (
                    <div className="border-t border-slate-800 bg-slate-900/60 p-4 space-y-2">
                      <div className="text-xs font-semibold text-slate-400 mb-2">本批次處理檔案原路徑：</div>
                      {loadingDetails && !logs.length ? (
                        <div className="text-xs text-slate-500 py-2">載入細項中...</div>
                      ) : (
                        <div className="space-y-1.5 max-h-60 overflow-y-auto">
                          {logs.map((log) => (
                            <div key={log.id} className="text-xs font-mono p-2 bg-slate-950/80 rounded border border-slate-800/80 text-slate-300 flex items-center justify-between">
                              <span className="truncate max-w-2xl">{log.original_path}</span>
                              <span className="text-[10px] text-slate-500 shrink-0 ml-2">
                                {new Date(log.at).toLocaleTimeString('zh-TW')}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
