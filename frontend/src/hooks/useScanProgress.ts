import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../services/api'
import { ScanProgressState } from '../services/types'

const initialProgress: ScanProgressState = {
  is_scanning: false,
  stage: 'idle',
  stage_label: '閒置中',
  total_files: 0,
  processed_files: 0,
  percentage: 0.0,
  current_file: '',
  speed_fps: 0.0,
  eta_seconds: null,
  new_assets_count: 0,
  new_candidates_count: 0,
  error_message: null,
  duration_seconds: 0.0,
}

export function useScanProgress(onComplete?: (state: ScanProgressState) => void) {
  const [progress, setProgress] = useState<ScanProgressState>(initialProgress)
  const eventSourceRef = useRef<EventSource | null>(null)
  const pollIntervalRef = useRef<any>(null)
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete

  // Polling fallback function
  const pollStatus = useCallback(async () => {
    try {
      const data = await api.getScanStatus()
      setProgress(data)
      if (!data.is_scanning && data.stage === 'completed' && onCompleteRef.current) {
        onCompleteRef.current(data)
      }
    } catch (err) {
      console.error('Failed to poll scan status:', err)
    }
  }, [])

  useEffect(() => {
    // Initial fetch
    pollStatus()

    // Connect to SSE
    try {
      const es = new EventSource('/api/scan/events')
      eventSourceRef.current = es

      es.onmessage = (event) => {
        try {
          const data: ScanProgressState = JSON.parse(event.data)
          setProgress(data)
          if (!data.is_scanning && data.stage === 'completed' && onCompleteRef.current) {
            onCompleteRef.current(data)
          }
        } catch (e) {
          console.error('Failed to parse SSE data:', e)
        }
      }

      es.onerror = () => {
        es.close()
        // If SSE fails, start polling every 800ms while scanning
        if (!pollIntervalRef.current) {
          pollIntervalRef.current = setInterval(pollStatus, 800)
        }
      }
    } catch (err) {
      pollIntervalRef.current = setInterval(pollStatus, 800)
    }

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
      }
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
      }
    }
  }, [pollStatus])

  const cancel = async () => {
    try {
      await api.cancelScan()
    } catch (err) {
      console.error('Failed to cancel scan:', err)
    }
  }

  return {
    progress,
    isScanning: progress.is_scanning,
    cancelScan: cancel,
    refreshStatus: pollStatus,
  }
}
