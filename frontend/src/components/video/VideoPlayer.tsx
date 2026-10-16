import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import {
  AlertTriangle,
  Film,
  Loader2,
  Maximize,
  Minimize,
  Pause,
  PictureInPicture2,
  Play,
  RectangleHorizontal,
  SkipBack,
  SkipForward,
  Volume1,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react'
import { TranscodeState, VideoInfo, videoApi } from '../../services/videoApi'
import {
  PlayerAction,
  SPEEDS,
  clamp,
  describeMediaError,
  diagnoseVideoElement,
  effectiveDuration,
  formatClock,
  formatSpeed,
  fractionFromPointer,
  resolvePlayerKey,
  stepSpeed,
} from './videoPlayerLogic'

/* ────────────────────────────────────────────────────────────
 * 影片播放器（v0.7.0，VD-10 至 VD-19）
 * - 原檔用 HTTP Range 串流（<video src> 由瀏覽器自己送 Range，拖曳進度條不必下載整支）
 * - 播放 / 暫停、音量、倍速、劇院模式、子母畫面、全螢幕、鍵盤（見 videoPlayerLogic.resolvePlayerKey）
 * - 瀏覽器播不了的格式（AVI / WMV / FLV / 部分 MKV / HEVC）：使用者按「轉檔後播放」才會用 ffmpeg 轉成 H.264 MP4，
 *   有進度、可取消；關閉播放器、換下一支、關閉分頁都會自動取消轉檔。原檔不會被修改。
 * - ffmpeg 是選用的：沒有安裝時顯示安裝說明，其他功能不受影響。
 * - 鍵盤由外層（VideoViewer）呼叫 handle.handleKey(e)，這樣審核頁的 D / K / R 可以排在前面處理。
 * ──────────────────────────────────────────────────────────── */

export interface VideoPlayerHandle {
  /** 處理一個鍵盤事件；回傳 true = 已處理（呼叫端不要再處理） */
  handleKey: (e: KeyboardEvent) => boolean
}

interface Props {
  assetId: number
  poster?: string
  autoPlay?: boolean
  theater: boolean
  onToggleTheater: () => void
  onPrev?: () => void
  onNext?: () => void
  /** 影片資訊讀到（或換片時重設為 null）時通知外層，資訊欄用 */
  onInfo?: (info: VideoInfo | null) => void
}

const VOLUME_KEY = 'pc.video.volume'
const MUTED_KEY = 'pc.video.muted'
const RATE_KEY = 'pc.video.rate'
const HIDE_CONTROLS_MS = 2500
const POLL_MS = 800
const MAX_POLL_FAILURES = 5

function readNum(key: string, fallback: number, lo: number, hi: number): number {
  try {
    const n = parseFloat(window.localStorage.getItem(key) ?? '')
    if (isFinite(n)) return clamp(n, lo, hi)
  } catch {
    /* ignore */
  }
  return fallback
}

function readBool(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function writePref(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* ignore */
  }
}

const errText = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback)

function localTc(assetId: number, status: TranscodeState['status'], error: string | null = null): TranscodeState {
  return {
    asset_id: assetId, status, progress: status === 'running' ? 0 : null, duration: null, speed: null,
    error, from_cache: false, elapsed_seconds: 0,
  }
}

interface Source {
  kind: 'original' | 'transcoded'
  url: string
}

/* ───────── 滑軌（進度條 / 音量）共用的拖曳處理 ───────── */
function useDrag(onFraction: (f: number, final: boolean) => void) {
  const dragging = useRef(false)
  const live = useRef(onFraction)
  live.current = onFraction
  return {
    onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return
      e.currentTarget.setPointerCapture(e.pointerId)
      dragging.current = true
      const r = e.currentTarget.getBoundingClientRect()
      live.current(fractionFromPointer(e.clientX, r.left, r.width), false)
    },
    onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging.current) return
      const r = e.currentTarget.getBoundingClientRect()
      live.current(fractionFromPointer(e.clientX, r.left, r.width), false)
    },
    onPointerUp: (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging.current) return
      dragging.current = false
      try {
        e.currentTarget.releasePointerCapture(e.pointerId)
      } catch {
        /* ignore */
      }
      const r = e.currentTarget.getBoundingClientRect()
      live.current(fractionFromPointer(e.clientX, r.left, r.width), true)
    },
  }
}

