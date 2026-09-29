import axios from 'axios'
import { 
  RootItem, 
  DashboardStats, 
  CandidateItem, 
  QueuedSummary, 
  DeletionResult, 
  BatchSummaryItem, 
  ActionLogItem 
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

  // Scan
  triggerScan: (rootId: number) => apiClient.post(`/scan/${rootId}`).then(res => res.data),
  triggerScanAll: () => apiClient.post('/scan/all').then(res => res.data),

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
}
