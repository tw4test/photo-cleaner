// v0.6：流程狀態、評估、一鍵流水線 API（獨立於 api.ts，避免與既有函式衝突）

export interface StageStat {
  total: number
  pending: number
  done: number
  failed: number
  not_needed?: number
  stale?: number
}

export interface CountSize {
  count: number
  size_bytes: number
}

export interface BreakdownItem {
  key: string
  label: string
  count: number
  size_bytes: number
}

export interface EvaluateResult {
  skipped?: boolean
  added?: number
  removed?: number
  total?: number
  at?: string
}

export interface JobState {
  is_running: boolean
  job_type: string
  stage: string
  stage_label: string
  percentage: number
  pipeline_step?: string
  cancellable?: boolean
  finished_at?: number | null
  evaluate_result?: EvaluateResult
}

export interface WorkflowStatus {
  library: {
    roots: number
    roots_offline: number
    roots_never_scanned: number
    last_scan_at: string | null
    assets: {
      total: number
      photo_count: number
      raw_count: number
      video_count: number
      total_size_bytes: number
    }
    missing: number
  }
  analysis: {
    stages: { hash: StageStat; basic: StageStat; clip: StageStat; video: StageStat }
    pending_total: number
    failures: number
  }
  evaluation: {
    last_evaluated_at: string | null
    is_stale: boolean
    analysed_since: number
    last_result: EvaluateResult | null
  }
  review: { candidate: CountSize; queued: CountSize; kept: CountSize }
  breakdown: BreakdownItem[]
  breakdown_total: number
  deleted: { count: number; size_bytes: number; batches: number }
  job: JobState
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const workflowApi = {
  getStatus: () => request<WorkflowStatus>('/api/workflow/status'),
  evaluate: (force = false) =>
    request<{ status: string; message: string }>('/api/jobs/evaluate', {
      method: 'POST',
      body: JSON.stringify({ force }),
    }),
  pipeline: (mode: 'full' | 'new_only', device: 'auto' | 'cuda' | 'cpu') =>
    request<{ status: string; message: string }>('/api/jobs/pipeline', {
      method: 'POST',
      body: JSON.stringify({ mode, device }),
    }),
}

export function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1)
  return `${(bytes / Math.pow(k, i)).toFixed(i >= 2 ? 2 : 0)} ${sizes[i]}`
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  // 後端 UTC 時間沒有時區字尾：補上 Z 再轉成本地時間
  const s = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`
  const d = new Date(s)
  return isNaN(d.getTime()) ? iso : d.toLocaleString('zh-TW', { hour12: false })
}