const SeekBar: React.FC<{
  current: number
  duration: number
  bufferedEnd: number
  onSeek: (seconds: number) => void
}> = ({ current, duration, bufferedEnd, onSeek }) => {
  const [dragFrac, setDragFrac] = useState<number | null>(null)
  const [hover, setHover] = useState<{ frac: number } | null>(null)
  const drag = useDrag((f, final) => {
    if (duration <= 0) return
    if (final) {
      setDragFrac(null)
      onSeek(f * duration)
    } else {
      setDragFrac(f)
    }
  })
  const frac = dragFrac ?? (duration > 0 ? clamp(current / duration, 0, 1) : 0)
  const bufFrac = duration > 0 ? clamp(bufferedEnd / duration, 0, 1) : 0
  return (
    <div
      role="slider"
      aria-label="播放進度"
      aria-valuemin={0}
      aria-valuemax={Math.round(duration)}
      aria-valuenow={Math.round(frac * duration)}
      data-testid="video-seek"
      className="group/seek relative flex h-5 cursor-pointer touch-none items-center"
      {...drag}
      onPointerMove={(e) => {
        drag.onPointerMove(e)
        const r = e.currentTarget.getBoundingClientRect()
        setHover({ frac: fractionFromPointer(e.clientX, r.left, r.width) })
      }}
      onPointerLeave={() => setHover(null)}
    >
      <div className="relative h-1 w-full rounded-full bg-white/25 transition-all group-hover/seek:h-1.5">
        <div className="absolute inset-y-0 left-0 rounded-full bg-white/30" style={{ width: `${bufFrac * 100}%` }} />
        <div className="absolute inset-y-0 left-0 rounded-full bg-sky-500" style={{ width: `${frac * 100}%` }} />
        <div
          className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-sky-400 opacity-0 shadow group-hover/seek:opacity-100"
          style={{ left: `${frac * 100}%`, opacity: dragFrac !== null ? 1 : undefined }}
        />
      </div>
      {hover && duration > 0 && (
        <div
          className="pointer-events-none absolute bottom-5 -translate-x-1/2 rounded bg-black/80 px-1.5 py-0.5 font-mono text-[11px] text-slate-100"
          style={{ left: `${hover.frac * 100}%` }}
        >
          {formatClock(hover.frac * duration)}
        </div>
      )}
    </div>
  )
}

const VolumeSlider: React.FC<{ volume: number; muted: boolean; onChange: (v: number) => void }> = ({ volume, muted, onChange }) => {
  const drag = useDrag((f) => onChange(f))
  const shown = muted ? 0 : volume
  return (
    <div
      role="slider"
      aria-label="音量"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(shown * 100)}
      className="group/vol relative flex h-5 w-20 cursor-pointer touch-none items-center"
      {...drag}
    >
      <div className="relative h-1 w-full rounded-full bg-white/25">
        <div className="absolute inset-y-0 left-0 rounded-full bg-white" style={{ width: `${shown * 100}%` }} />
        <div className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white shadow" style={{ left: `${shown * 100}%` }} />
      </div>
    </div>
  )
}

/** 控制列上的按鈕：不接收焦點（否則按完按鈕後空白鍵會再按一次同一個按鈕） */
const CtrlBtn: React.FC<{
  label: string
  onClick: () => void
  active?: boolean
  disabled?: boolean
  testId?: string
  children: React.ReactNode
}> = ({ label, onClick, active, disabled, testId, children }) => (
  <button
    type="button"
    tabIndex={-1}
    title={label}
    aria-label={label}
    aria-pressed={active}
    disabled={disabled}
    data-testid={testId}
    onMouseDown={(e) => e.preventDefault()}
    onClick={onClick}
    className={`flex h-8 min-w-8 items-center justify-center gap-1 rounded-md px-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-30 cursor-pointer ${
      active ? 'bg-sky-500/30 text-sky-200' : 'text-slate-100 hover:bg-white/15'
    }`}
  >
    {children}
  </button>
)

