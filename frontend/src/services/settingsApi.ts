// v0.6.1：設定頁用的型別與 API（獨立於 api.ts / types.ts，不修改既有檔案）
// 預設值、範圍、選項一律由後端 /api/settings/meta 提供（單一來源：backend/app/core/defaults.py）。

export type NearDupMethod = 'clip' | 'phash' | 'both' | 'either'

export interface AppSettings {
  // 規則開關
  rule_exact_dup_enabled: boolean
  rule_near_dup_enabled: boolean
  rule_burst_enabled: boolean
  rule_blur_enabled: boolean
  rule_exposure_enabled: boolean
  rule_video_same_size_enabled: boolean
  rule_video_tiny_enabled: boolean
  rule_screenshot_enabled: boolean
  rule_document_enabled: boolean
  rule_meme_enabled: boolean
  rule_mistouch_enabled: boolean
  // AI 分類門檻
  screenshot_threshold: number
  document_threshold: number
  meme_threshold: number
  mistouch_threshold: number
  // 近似重複 / 連拍
  near_dup_method: NearDupMethod
  clip_dup_threshold: number
  phash_threshold: number
  near_dup_max_group: number
  burst_time_window_seconds: number
  burst_similarity_threshold: number
  // 畫質
  blur_threshold: number
  under_exposure_threshold: number
  over_exposure_threshold: number
  video_tiny_threshold_kb: number
  // 保留 / 刪除（設定頁不顯示，使用預設值）
  keeper_strategy: string
  cross_path_duplicates: boolean
  deletion_mode: string
  auto_evaluate_on_save: boolean
  // CLIP 模型與運算
  clip_model: string
  clip_pretrained: string
  compute_device: string
  clip_batch_size: number
  // 並行 Runner
  hash_runners: number
  basic_runners: number
  clip_runners: number
  video_runners: number
}

export type SettingKey = keyof AppSettings
export type NumKey = { [K in SettingKey]: AppSettings[K] extends number ? K : never }[SettingKey]
export type BoolKey = { [K in SettingKey]: AppSettings[K] extends boolean ? K : never }[SettingKey]

export interface SettingsMeta {
  defaults: AppSettings
  ranges: Record<string, [number, number]>
  choices: Record<string, string[]>
  /** 只改這些設定不需要重新評估（效能類）；其餘設定都會影響評估結果 */
  non_evaluation_keys: string[]
}

export interface SaveResult {
  status: string
  message: string
  changed: string[]
  evaluated: boolean
}

export interface ResetResult {
  status: string
  message: string
  evaluated: boolean
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
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

export const settingsApi = {
  get: () => request<AppSettings>('/api/settings'),
  getMeta: () => request<SettingsMeta>('/api/settings/meta'),
  /** 只送出有變更的項目；reEvaluate=true 時，後端只在變更了會影響評估的設定時才會評估 */
  save: (changes: Partial<AppSettings>, reEvaluate: boolean) =>
    request<SaveResult>('/api/settings', {
      method: 'PUT',
      body: JSON.stringify({ settings: changes, re_evaluate: reEvaluate }),
    }),
  reset: () => request<ResetResult>('/api/settings/reset', { method: 'POST' }),
}
