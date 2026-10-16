// 播放器的純邏輯（不依賴 React / DOM）：按鍵對應、時間格式、倍速、座標換算。
// 獨立成檔是為了能用 Node 直接測試（見 AGENT.md 的前端驗證方式）。

export const SEEK_STEP = 5          // ← → 快轉 / 倒轉秒數
export const SEEK_STEP_LONG = 10    // J / L
export const VOLUME_STEP = 0.05
export const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2] as const

export type PlayerAction =
  | { type: 'togglePlay' }
  | { type: 'seekBy'; seconds: number }
  | { type: 'seekPercent'; percent: number }
  | { type: 'seekStart' }
  | { type: 'volumeBy'; delta: number }
  | { type: 'toggleMute' }
  | { type: 'toggleFullscreen' }
  | { type: 'toggleTheater' }
  | { type: 'togglePip' }
  | { type: 'speedStep'; direction: 1 | -1 }
  | { type: 'prev' }
  | { type: 'next' }

export interface KeyLike {
  key: string
  shiftKey?: boolean
  ctrlKey?: boolean
  metaKey?: boolean
  altKey?: boolean
}

/**
 * 按鍵 → 播放器動作。沒有對應就回 null（交給外層，例如審核頁的 D / K / R）。
 *
 * - ← → 是快轉 / 倒轉；上一支 / 下一支是 Shift + ← → 或 PageUp / PageDown。
 * - 刻意不使用 K（YouTube 的暫停鍵）：在審核頁 K 是「排除保留」。D / R 同理不使用。
 * - 帶 Ctrl / Meta / Alt 的組合一律不處理（Ctrl+Z 復原等留給外層）。
 */
export function resolvePlayerKey(e: KeyLike): PlayerAction | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null
  const k = e.key
  switch (k) {
    case ' ':
    case 'Spacebar':
      return { type: 'togglePlay' }
    case 'ArrowLeft':
      return e.shiftKey ? { type: 'prev' } : { type: 'seekBy', seconds: -SEEK_STEP }
    case 'ArrowRight':
      return e.shiftKey ? { type: 'next' } : { type: 'seekBy', seconds: SEEK_STEP }
    case 'PageUp':
      return { type: 'prev' }
    case 'PageDown':
      return { type: 'next' }
    case 'ArrowUp':
      return { type: 'volumeBy', delta: VOLUME_STEP }
    case 'ArrowDown':
      return { type: 'volumeBy', delta: -VOLUME_STEP }
    case 'Home':
      return { type: 'seekStart' }
    case ',':
    case '<':
      return { type: 'speedStep', direction: -1 }
    case '.':
    case '>':
      return { type: 'speedStep', direction: 1 }
    default:
      break
  }
  if (k.length === 1) {
    const c = k.toLowerCase()
    if (c === 'j') return { type: 'seekBy', seconds: -SEEK_STEP_LONG }
    if (c === 'l') return { type: 'seekBy', seconds: SEEK_STEP_LONG }
    if (c === 'm') return { type: 'toggleMute' }
    if (c === 'f') return { type: 'toggleFullscreen' }
    if (c === 't') return { type: 'toggleTheater' }
    if (c === 'p') return { type: 'togglePip' }
    if (c >= '0' && c <= '9') return { type: 'seekPercent', percent: Number(c) * 10 }
  }
  return null
}

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

/** 2 → "0:02"；65 → "1:05"；3725 → "1:02:05"；無效值 → "--:--" */
export function formatClock(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !isFinite(sec) || sec < 0) return '--:--'
  const t = Math.floor(sec)
  const h = Math.floor(t / 3600)
  const m = Math.floor((t % 3600) / 60)
  const s = t % 60
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

/** 目前倍速的下一檔 / 上一檔；不在清單上（例如外部設的值）時取最接近的一檔再移動。到頭就停在頭。 */
export function stepSpeed(current: number, direction: 1 | -1): number {
  let idx = 0
  let best = Infinity
  SPEEDS.forEach((s, i) => {
    const d = Math.abs(s - current)
    if (d < best) {
      best = d
      idx = i
    }
  })
  return SPEEDS[clamp(idx + direction, 0, SPEEDS.length - 1)]
}

export const formatSpeed = (s: number) => `${s}×`

/** 滑鼠 / 觸控位置 → 0–1 的比例（拖曳時游標移出滑軌也不會超出範圍） */
export function fractionFromPointer(clientX: number, left: number, width: number): number {
  if (!(width > 0)) return 0
  return clamp((clientX - left) / width, 0, 1)
}

/** 影片的有效時長：video.duration 可能是 Infinity / NaN（某些串流），退回資料庫或 ffprobe 的值 */
export function effectiveDuration(videoDuration: number, fallback: number | null | undefined): number {
  if (isFinite(videoDuration) && videoDuration > 0) return videoDuration
  return fallback && fallback > 0 ? fallback : 0
}

export function describeMediaError(code: number | undefined): string {
  switch (code) {
    case 1: return '播放被中止'
    case 2: return '讀取影片時發生網路或磁碟錯誤（NAS 可能離線）'
    case 3: return '影片解碼失敗（檔案可能損壞，或瀏覽器不支援這個編碼）'
    case 4: return '瀏覽器無法播放這個影片格式 / 編碼'
    default: return '無法播放這個影片'
  }
}

export type PlaybackKind = 'direct' | 'try_first' | 'transcode'

/**
 * 「先試播原檔」（HEVC 等，由瀏覽器與顯示卡決定）時，偵測**沒有 error 事件的失敗**：
 * 沒有 HEVC 解碼器的瀏覽器常常能讀檔、也播得出聲音，但完全沒有畫面（videoWidth = 0、解碼的畫格數是 0），
 * 這時 error 事件不會觸發，只能自己檢查。回傳失敗說明；沒問題回 null。
 * - decodedFrames：video.getVideoPlaybackQuality().totalVideoFrames；瀏覽器不提供時傳 null（就只看 videoWidth）
 * - currentTime：要已經播出一點（> 0.4 秒）才判斷，避免剛開始載入時誤判
 */
export function diagnoseVideoElement(v: { videoWidth: number; decodedFrames: number | null; currentTime: number }, codec?: string | null): string | null {
  if (!(v.currentTime > 0.4)) return null
  const c = codec ? `（${codec}）` : ''
  if (v.videoWidth === 0) return `瀏覽器讀得到檔案，但沒有影像輸出${c}：這個瀏覽器或顯示卡不支援這個影像編碼，只播得出聲音`
  if (v.decodedFrames === 0) return `瀏覽器沒有解出任何畫面${c}：這個瀏覽器或顯示卡可能不支援這個影像編碼`
  return null
}

/** 影片列表 / 資訊欄用的小工具 */
export function formatResolution(w: number | null | undefined, h: number | null | undefined): string | null {
  return w && h ? `${w}×${h}` : null
}

/** 解析度的常見名稱：2160 → 4K、1080 → 1080p…（以短邊判斷，直式影片也一樣） */
export function resolutionLabel(w: number | null | undefined, h: number | null | undefined): string | null {
  if (!w || !h) return null
  const short = Math.min(w, h)
  const long = Math.max(w, h)
  if (short >= 2000 || long >= 3800) return '4K'
  if (short >= 1400) return '1440p'
  if (short >= 1000) return '1080p'
  if (short >= 700) return '720p'
  if (short >= 460) return '480p'
  return 'SD'
}
