// v0.6.3：審核頁用的型別與 API（獨立於 api.ts / types.ts，不修改既有檔案）
import { ApiError } from './settingsApi'

export type ReviewState = 'candidate' | 'queued' | 'kept'
export type CategoryKey =
  | 'all' | 'screenshot' | 'document' | 'meme' | 'mistouch'
  | 'exact_dup' | 'near_dup' | 'burst' | 'blur' | 'exposure' | 'video'
export type SortKey = 'score' | 'size' | 'path' | 'group'
export type KeeperMode = 'show' | 'hide' | 'only'

export interface ReviewItem {
  id: number
  asset_id: number
  filename: string
  primary_path: string
  media_type: 'photo' | 'raw' | 'video' | string
  size_total: number
  rule: string
  rules: string[]
  reason: string
  reasons: string[]
  score: number
  group_id: number | null
  group_size: number | null
  is_keeper: boolean
  state: ReviewState
  thumbnail_url: string
  width: number | null
  height: number | null
  taken_at: string | null
}

export interface ReviewPage {
  items: ReviewItem[]
  total: number
  offset: number
  limit: number
  has_more: boolean
}

export interface CategoryStat {
  key: CategoryKey
  count: number
  size_bytes: number
  keepers: number
}

export interface ReviewSummary {
  state: ReviewState
  states: Record<ReviewState, { count: number; size_bytes: number }>
  categories: CategoryStat[]
}

export interface PageQuery {
  category: CategoryKey
  state: ReviewState
  sort: SortKey
  keepers: KeeperMode
  min_score?: number
  offset: number
  limit: number
}

export interface StatusResult {
  status: string
  updated_count: number
  target_state: string
  protected_count: number
  protected_asset_ids: number[]
  updated_asset_ids?: number[]
  undo_token: string | null
}

export interface BulkFilter {
  category: CategoryKey
  state: ReviewState
  min_score?: number
}

export interface BulkSampleItem {
  asset_id: number
  filename: string
  thumbnail_url: string
  score: number
}

export interface BulkPreview {
  status: 'preview'
  count: number
  size_bytes: number
  waived: boolean
  target_state: ReviewState
  sample: BulkSampleItem[]
}

export interface BulkResult extends StatusResult {
  size_bytes: number
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
    throw new ApiError(detail, res.status)
  }
  return res.json() as Promise<T>
}

const post = <T>(url: string, body: unknown) => request<T>(url, { method: 'POST', body: JSON.stringify(body) })

export const reviewApi = {
  summary: (state: ReviewState) => request<ReviewSummary>(`/api/candidates/summary?state=${state}`),

  page: (q: PageQuery) => {
    const p = new URLSearchParams({
      category: q.category,
      state: q.state,
      sort: q.sort,
      keepers: q.keepers,
      offset: String(q.offset),
      limit: String(q.limit),
    })
    if (q.min_score !== undefined) p.set('min_score', String(q.min_score))
    return request<ReviewPage>(`/api/candidates/page?${p.toString()}`)
  },

  /** 以資產 id 變更狀態。waive：在「螢幕截圖 / 文件」分類操作時帶入，該規則的相片不受群組保護 */
  setState: (assetIds: number[], state: ReviewState, waive?: 'screenshot' | 'document') =>
    post<StatusResult>('/api/candidates/batch-status', {
      asset_ids: assetIds,
      state,
      waive_protection_for: waive ?? null,
    }),

  bulkPreview: (filter: BulkFilter, target: ReviewState, excluded: number[]) =>
    post<BulkPreview>('/api/candidates/bulk-status', {
      filter,
      target_state: target,
      exclude_asset_ids: excluded,
      dry_run: true,
    }),

  bulkApply: (filter: BulkFilter, target: ReviewState, excluded: number[], expectedCount: number) =>
    post<BulkResult>('/api/candidates/bulk-status', {
      filter,
      target_state: target,
      exclude_asset_ids: excluded,
      dry_run: false,
      expected_count: expectedCount,
    }),

  undo: (token: string) => post<{ status: string; restored_count: number }>('/api/candidates/undo', { token }),
}
