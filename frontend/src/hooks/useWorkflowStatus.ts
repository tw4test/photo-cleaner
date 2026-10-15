import { useCallback, useEffect, useRef, useState } from 'react'
import { WorkflowStatus, workflowApi } from '../services/workflow'

/**
 * 讀取 /api/workflow/status。
 * - 有任務執行中：每 2 秒更新一次（數字隨進度即時變化）
 * - 閒置：每 30 秒更新一次；任務剛結束時立即再讀一次
 */
export function useWorkflowStatus() {
  const [status, setStatus] = useState<WorkflowStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wasRunning = useRef(false)
  const alive = useRef(true)

  const reload = useCallback(async () => {
    if (timer.current) {
      clearTimeout(timer.current)
      timer.current = null
    }
    try {
      setLoading(true)
      const data = await workflowApi.getStatus()
      if (!alive.current) return
      setStatus(data)
      setError(null)
      const running = !!data.job?.is_running
      const justFinished = wasRunning.current && !running
      wasRunning.current = running
      timer.current = setTimeout(reload, running ? 2000 : justFinished ? 500 : 30000)
    } catch (e: any) {
      if (!alive.current) return
      setError(e?.message || '讀取狀態失敗')
      timer.current = setTimeout(reload, 5000)
    } finally {
      if (alive.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    alive.current = true
    reload()
    return () => {
      alive.current = false
      if (timer.current) clearTimeout(timer.current)
    }
  }, [reload])

  return { status, loading, error, reload }
}
