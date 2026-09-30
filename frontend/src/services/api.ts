import axios from 'axios'
import { 
  RootItem, 
  DashboardStats, 
  CandidateItem, 
  QueuedSummary, 
  DeletionResult, 
  BatchSummaryItem, 
  ActionLogItem,
  ScanProgressState,
  AnalysisStatus,
  AnalysisFailureItem
} from './types'

const apiClient = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
})

export const api = {
  // Roots
  getRoots: () => apiClient.get<RootItem[]>('/roots').then(res => res.data),
  createRoot: (data: { path: string; recursive?: boolean; exclude_patterns?: string; priority?: number }) =>
    apiClient.post<RootItem>('/roots', data).then(res => res.data),
  deleteRoot: (id: number) => apiClient.delete(`/roots/${id}`).then(res => res.data),

  // Jobs API (New split scan / analyze / evaluate)
  triggerJobScan: (mode: 'full' | 'new_only' = 'full', rootId?: number) =>
    apiClient.post('/jobs/scan', { mode, root_id: rootId }).then(res => res.data),
  triggerJobAnalyze: (stages: string[] = ['hash', 'basic', 'clip', 'video'], device: string = 'auto', retryFailed: boolean = false) =>
    apiClient.post('/jobs/analyze', { stages, device, retry_failed: retryFailed }).then(res => res.data),
  triggerRebuildThumbs: () => apiClient.post('/jobs/rebuild_thumbs').then(res => res.data),
  triggerEvaluate: () => apiClient.post('/jobs/evaluate').then(res => res.data),
  getJobStatus: () => apiClient.get<ScanProgressState>('/jobs/status').then(res => res.data),
  cancelJob: () => apiClient.post('/jobs/cancel').then(res => res.data),

  // Analysis status & failures
  getAnalysisStatus: () => apiClient.get<AnalysisStatus>('/analysis/status').then(res => res.data),
  getAnalysisFailures: (stage?: string) =>
    apiClient.get<AnalysisFailureItem[]>('/analysis/failures', { params: { stage } }).then(res => res.data),

  // Legacy Scan & Live Progress (Backward-compatible)
  triggerScan: (rootId: number) => apiClient.post(`/scan/${rootId}`).then(res => res.data),
  triggerScanAll: () => apiClient.post('/scan/all').then(res => res.data),
  getScanStatus: () => apiClient.get<ScanProgressState>('/scan/status').then(res => res.data),
  cancelScan: () => apiClient.post('/scan/cancel').then(res => res.data),

  // Dashboard
  getDashboardStats: () => apiClient.get<DashboardStats>('/dashboard/stats').then(res => res.data),

  // Candidates
  getCandidates: (params?: { rule?: string; state?: string; media_type?: string }) =>
    apiClient.get<CandidateItem[]>('/candidates', { params }).then(res => res.data),
  updateBatchStatus: (candidateIds: number[], state: 'queued' | 'kept' | 'candidate') =>
    apiClient.post('/candidates/batch-status', { candidate_ids: candidateIds, state }).then(res => res.data),
  reEvaluateRules: () => apiClient.post('/candidates/evaluate').then(res => res.data),

  // Process (Deletion)
  getQueuedSummary: () => apiClient.get<QueuedSummary>('/process/summary').then(res => res.data),
  executeDeletion: (data: { asset_ids?: number[]; mode?: string; confirmed: boolean }) =>
    apiClient.post<DeletionResult>('/process/execute', data).then(res => res.data),
  unqueueItems: (assetIds: number[], targetState: 'candidate' | 'kept' = 'candidate') =>
    apiClient.post('/process/unqueue', assetIds, { params: { target_state: targetState } }).then(res => res.data),

  // History
  getHistoryBatches: () => apiClient.get<BatchSummaryItem[]>('/history/batches').then(res => res.data),
  getBatchDetails: (batchId: string) => apiClient.get<ActionLogItem[]>(`/history/batches/${batchId}`).then(res => res.data),

  // Settings
  getSettings: () => apiClient.get<Record<string, any>>('/settings').then(res => res.data),
  updateSettings: (settings: Record<string, any>, reEvaluate: boolean = true) =>
    apiClient.put('/settings', { settings, re_evaluate: reEvaluate }).then(res => res.data),
  resetSettings: () => apiClient.post('/settings/reset').then(res => res.data),

  // System & GPU Diagnostics / Installation
  getGpuStatus: (refresh: boolean = false) =>
    apiClient.get<import('./types').GpuDiagnosticStatus>('/system/gpu-status', { params: { refresh } }).then(res => res.data),
  triggerInstallCuda: (variant: string = 'cu121') =>
    apiClient.post('/system/install-cuda', { variant }).then(res => res.data),
  cancelInstallCuda: () =>
    apiClient.post('/system/install-cuda/cancel').then(res => res.data),
}
