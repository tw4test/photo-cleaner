// 影片相關的型別與 API（v0.7.0）。獨立於 api.ts / types.ts / reviewApi.ts，不修改既有檔案。
import { ApiError } from './settingsApi'

export type VideoSortKey = 'name' | 'date' | 'size' | 'duration' | 'resolution'
export type SortOrder = 'asc' | 'desc'

export interface VideoListItem {
  asset_id: number
  filename: string
  primary_path: string
  size_total: number
  /** 秒；還沒做過 video 階段、或讀不到時是 null */
  duration: number | null
  width: number | null
  height: number | null
  codec: string | null
  /** 檔案修改時間（影片沒有 EXIF 拍攝時間） */
  modified_at: string | null
  /** unanalysed / analysed / candidate / queued / kept */
  status: string
  /** 命中的規則；沒有命中就是空陣列 */
  rules: string[]
  is_keeper: boolean
  thumbnail_url: string
}

export interface VideoListPage {
  items: VideoListItem[]
  total: number
  total_size: number
  offset: number
  limit: number
  has_more: boolean
  sort: VideoSortKey
  order: SortOrder
  q: string
}

export interface VideoSummary {
  count: number
  total_size: number
}

export interface FfmpegStatus {
  available: boolean
  ffmpeg_path: string | null
  ffprobe_available: boolean
  version: string | null
  install_hint: string | null
  cache: { bytes: number; files: number; limit_bytes: number }
}

export type TranscodeStatus = 'idle' | 'running' | 'done' | 'error' | 'cancelled'

export interface TranscodeState {
  asset_id: number
  status: TranscodeStatus
  /** 0–100；時長未知時是 null（顯示不確定進度） */
  progress: number | null
  duration: number | null
  speed: string | null
  error: string | null
  from_cache: boolean
  elapsed_seconds: number
  /** 轉好之後的串流網址（status = done 才有） */
  url?: string
}

export interface VideoInfo {
  asset_id: number
  filename: string
  size: number
  extension: string
  container: string | null
  duration: number | null
  width: number | null
  height: number | null
  video_codec: string | null
  audio_codec: string | null
  /** direct = 保證可播；try_first = HEVC 等，先試播原檔、失敗再轉檔；transcode = 一定要轉檔 */
  playback: 'direct' | 'try_first' | 'transcode'
  /** 保證可播（舊欄位）；是否一定要轉檔請看 playback */
  direct_playable: boolean
  needs_transcode: boolean
  reason: string
  ffmpeg_available: boolean
  install_hint: string | null
  stream_url: string
  transcode: TranscodeState
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
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

export interface VideoListQuery {
  q: string
  sort: VideoSortKey
  order: SortOrder
  offset: number
  limit: number
}

export const videoApi = {
  list: (q: VideoListQuery, signal?: AbortSignal) => {
    const p = new URLSearchParams({
      sort: q.sort,
      order: q.order,
      offset: String(q.offset),
      limit: String(q.limit),
    })
    if (q.q.trim()) p.set('q', q.q.trim())
    return request<VideoListPage>(`/api/videos?${p.toString()}`, { signal })
  },

  summary: () => request<VideoSummary>('/api/videos/summary'),

  ffmpeg: () => request<FfmpegStatus>('/api/videos/ffmpeg'),

  info: (assetId: number) => request<VideoInfo>(`/api/videos/${assetId}/info`),

  startTranscode: (assetId: number) =>
    request<TranscodeState>(`/api/videos/${assetId}/transcode`, { method: 'POST' }),

  transcodeState: (assetId: number) => request<TranscodeState>(`/api/videos/${assetId}/transcode`),

  cancelTranscode: (assetId: number) =>
    request<TranscodeState>(`/api/videos/${assetId}/transcode/cancel`, { method: 'POST' }),

  /**
   * 關閉播放器 / 切換影片 / 關閉分頁時的取消：fire-and-forget。
   * keepalive 讓請求在頁面卸載（pagehide）時也能送出；失敗不處理（後端結束時也會殺掉 ffmpeg）。
   */
  cancelTranscodeQuietly: (assetId: number) => {
    try {
      void fetch(`/api/videos/${assetId}/transcode/cancel`, { method: 'POST', keepalive: true }).catch(() => {})
    } catch {
      /* ignore */
    }
  },
}