export const VideoPlayer = forwardRef<VideoPlayerHandle, Props>(function VideoPlayer(
  { assetId, poster, autoPlay = true, theater, onToggleTheater, onPrev, onNext, onInfo },
  ref,
) {
  const stageRef = useRef<HTMLDivElement | null>(null)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [videoEl, setVideoEl] = useState<HTMLVideoElement | null>(null)
  const attachVideo = useCallback((el: HTMLVideoElement | null) => {
    videoRef.current = el
    setVideoEl(el)
  }, [])

  // 資訊與播放來源
  const [info, setInfo] = useState<VideoInfo | null>(null)
  const [infoError, setInfoError] = useState<string | null>(null)
  const [source, setSource] = useState<Source | null>(null)
  const [tc, setTc] = useState<TranscodeState | null>(null)
  const [mediaError, setMediaError] = useState<string | null>(null)

  // 播放狀態
  const [playing, setPlaying] = useState(false)
  const [buffering, setBuffering] = useState(false)
  const [current, setCurrent] = useState(0)
  const [mediaDuration, setMediaDuration] = useState(0)
  const [bufferedEnd, setBufferedEnd] = useState(0)
  const [volume, setVolume] = useState(() => readNum(VOLUME_KEY, 1, 0, 1))
  const [muted, setMuted] = useState(() => readBool(MUTED_KEY))
  const [rate, setRate] = useState(() => readNum(RATE_KEY, 1, 0.25, 2))
  const [fullscreen, setFullscreen] = useState(false)
  const [pip, setPip] = useState(false)
  const [controlsVisible, setControlsVisible] = useState(true)
  const [speedMenu, setSpeedMenu] = useState(false)
  const [toast, setToast] = useState<{ text: string; id: number } | null>(null)

  const epochRef = useRef(0)                       // 換片 / 卸載時 +1，讓進行中的非同步結果作廢
  const ownRef = useRef<number | null>(null)       // 本播放器正在跑（或接手）的轉檔所屬資產；非 null 時關閉要取消
  const pollRef = useRef<number | undefined>(undefined)
  const pollFailures = useRef(0)
  const hideRef = useRef<number | undefined>(undefined)
  const toastRef = useRef<number | undefined>(undefined)
  const clickRef = useRef<number | undefined>(undefined)
  const overControls = useRef(false)
  const lastFsExit = useRef(0)
  const onInfoRef = useRef(onInfo)
  onInfoRef.current = onInfo

  // 「先試播原檔」中：失敗要自己偵測（沒有 HEVC 解碼器的瀏覽器常常只播出聲音、不觸發 error）
  const trying = info?.playback === 'try_first' && source?.kind === 'original'
  const pipSupported = typeof document !== 'undefined' && !!document.pictureInPictureEnabled
  const duration = effectiveDuration(mediaDuration, info?.duration)
  const ready = source !== null && !mediaError

  /* ───────── 轉檔 ───────── */
  // 輪詢要呼叫自己：用 ref 持有最新的實作（useCallback 直接遞迴會讓 TypeScript 推不出型別）
  const applyTcRef = useRef<(s: TranscodeState, id: number, epoch: number) => void>(() => {})
  applyTcRef.current = (s, id, epoch) => {
    if (epoch !== epochRef.current) return
    setTc(s)
    window.clearTimeout(pollRef.current)
    if (s.status === 'running') {
      ownRef.current = id
      pollRef.current = window.setTimeout(async () => {
        try {
          const next = await videoApi.transcodeState(id)
          pollFailures.current = 0
          applyTcRef.current(next, id, epoch)
        } catch (e) {
          if (epoch !== epochRef.current) return
          pollFailures.current += 1
          if (pollFailures.current >= MAX_POLL_FAILURES) {
            ownRef.current = null
            setTc(localTc(id, 'error', errText(e, '無法取得轉檔進度')))
          } else {
            applyTcRef.current(s, id, epoch)       // 暫時失敗：沿用上一個狀態繼續輪詢
          }
        }
      }, POLL_MS)
      return
    }
    ownRef.current = null
    if (s.status === 'done' && s.url) {
      setMediaError(null)
      setSource({ kind: 'transcoded', url: s.url })
    }
  }
  const applyTc = useCallback((s: TranscodeState, id: number, epoch: number) => applyTcRef.current(s, id, epoch), [])

  const startTranscode = useCallback(async () => {
    const id = assetId
    const epoch = epochRef.current
    setMediaError(null)
    setTc(localTc(id, 'running'))
    pollFailures.current = 0
    try {
      const s = await videoApi.startTranscode(id)
      if (epoch !== epochRef.current) {
        videoApi.cancelTranscodeQuietly(id)   // 等待期間已經換片 / 關閉：把剛啟動的轉檔取消掉
        return
      }
      applyTc(s, id, epoch)
    } catch (e) {
      if (epoch !== epochRef.current) return
      setTc(localTc(id, 'error', errText(e, '無法開始轉檔')))
    }
  }, [assetId, applyTc])

  const cancelTranscode = useCallback(async () => {
    const id = assetId
    const epoch = epochRef.current
    window.clearTimeout(pollRef.current)
    ownRef.current = null
    try {
      const s = await videoApi.cancelTranscode(id)
      if (epoch === epochRef.current) setTc(s)
    } catch (e) {
      if (epoch === epochRef.current) setTc(localTc(id, 'error', errText(e, '取消轉檔失敗')))
    }
  }, [assetId])

  /* ───────── 換片：讀資訊並決定來源 ───────── */
  useEffect(() => {
    const epoch = ++epochRef.current
    const id = assetId
    setInfo(null)
    setInfoError(null)
    setSource(null)
    setTc(null)
    setMediaError(null)
    setPlaying(false)
    setBuffering(false)
    setCurrent(0)
    setMediaDuration(0)
    setBufferedEnd(0)
    setSpeedMenu(false)
    onInfoRef.current?.(null)

    videoApi
      .info(id)
      .then((i) => {
        if (epoch !== epochRef.current) return
        setInfo(i)
        onInfoRef.current?.(i)
        const t = i.transcode
        if (i.needs_transcode && t.status === 'done' && t.url) {
          setSource({ kind: 'transcoded', url: t.url })     // 之前轉過、結果還在快取裡：直接用
        } else if (i.playback !== 'transcode') {
          // direct（保證可播）與 try_first（HEVC 等：能不能播取決於這台電腦的瀏覽器與顯示卡）都先播原檔；
          // try_first 失敗才提示轉檔（見下面的 onError / 影像偵測）
          setSource({ kind: 'original', url: i.stream_url })
        } else if (t.status === 'running') {
          applyTc(t, id, epoch)                             // 轉檔還在跑（例如另一個視窗開的）：接手顯示進度
        }
      })
      .catch((e) => {
        if (epoch === epochRef.current) setInfoError(errText(e, '讀不到影片資訊'))
      })

    return () => {
      // 換片 / 關閉播放器：自動取消還在跑的轉檔
      epochRef.current += 1
      window.clearTimeout(pollRef.current)
      if (ownRef.current !== null) {
        videoApi.cancelTranscodeQuietly(ownRef.current)
        ownRef.current = null
      }
    }
  }, [assetId, applyTc])

  // 關閉分頁 / 重新整理
  useEffect(() => {
    const onHide = () => {
      if (ownRef.current !== null) videoApi.cancelTranscodeQuietly(ownRef.current)
    }
    window.addEventListener('pagehide', onHide)
    return () => window.removeEventListener('pagehide', onHide)
  }, [])

  /* ───────── 控制列顯示 / 隱藏 ───────── */
  const showControls = useCallback(() => {
    setControlsVisible(true)
    window.clearTimeout(hideRef.current)
    hideRef.current = window.setTimeout(() => {
      const v = videoRef.current
      if (v && !v.paused && !overControls.current) setControlsVisible(false)
    }, HIDE_CONTROLS_MS)
  }, [])

  useEffect(() => {
    if (!playing) {
      window.clearTimeout(hideRef.current)
      setControlsVisible(true)
    } else {
      showControls()
    }
  }, [playing, showControls])

  useEffect(
    () => () => {
      window.clearTimeout(hideRef.current)
      window.clearTimeout(toastRef.current)
      window.clearTimeout(clickRef.current)
    },
    [],
  )

  const flash = useCallback((text: string) => {
    setToast({ text, id: Date.now() })
    window.clearTimeout(toastRef.current)
    toastRef.current = window.setTimeout(() => setToast(null), 900)
  }, [])

  /* ───────── 全螢幕 / 子母畫面 ───────── */
  useEffect(() => {
    const onFs = () => {
      const on = document.fullscreenElement === stageRef.current
      setFullscreen((was) => {
        if (was && !on) lastFsExit.current = Date.now()
        return on
      })
    }
    document.addEventListener('fullscreenchange', onFs)
    return () => document.removeEventListener('fullscreenchange', onFs)
  }, [])

  useEffect(() => {
    if (!videoEl) return
    const on = () => setPip(true)
    const off = () => setPip(false)
    videoEl.addEventListener('enterpictureinpicture', on)
    videoEl.addEventListener('leavepictureinpicture', off)
    return () => {
      videoEl.removeEventListener('enterpictureinpicture', on)
      videoEl.removeEventListener('leavepictureinpicture', off)
    }
  }, [videoEl])

  const toggleFullscreen = useCallback(() => {
    const el = stageRef.current
    if (!el) return
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {})
    else void el.requestFullscreen().catch(() => {})
  }, [])

  const togglePip = useCallback(() => {
    const v = videoRef.current
    if (!v || !pipSupported) return
    if (document.pictureInPictureElement) void document.exitPictureInPicture().catch(() => {})
    else void v.requestPictureInPicture().catch(() => flash('這個影片無法使用子母畫面'))
  }, [pipSupported, flash])

  /* ───────── 播放控制 ───────── */
  const applyRate = useCallback((r: number) => {
    const v = videoRef.current
    if (v) {
      v.defaultPlaybackRate = r
      v.playbackRate = r
    }
    setRate(r)
    writePref(RATE_KEY, String(r))
  }, [])

  const togglePlay = useCallback(() => {
    const v = videoRef.current
    if (!v) return
    if (v.paused) void v.play().catch(() => {})
    else v.pause()
  }, [])

  const setVolumeValue = useCallback((vol: number) => {
    const v = videoRef.current
    const value = clamp(vol, 0, 1)
    if (v) {
      v.volume = value
      if (value > 0 && v.muted) v.muted = false
    }
    setVolume(value)
    if (value > 0) setMuted(false)
    writePref(VOLUME_KEY, String(value))
    writePref(MUTED_KEY, '0')
  }, [])

  const toggleMute = useCallback(() => {
    const v = videoRef.current
    const next = !(v ? v.muted : muted)
    if (v) v.muted = next
    setMuted(next)
    writePref(MUTED_KEY, next ? '1' : '0')
  }, [muted])

  const seekTo = useCallback((t: number) => {
    const v = videoRef.current
    if (!v) return
    const d = effectiveDuration(v.duration, info?.duration)
    v.currentTime = d > 0 ? clamp(t, 0, d) : Math.max(0, t)
    setCurrent(v.currentTime)
  }, [info])

  const run = (a: PlayerAction) => {
    const v = videoRef.current
    switch (a.type) {
      case 'togglePlay':
        togglePlay()
        break
      case 'seekBy':
        if (v) {
          seekTo(v.currentTime + a.seconds)
          flash(`${a.seconds > 0 ? '快轉' : '倒轉'} ${Math.abs(a.seconds)} 秒`)
        }
        break
      case 'seekPercent':
        if (v && duration > 0) {
          seekTo((duration * a.percent) / 100)
          flash(`${a.percent}%`)
        }
        break
      case 'seekStart':
        seekTo(0)
        break
      case 'volumeBy':
        if (v) {
          const base = v.muted ? 0 : v.volume
          const next = clamp(Math.round((base + a.delta) * 100) / 100, 0, 1)
          setVolumeValue(next)
          flash(`音量 ${Math.round(next * 100)}%`)
        }
        break
      case 'toggleMute':
        toggleMute()
        flash(v && v.muted ? '靜音' : '取消靜音')
        break
      case 'toggleFullscreen':
        toggleFullscreen()
        break
      case 'toggleTheater':
        onToggleTheater()
        break
      case 'togglePip':
        togglePip()
        break
      case 'speedStep': {
        const next = stepSpeed(v ? v.playbackRate : rate, a.direction)
        applyRate(next)
        flash(`速度 ${formatSpeed(next)}`)
        break
      }
      case 'prev':
        onPrev?.()
        break
      case 'next':
        onNext?.()
        break
    }
  }

  const runRef = useRef(run)
  runRef.current = run
  const keyRef = useRef((e: KeyboardEvent): boolean => {
    // 全螢幕中按 Esc 是退出全螢幕：不要讓外層把整個播放視窗也關掉（部分瀏覽器會同時送出 keydown）
    if (e.key === 'Escape') {
      if (document.fullscreenElement || Date.now() - lastFsExit.current < 400) {
        e.preventDefault()
        return true
      }
      return false
    }
    const a = resolvePlayerKey(e)
    if (!a) return false
    e.preventDefault()
    runRef.current(a)
    if (a.type !== 'prev' && a.type !== 'next') showControls()
    return true
  })
  useImperativeHandle(ref, () => ({ handleKey: (e: KeyboardEvent) => keyRef.current(e) }), [])

  /* ───────── <video> 事件 ───────── */
  const syncBuffered = (v: HTMLVideoElement) => {
    const b = v.buffered
    let end = 0
    for (let i = 0; i < b.length; i += 1) {
      if (b.start(i) <= v.currentTime + 0.5) end = Math.max(end, b.end(i))
    }
    setBufferedEnd(end)
  }

  const onLoadedMetadata = (e: React.SyntheticEvent<HTMLVideoElement>) => {
    const v = e.currentTarget
    v.volume = volume
    v.muted = muted
    v.defaultPlaybackRate = rate
    v.playbackRate = rate
    setMediaDuration(v.duration)
  }

  // 先試播原檔：開始播放後約 1.5 秒檢查一次有沒有影像（沒有 error 事件的失敗）
  useEffect(() => {
    if (!trying || !playing || !videoEl || mediaError) return
    const t = window.setTimeout(() => {
      const q = typeof videoEl.getVideoPlaybackQuality === 'function' ? videoEl.getVideoPlaybackQuality() : null
      const msg = diagnoseVideoElement(
        { videoWidth: videoEl.videoWidth, decodedFrames: q ? q.totalVideoFrames : null, currentTime: videoEl.currentTime },
        info?.video_codec,
      )
      if (msg) {
        videoEl.pause()
        setMediaError(msg)
      }
    }, 1500)
    return () => window.clearTimeout(t)
  }, [trying, playing, videoEl, mediaError, info])

  const onVideoClick = () => {
    if (speedMenu) {
      setSpeedMenu(false)
      return
    }
    // 單擊 = 播放 / 暫停；雙擊 = 全螢幕（延遲一下，避免雙擊時先暫停又播放）
    window.clearTimeout(clickRef.current)
    clickRef.current = window.setTimeout(togglePlay, 220)
  }
  const onVideoDoubleClick = () => {
    window.clearTimeout(clickRef.current)
    toggleFullscreen()
  }

  const copyHint = () => {
    void navigator.clipboard?.writeText('winget install Gyan.FFmpeg').then(() => flash('已複製安裝指令')).catch(() => {})
  }

  /* ───────── 畫面 ───────── */
  const transcoding = tc?.status === 'running'
  const needsPanel = !infoError && !transcoding && source === null && info !== null
  const loadingInfo = !infoError && info === null
  const ffmpegOk = info?.ffmpeg_available ?? false
  const VolIcon = muted || volume === 0 ? VolumeX : volume < 0.5 ? Volume1 : Volume2

  return (
    <div
      ref={stageRef}
      data-testid="video-stage"
      className={`relative h-full w-full select-none overflow-hidden bg-black ${playing && !controlsVisible && ready ? 'cursor-none' : ''}`}
      onMouseMove={showControls}
      onMouseLeave={() => {
        if (playing) setControlsVisible(false)
      }}
    >
      {source && (
        <video
          key={source.url}
          ref={attachVideo}
          src={source.url}
          poster={poster}
          autoPlay={autoPlay}
          playsInline
          preload="metadata"
          data-testid="video-element"
          data-source={source.kind}
          className="absolute inset-0 h-full w-full bg-black object-contain"
          onLoadedMetadata={onLoadedMetadata}
          onDurationChange={(e) => setMediaDuration(e.currentTarget.duration)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onWaiting={() => setBuffering(true)}
          onPlaying={() => setBuffering(false)}
          onCanPlay={() => setBuffering(false)}
          onTimeUpdate={(e) => {
            setCurrent(e.currentTarget.currentTime)
            syncBuffered(e.currentTarget)
          }}
          onProgress={(e) => syncBuffered(e.currentTarget)}
          onVolumeChange={(e) => {
            setVolume(e.currentTarget.volume)
            setMuted(e.currentTarget.muted)
          }}
          onError={(e) =>
            setMediaError(
              describeMediaError(e.currentTarget.error?.code) +
                (trying ? `（${info?.video_codec ?? '這個編碼'} 能不能播取決於瀏覽器與顯示卡）` : ''),
            )
          }
          onClick={onVideoClick}
          onDoubleClick={onVideoDoubleClick}
        />
      )}

      {/* 載入中 */}
      {loadingInfo && (
        <div className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-slate-400">
          <Loader2 className="h-5 w-5 animate-spin" /> 讀取影片資訊...
        </div>
      )}
      {ready && buffering && playing && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <Loader2 className="h-10 w-10 animate-spin text-white/80" />
        </div>
      )}

      {/* 讀不到資訊（檔案不存在 / 離線） */}
      {infoError && (
        <div className="absolute inset-0 flex items-center justify-center p-6" data-testid="video-info-error">
          <div className="flex max-w-md flex-col items-center gap-3 text-center">
            <AlertTriangle className="h-8 w-8 text-amber-400" />
            <div className="text-sm font-semibold text-slate-100">無法播放這支影片</div>
            <div className="text-xs leading-relaxed text-slate-400">{infoError}</div>
          </div>
        </div>
      )}

      {/* 瀏覽器播不了：轉檔或安裝說明 */}
      {needsPanel && info && (
        <div className="absolute inset-0 flex items-center justify-center overflow-y-auto p-6" data-testid="video-needs-transcode">
          <div className="flex max-w-lg flex-col items-center gap-3 text-center">
            <Film className="h-9 w-9 text-slate-500" />
            <div className="text-sm font-semibold text-slate-100">瀏覽器無法直接播放這支影片</div>
            <div className="text-xs leading-relaxed text-slate-400">
              {info.reason}
              <div className="mt-1 font-mono text-[11px] text-slate-500">
                {[info.container, info.video_codec, info.audio_codec].filter(Boolean).join(' · ')}
              </div>
            </div>
            {ffmpegOk ? (
              <>
                <button
                  type="button"
                  onClick={() => void startTranscode()}
                  data-testid="video-transcode-start"
                  className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 cursor-pointer"
                >
                  轉檔後播放
                </button>
                <div className="text-[11px] leading-relaxed text-slate-500">
                  用 ffmpeg 轉成 H.264 MP4（最高 1080p）。原檔不會被修改，結果存在 video_cache（上限 5 GB）；
                  轉檔中關閉播放器會自動取消。
                </div>
              </>
            ) : (
              <div className="w-full rounded-xl border border-slate-700 bg-slate-900/80 p-3 text-left text-xs leading-relaxed text-slate-300" data-testid="video-ffmpeg-missing">
                <div className="mb-1 font-semibold text-amber-300">找不到 ffmpeg（選用）</div>
                {info.install_hint}
                <div className="mt-2 flex items-center gap-2">
                  <code className="rounded bg-black/60 px-2 py-1 font-mono text-[11px] text-slate-200">winget install Gyan.FFmpeg</code>
                  <button type="button" onClick={copyHint} className="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800 cursor-pointer">
                    複製
                  </button>
                </div>
                <div className="mt-2 text-[11px] text-slate-500">沒有 ffmpeg 時其他功能不受影響，只是這類影片無法轉檔播放。</div>
              </div>
            )}
            {tc?.status === 'error' && <div className="text-xs text-rose-300" data-testid="video-transcode-error">轉檔失敗：{tc.error}</div>}
            {tc?.status === 'cancelled' && <div className="text-xs text-slate-400">已取消轉檔。</div>}
            <button
              type="button"
              onClick={() => setSource({ kind: 'original', url: info.stream_url })}
              data-testid="video-try-direct"
              className="text-[11px] text-slate-500 underline hover:text-slate-300 cursor-pointer"
            >
              仍嘗試直接播放原檔
            </button>
          </div>
        </div>
      )}

      {/* 轉檔進度 */}
      {transcoding && tc && (
        <div className="absolute inset-0 flex items-center justify-center p-6" data-testid="video-transcoding">
          <div className="flex w-full max-w-md flex-col items-center gap-3 text-center">
            <Loader2 className="h-8 w-8 animate-spin text-sky-400" />
            <div className="text-sm font-semibold text-slate-100">轉檔中（H.264 MP4，最高 1080p）</div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-800">
              {tc.progress === null ? (
                <div className="h-full w-1/3 animate-pulse rounded-full bg-sky-500" />
              ) : (
                <div className="h-full rounded-full bg-sky-500 transition-all duration-500" style={{ width: `${Math.max(2, tc.progress)}%` }} />
              )}
            </div>
            <div className="flex items-center gap-3 font-mono text-xs text-slate-400" data-testid="video-transcode-progress">
              <span>{tc.progress === null ? '進度未知' : `${tc.progress.toFixed(0)}%`}</span>
              {tc.speed && <span>速度 {tc.speed}</span>}
              <span>已用 {formatClock(tc.elapsed_seconds)}</span>
            </div>
            <button
              type="button"
              onClick={() => void cancelTranscode()}
              data-testid="video-transcode-cancel"
              className="flex items-center gap-1.5 rounded-lg border border-rose-800/70 bg-rose-950/40 px-3.5 py-1.5 text-xs font-medium text-rose-300 hover:bg-rose-900/50 cursor-pointer"
            >
              <X className="h-3.5 w-3.5" /> 取消轉檔
            </button>
            <div className="text-[11px] text-slate-500">關閉播放器或切換影片也會自動取消。</div>
          </div>
        </div>
      )}

      {/* 播放失敗 */}
      {source && mediaError && (
        <div className="absolute inset-0 flex items-center justify-center p-6" data-testid="video-media-error">
          <div className="flex max-w-md flex-col items-center gap-3 text-center">
            <AlertTriangle className="h-8 w-8 text-amber-400" />
            <div className="text-sm font-semibold text-slate-100">播放失敗</div>
            <div className="text-xs leading-relaxed text-slate-400">{mediaError}</div>
            {source.kind === 'original' && ffmpegOk && (
              <button
                type="button"
                onClick={() => {
                  setSource(null)
                  void startTranscode()
                }}
                className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 cursor-pointer"
              >
                轉檔後播放
              </button>
            )}
            {source.kind === 'original' && !ffmpegOk && info?.install_hint && (
              <div className="text-[11px] leading-relaxed text-slate-500">{info.install_hint}</div>
            )}
          </div>
        </div>
      )}

      {/* 子母畫面中 */}
      {ready && pip && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/70 text-sm text-slate-300">
          子母畫面播放中（按 P 或關閉小視窗回到這裡）
        </div>
      )}

      {/* 暫停時的中央播放鍵 */}
      {ready && !playing && !buffering && !pip && (
        <button
          type="button"
          tabIndex={-1}
          aria-label="播放"
          onMouseDown={(e) => e.preventDefault()}
          onClick={togglePlay}
          className="absolute left-1/2 top-1/2 flex h-16 w-16 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-black/55 text-white hover:bg-black/75 cursor-pointer"
        >
          <Play className="ml-1 h-8 w-8" />
        </button>
      )}

      {/* 按鍵回饋 */}
      {toast && (
        <div
          key={toast.id}
          data-testid="video-toast"
          className="pointer-events-none absolute left-1/2 top-6 -translate-x-1/2 rounded-lg bg-black/70 px-3 py-1.5 text-sm font-medium text-white"
        >
          {toast.text}
        </div>
      )}

      {/* 控制列 */}
      {ready && (
        <div
          data-testid="video-controls"
          className={`absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 via-black/40 to-transparent px-3 pb-2 pt-8 transition-opacity duration-200 ${
            controlsVisible ? 'opacity-100' : 'pointer-events-none opacity-0'
          }`}
          onMouseEnter={() => {
            overControls.current = true
          }}
          onMouseLeave={() => {
            overControls.current = false
          }}
        >
          <SeekBar current={current} duration={duration} bufferedEnd={bufferedEnd} onSeek={seekTo} />
          <div className="mt-1 flex items-center gap-1">
            <CtrlBtn label={playing ? '暫停（空白鍵）' : '播放（空白鍵）'} onClick={togglePlay} testId="video-play">
              {playing ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5" />}
            </CtrlBtn>
            {onPrev && (
              <CtrlBtn label="上一支（Shift+← 或 PageUp）" onClick={onPrev} testId="video-prev">
                <SkipBack className="h-4 w-4" />
              </CtrlBtn>
            )}
            {onNext && (
              <CtrlBtn label="下一支（Shift+→ 或 PageDown）" onClick={onNext} testId="video-next">
                <SkipForward className="h-4 w-4" />
              </CtrlBtn>
            )}
            <div className="group/volwrap flex items-center gap-1">
              <CtrlBtn label={muted ? '取消靜音（M）' : '靜音（M）'} onClick={toggleMute} testId="video-mute">
                <VolIcon className="h-5 w-5" />
              </CtrlBtn>
              <VolumeSlider volume={volume} muted={muted} onChange={setVolumeValue} />
            </div>
            <span className="ml-2 font-mono text-xs text-slate-200" data-testid="video-time">
              {formatClock(current)} / {formatClock(duration)}
            </span>

            <div className="ml-auto flex items-center gap-1">
              <div className="relative" onMouseLeave={() => setSpeedMenu(false)}>
                <CtrlBtn label="播放速度（, 和 .）" onClick={() => setSpeedMenu((v) => !v)} active={rate !== 1} testId="video-speed">
                  <span className="font-mono">{formatSpeed(rate)}</span>
                </CtrlBtn>
                {speedMenu && (
                  <div className="absolute bottom-full right-0 z-10 pb-1">
                    <div className="rounded-lg border border-white/10 bg-slate-900/95 p-1 shadow-xl">
                      {SPEEDS.map((s) => (
                        <button
                          key={s}
                          type="button"
                          tabIndex={-1}
                          onMouseDown={(e) => e.preventDefault()}
                          onClick={() => {
                            applyRate(s)
                            setSpeedMenu(false)
                          }}
                          className={`block w-full rounded px-4 py-1 text-left font-mono text-xs cursor-pointer ${
                            s === rate ? 'bg-sky-500/30 text-sky-200' : 'text-slate-200 hover:bg-white/10'
                          }`}
                        >
                          {formatSpeed(s)}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              {pipSupported && (
                <CtrlBtn label="子母畫面（P）" onClick={togglePip} active={pip} testId="video-pip">
                  <PictureInPicture2 className="h-4 w-4" />
                </CtrlBtn>
              )}
              <CtrlBtn label={theater ? '離開劇院模式（T）' : '劇院模式（T）'} onClick={onToggleTheater} active={theater} testId="video-theater">
                <RectangleHorizontal className="h-5 w-5" />
              </CtrlBtn>
              <CtrlBtn label={fullscreen ? '離開全螢幕（F）' : '全螢幕（F）'} onClick={toggleFullscreen} testId="video-fullscreen">
                {fullscreen ? <Minimize className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}
              </CtrlBtn>
            </div>
          </div>
        </div>
      )}
    </div>
  )
})
