import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Cpu,
  EyeOff,
  FileText,
  Info,
  Laugh,
  Layers,
  Loader2,
  RotateCcw,
  Save,
  Sliders,
  Smartphone,
  Sparkles,
  Sun,
} from 'lucide-react'
import { useWorkflowStatus } from '../hooks/useWorkflowStatus'
import {
  ApiError,
  AppSettings,
  BoolKey,
  NearDupMethod,
  NumKey,
  SettingKey,
  SettingsMeta,
  settingsApi,
} from '../services/settingsApi'

/* ────────────────────────────────────────────────────────────
 * 設定頁（v0.6.2）
 * - 只顯示會明顯影響結果的設定；其餘（保留者排序、影片規則、刪除模式…）使用預設值
 * - 只送出有變更的項目；後端只在變更了「會影響評估」的設定時才重新評估
 * - 預設值 / 範圍來自後端 /api/settings/meta（單一來源）
 * - v0.6.2：頁面自己負責捲動（AppShell 的 main 不一定允許捲動）
 * ──────────────────────────────────────────────────────────── */

/** 這個頁面會編輯的設定鍵（用來判斷「有沒有未儲存的變更」） */
const VISIBLE_KEYS: SettingKey[] = [
  'rule_screenshot_enabled', 'screenshot_threshold',
  'rule_document_enabled', 'document_threshold',
  'rule_meme_enabled', 'meme_threshold',
  'rule_mistouch_enabled', 'mistouch_threshold',
  'rule_exact_dup_enabled',
  'rule_near_dup_enabled', 'near_dup_method', 'clip_dup_threshold', 'phash_threshold',
  'rule_burst_enabled', 'burst_time_window_seconds', 'burst_similarity_threshold',
  'rule_blur_enabled', 'blur_threshold',
  'rule_exposure_enabled', 'under_exposure_threshold', 'over_exposure_threshold',
  'clip_batch_size', 'clip_runners', 'basic_runners', 'hash_runners', 'video_runners',
]

type IconType = React.ComponentType<{ className?: string }>

interface AiRule {
  rule: BoolKey
  threshold: NumKey
  title: string
  desc: string
  icon: IconType
  accent: string
}

const AI_RULES: AiRule[] = [
  { rule: 'rule_screenshot_enabled', threshold: 'screenshot_threshold', title: '螢幕截圖', desc: '手機、電腦螢幕擷取與聊天對話截圖', icon: Smartphone, accent: 'accent-sky-500' },
  { rule: 'rule_document_enabled', threshold: 'document_threshold', title: '文件 / 收據', desc: '拍攝的文件、發票收據、名片、白板筆記', icon: FileText, accent: 'accent-emerald-500' },
  { rule: 'rule_meme_enabled', threshold: 'meme_threshold', title: '梗圖 / 表情包', desc: '網路下載的搞笑圖片、貼圖、迷因', icon: Laugh, accent: 'accent-purple-500' },
  { rule: 'rule_mistouch_enabled', threshold: 'mistouch_threshold', title: '誤拍 / 廢片', desc: '口袋黑畫面、地板、手指遮擋', icon: EyeOff, accent: 'accent-rose-500' },
]

const NEAR_METHODS: Array<{ value: NearDupMethod; label: string; hint: string }> = [
  { value: 'either', label: 'CLIP 或 pHash', hint: '任一符合即可（預設；沒有 CLIP 向量的相片仍可用 pHash 找）' },
  { value: 'clip', label: '只用 CLIP', hint: '可找出裁切、調色、不同解析度的版本' },
  { value: 'phash', label: '只用 pHash', hint: '只找外觀幾乎一模一樣的' },
  { value: 'both', label: '兩者都符合', hint: '最嚴格，誤判最少' },
]

const roundTo = (v: number, decimals: number) => Number(v.toFixed(decimals))

const errText = (e: unknown, fallback: string): string => {
  if (e instanceof Error && e.message) return e.message
  return fallback
}

/* ───────────── 小元件 ───────────── */

const Toggle: React.FC<{ checked: boolean; onChange: (v: boolean) => void; label: string }> = ({ checked, onChange, label }) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    aria-label={label}
    onClick={() => onChange(!checked)}
    className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors cursor-pointer ${checked ? 'bg-sky-500' : 'bg-slate-700'}`}
  >
    <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${checked ? 'translate-x-4' : 'translate-x-0.5'}`} />
  </button>
)

const Card: React.FC<{ icon: IconType; title: string; subtitle?: string; children: React.ReactNode }> = ({ icon: Icon, title, subtitle, children }) => (
  <section className="bg-slate-900 border border-slate-800 rounded-2xl p-5 space-y-4">
    <div className="flex items-start gap-3">
      <div className="p-2 rounded-xl bg-sky-500/10 border border-sky-500/20 text-sky-400 shrink-0">
        <Icon className="w-4 h-4" />
      </div>
      <div>
        <h4 className="text-sm font-semibold text-slate-100">{title}</h4>
        {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
      </div>
    </div>
    {children}
  </section>
)

const Note: React.FC<{ tone?: 'info' | 'warn'; children: React.ReactNode }> = ({ tone = 'info', children }) => (
  <div
    className={`flex gap-2 rounded-xl border px-3 py-2 text-[11px] leading-relaxed ${
      tone === 'warn' ? 'border-amber-800/60 bg-amber-950/30 text-amber-200' : 'border-slate-800 bg-slate-950/60 text-slate-400'
    }`}
  >
    {tone === 'warn' ? <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" /> : <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />}
    <div>{children}</div>
  </div>
)

const NumberBox: React.FC<{
  value: number
  decimals: number
  lo: number
  hi: number
  disabled?: boolean
  onCommit: (v: number) => void
}> = ({ value, decimals, lo, hi, disabled, onCommit }) => {
  const [text, setText] = useState(value.toFixed(decimals))
  useEffect(() => {
    setText(value.toFixed(decimals))
  }, [value, decimals])
  const commit = () => {
    const n = parseFloat(text)
    if (Number.isNaN(n)) {
      setText(value.toFixed(decimals))
      return
    }
    const clamped = roundTo(Math.min(hi, Math.max(lo, n)), decimals)
    setText(clamped.toFixed(decimals))
    if (clamped !== value) onCommit(clamped)
  }
  return (
    <input
      type="text"
      inputMode="decimal"
      value={text}
      disabled={disabled}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
      }}
      className="w-16 text-center bg-slate-950 border border-slate-700 rounded-lg text-slate-100 font-mono text-xs py-1 focus:ring-1 focus:ring-sky-500 focus:outline-none disabled:opacity-40"
    />
  )
}

interface SliderFieldProps {
  label: string
  hint?: string
  value: number
  defaultValue?: number
  min: number
  max: number
  step: number
  decimals: number
  lo: number
  hi: number
  accent?: string
  disabled?: boolean
  onChange: (v: number) => void
}

const SliderField: React.FC<SliderFieldProps> = ({ label, hint, value, defaultValue, min, max, step, decimals, lo, hi, accent = 'accent-sky-500', disabled, onChange }) => {
  const differs = defaultValue !== undefined && Math.abs(value - defaultValue) > 1e-9
  return (
    <div className={`space-y-1.5 ${disabled ? 'opacity-40' : ''}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-medium text-slate-300">{label}</div>
          {hint && <div className="text-[11px] text-slate-500">{hint}</div>}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {differs && !disabled && (
            <button type="button" onClick={() => onChange(defaultValue as number)} className="text-[11px] text-sky-400 hover:text-sky-300 cursor-pointer" title="還原為預設值">
              預設 {(defaultValue as number).toFixed(decimals)}
            </button>
          )}
          <NumberBox value={value} decimals={decimals} lo={lo} hi={hi} disabled={disabled} onCommit={onChange} />
        </div>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={Math.min(max, Math.max(min, value))}
        disabled={disabled}
        onChange={(e) => onChange(roundTo(parseFloat(e.target.value), decimals))}
        className={`w-full ${accent} bg-slate-800 rounded-lg h-1.5 cursor-pointer disabled:cursor-not-allowed`}
      />
    </div>
  )
}

const RuleHeader: React.FC<{ title: string; desc: string; checked: boolean; onChange: (v: boolean) => void; icon?: IconType }> = ({ title, desc, checked, onChange, icon: Icon }) => (
  <div className="flex items-start justify-between gap-3">
    <div className="flex items-start gap-2.5 min-w-0">
      {Icon && <Icon className="w-4 h-4 text-slate-400 mt-0.5 shrink-0" />}
      <div className="min-w-0">
        <div className="text-sm font-medium text-slate-100">{title}</div>
        <div className="text-[11px] text-slate-500">{desc}</div>
      </div>
    </div>
    <Toggle checked={checked} onChange={onChange} label={title} />
  </div>
)

type Msg = { type: 'success' | 'error'; text: string }

/* ───────────── 頁面 ───────────── */

export const Settings: React.FC = () => {
  const { status, reload: reloadStatus } = useWorkflowStatus()
  const [meta, setMeta] = useState<SettingsMeta | null>(null)
  const [saved, setSaved] = useState<AppSettings | null>(null)
  const [draft, setDraft] = useState<AppSettings | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<Msg | null>(null)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const load = useCallback(async () => {
    try {
      const [m, s] = await Promise.all([settingsApi.getMeta(), settingsApi.get()])
      setMeta(m)
      setSaved(s)
      setDraft(s)
      setLoadError(null)
    } catch (e) {
      setLoadError(errText(e, '無法載入系統設定。'))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  /* 有未儲存變更時，關閉 / 重新整理分頁前提醒 */
  const dirtyKeys = useMemo(() => {
    if (!draft || !saved) return [] as SettingKey[]
    return VISIBLE_KEYS.filter((k) => draft[k] !== saved[k])
  }, [draft, saved])

  useEffect(() => {
    if (dirtyKeys.length === 0) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirtyKeys.length])

  const noEval = useMemo(() => new Set(meta?.non_evaluation_keys ?? []), [meta])
  const needsEvaluate = dirtyKeys.some((k) => !noEval.has(k))
  const busy = !!status?.job?.is_running

  const problems = useMemo(() => {
    const p: string[] = []
    if (draft && draft.rule_exposure_enabled && draft.under_exposure_threshold >= draft.over_exposure_threshold) {
      p.push('「過暗」的亮度上限必須小於「過曝」的亮度下限。')
    }
    return p
  }, [draft])

  const setVal = <K extends SettingKey>(key: K, value: AppSettings[K]) => {
    setDraft((prev) => (prev ? ({ ...prev, [key]: value } as AppSettings) : prev))
  }
  const setNum = (key: NumKey, value: number) => setVal(key, value as AppSettings[NumKey])
  const setBool = (key: BoolKey, value: boolean) => setVal(key, value as AppSettings[BoolKey])

  const rangeOf = (key: NumKey, fallbackLo: number, fallbackHi: number): [number, number] => {
    const r = meta?.ranges?.[key]
    return r ? [r[0], r[1]] : [fallbackLo, fallbackHi]
  }
  const defOf = (key: NumKey): number | undefined => meta?.defaults?.[key]

  /** 通用數值欄位 */
  const numField = (
    key: NumKey,
    label: string,
    o: { min: number; max: number; step: number; decimals: number; hint?: string; accent?: string; disabled?: boolean },
  ) => {
    if (!draft) return null
    const [lo, hi] = rangeOf(key, o.min, o.max)
    return (
      <SliderField
        label={label}
        hint={o.hint}
        value={draft[key]}
        defaultValue={defOf(key)}
        min={o.min}
        max={o.max}
        step={o.step}
        decimals={o.decimals}
        lo={lo}
        hi={hi}
        accent={o.accent}
        disabled={o.disabled}
        onChange={(v) => setNum(key, v)}
      />
    )
  }

  const doSave = async (evaluate: boolean) => {
    if (!draft || !saved || dirtyKeys.length === 0 || problems.length > 0) return
    const changes: Partial<AppSettings> = {}
    for (const k of dirtyKeys) (changes as Record<string, unknown>)[k] = draft[k]
    setSaving(true)
    setMessage(null)
    try {
      const res = await settingsApi.save(changes, evaluate && needsEvaluate)
      const fresh = await settingsApi.get()
      setSaved(fresh)
      setDraft(fresh)
      void reloadStatus()
      let text: string
      if (res.evaluated) text = '設定已儲存，候選清單已依新設定重新評估。'
      else if (needsEvaluate) text = '設定已儲存。候選清單尚未依新設定評估，請到資料庫頁執行「評估變更」。'
      else text = '設定已儲存（這些設定不影響偵測結果，不需要重新評估）。'
      setMessage({ type: 'success', text })
    } catch (e) {
      let text = errText(e, '儲存設定失敗。')
      if (e instanceof ApiError && e.status === 409) {
        text = '目前有任務進行中，無法同時重新評估。請等任務完成，或改按「只儲存」稍後再評估。'
      }
      // 後端是「先寫入、再評估」：評估失敗時設定其實已經存好，要讓畫面和資料庫一致
      try {
        const fresh = await settingsApi.get()
        if (dirtyKeys.every((k) => fresh[k] === draft[k])) {
          setSaved(fresh)
          setDraft(fresh)
          text = `設定已儲存，但重新評估失敗：${text}`
        }
      } catch {
        /* 讀不到就保留目前畫面 */
      }
      setMessage({ type: 'error', text })
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    if (!window.confirm('要把所有設定（包含未顯示的）還原為預設值嗎？\n若被還原的設定會影響偵測結果，會一併重新評估。')) return
    setSaving(true)
    setMessage(null)
    try {
      const res = await settingsApi.reset()
      await load()
      void reloadStatus()
      setMessage({ type: 'success', text: res.message })
    } catch (e) {
      const text = e instanceof ApiError && e.status === 409
        ? '目前有任務進行中，無法還原會影響評估的設定。請等任務完成後再試。'
        : errText(e, '還原設定失敗。')
      setMessage({ type: 'error', text })
    } finally {
      setSaving(false)
    }
  }

  if (!draft || !saved || !meta) {
    return (
      <div className="h-full min-h-0 overflow-y-auto space-y-4 pr-2 pt-1">
        <h3 className="text-2xl font-bold text-slate-100">系統與偵測規則設定</h3>
        {loadError ? (
          <div className="p-4 rounded-xl text-sm flex items-center gap-2 border bg-rose-950/40 text-rose-300 border-rose-800/60">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{loadError}</span>
            <button type="button" onClick={() => void load()} className="ml-auto underline cursor-pointer">重試</button>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <Loader2 className="w-4 h-4 animate-spin" /> 載入設定中...
          </div>
        )}
      </div>
    )
  }

  const method = draft.near_dup_method
  const showClipDup = method !== 'phash'
  const showPhashDup = method !== 'clip'
  const evalStale = !!status?.evaluation?.is_stale && dirtyKeys.length === 0
  const primaryBlocked = saving || dirtyKeys.length === 0 || problems.length > 0 || (needsEvaluate && busy)

  return (
    /* 頁面自己捲動：h-full 取得 main 的高度，overflow-y-auto 讓內容超出時可捲動；
       儲存列的 sticky 也是相對於這個捲動容器 */
    <div className="h-full min-h-0 overflow-y-auto space-y-6 pr-2 pt-1 pb-10">
      {/* 標題 */}
      <div>
        <h3 className="text-2xl font-bold text-slate-100 flex items-center gap-2">
          <Sliders className="w-6 h-6 text-sky-400" />
          系統與偵測規則設定
        </h3>
        <p className="text-sm text-slate-400 mt-1">調整偵測規則的門檻。這些設定只影響「評估」，不需要重新掃描或分析相片。</p>
      </div>

      {/* 狀態提示 */}
      {busy && status && (
        <Note tone="warn">
          目前有任務進行中（{status.job.stage_label}　{Math.round(status.job.percentage)}%）。進行中只能儲存「不影響偵測結果」的設定；
          會影響偵測結果的設定請等任務完成，或選「只儲存」稍後再評估。
        </Note>
      )}
      {evalStale && !busy && (
        <Note>
          候選清單不是最新的（設定或相片庫已變更）。到 <Link to="/library" className="text-sky-400 underline">資料庫頁</Link> 執行「評估變更」即可更新。
        </Note>
      )}
      {message && (
        <div className={`p-4 rounded-xl text-sm flex items-center gap-2 border ${message.type === 'success' ? 'bg-emerald-950/40 text-emerald-300 border-emerald-800/60' : 'bg-rose-950/40 text-rose-300 border-rose-800/60'}`}>
          {message.type === 'success' ? <CheckCircle2 className="w-4 h-4 shrink-0" /> : <AlertCircle className="w-4 h-4 shrink-0" />}
          <span>{message.text}</span>
        </div>
      )}
      {problems.map((p) => (
        <Note key={p} tone="warn">{p}</Note>
      ))}

      {/* 儲存列 */}
      <div className="sticky top-0 z-20 py-2 bg-slate-950/85 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-800 bg-slate-900 px-4 py-3">
          <div className="text-xs">
            {dirtyKeys.length === 0 ? (
              <span className="text-slate-500">目前沒有未儲存的變更</span>
            ) : (
              <>
                <span className="text-amber-300 font-medium">● {dirtyKeys.length} 項未儲存的變更</span>
                <span className="text-slate-400 ml-2">
                  {needsEvaluate ? '儲存後會重新評估候選清單（約數秒）' : '這些變更不影響偵測結果，不需要重新評估'}
                </span>
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={saving}
              onClick={() => void handleReset()}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-300 rounded-lg text-xs font-medium border border-slate-700 cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" /> 還原預設
            </button>
            {dirtyKeys.length > 0 && (
              <button
                type="button"
                disabled={saving}
                onClick={() => setDraft(saved)}
                className="px-3 py-1.5 text-slate-400 hover:text-slate-200 text-xs cursor-pointer"
              >
                放棄變更
              </button>
            )}
            {needsEvaluate && (
              <button
                type="button"
                disabled={saving || problems.length > 0}
                onClick={() => void doSave(false)}
                className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-200 rounded-lg text-xs font-medium border border-slate-700 cursor-pointer"
                title="只儲存設定，稍後再到資料庫頁評估"
              >
                只儲存
              </button>
            )}
            <button
              type="button"
              disabled={primaryBlocked}
              onClick={() => void doSave(true)}
              className="flex items-center gap-1.5 px-4 py-1.5 bg-sky-600 hover:bg-sky-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-lg text-xs font-semibold shadow-md shadow-sky-600/20 cursor-pointer"
              title={needsEvaluate && busy ? '任務進行中，請改按「只儲存」' : undefined}
            >
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              {needsEvaluate ? '儲存並重新評估' : '儲存變更'}
            </button>
          </div>
        </div>
      </div>

      {/* 1. AI 內容分類 */}
      <Card icon={Sparkles} title="AI 內容分類" subtitle="CLIP 零樣本分類：找出截圖、文件、梗圖與誤拍">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {AI_RULES.map((r) => (
            <div key={r.rule} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4 space-y-3">
              <RuleHeader title={r.title} desc={r.desc} icon={r.icon} checked={draft[r.rule]} onChange={(v) => setBool(r.rule, v)} />
              {numField(r.threshold, '判定分數門檻', { min: 0.3, max: 0.95, step: 0.01, decimals: 2, accent: r.accent, disabled: !draft[r.rule] })}
            </div>
          ))}
        </div>
        <Note>分數是 CLIP 對該類別的機率，且必須同時高於「一般相片」的分數才會列入候選。調高 = 更嚴格、誤判更少但可能漏掉。</Note>
      </Card>

      {/* 2. 重複與連拍 */}
      <Card icon={Layers} title="重複與連拍" subtitle="每張相片只會歸入一個群組（完全重複 → 近似 → 連拍），每組自動保留最佳的一張">
        <div className="space-y-5">
          <RuleHeader title="完全重複" desc="SHA-256 完全相同；跨路徑比對，依路徑優先序保留一張" checked={draft.rule_exact_dup_enabled} onChange={(v) => setBool('rule_exact_dup_enabled', v)} />

          <div className="border-t border-slate-800 pt-4 space-y-3">
            <RuleHeader title="近似重複" desc="外觀非常相似但不完全相同（縮放、裁切、重新壓縮…）" checked={draft.rule_near_dup_enabled} onChange={(v) => setBool('rule_near_dup_enabled', v)} />
            <div className={`space-y-3 ${draft.rule_near_dup_enabled ? '' : 'opacity-40 pointer-events-none'}`}>
              <div>
                <div className="text-xs font-medium text-slate-300 mb-1.5">比對方式</div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  {NEAR_METHODS.map((m) => (
                    <button
                      key={m.value}
                      type="button"
                      onClick={() => setVal('near_dup_method', m.value)}
                      className={`rounded-xl border px-3 py-2 text-xs text-left transition-colors cursor-pointer ${
                        method === m.value ? 'border-sky-500 bg-sky-950/40 text-slate-100 ring-1 ring-sky-500' : 'border-slate-800 bg-slate-950/50 text-slate-400 hover:border-slate-700'
                      }`}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
                <div className="text-[11px] text-slate-500 mt-1.5">{NEAR_METHODS.find((m) => m.value === method)?.hint}</div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {showClipDup && numField('clip_dup_threshold', 'CLIP 相似度門檻', { min: 0.8, max: 1, step: 0.01, decimals: 2, hint: '越高越嚴格' })}
                {showPhashDup && numField('phash_threshold', 'pHash 漢明距離上限', { min: 0, max: 20, step: 1, decimals: 0, hint: '越小越嚴格' })}
              </div>
            </div>
          </div>

          <div className="border-t border-slate-800 pt-4 space-y-3">
            <RuleHeader title="連拍" desc="短時間內連續拍的相似相片，保留最清晰的一張" checked={draft.rule_burst_enabled} onChange={(v) => setBool('rule_burst_enabled', v)} />
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {numField('burst_time_window_seconds', '時間視窗（秒）', { min: 0, max: 10, step: 0.5, decimals: 1, hint: '拍攝時間相差不超過此值', disabled: !draft.rule_burst_enabled })}
              {numField('burst_similarity_threshold', 'CLIP 相似度門檻', { min: 0.5, max: 1, step: 0.01, decimals: 2, disabled: !draft.rule_burst_enabled })}
            </div>
            <Note>連拍需要 EXIF 拍攝時間；讀不到時（以檔案修改時間代替）不會採用連拍規則。</Note>
          </div>
        </div>
      </Card>

      {/* 3. 畫質 */}
      <Card icon={Sun} title="畫質" subtitle="模糊與曝光不良">
        <div className="space-y-5">
          <div className="space-y-3">
            <RuleHeader title="模糊相片" desc="拉普拉斯變異數低於門檻視為模糊" checked={draft.rule_blur_enabled} onChange={(v) => setBool('rule_blur_enabled', v)} />
            {numField('blur_threshold', '清晰度門檻（低於此值視為模糊）', { min: 0, max: 500, step: 5, decimals: 0, accent: 'accent-amber-500', disabled: !draft.rule_blur_enabled })}
            <Note tone="warn">
              v0.6 起模糊分數改在「最長邊 1024px」的縮小圖上計算，舊預設值 100 尚未重新校準。
              建議先用預設值評估，到審核頁的「模糊與曝光」抽樣檢查後再調整。
            </Note>
          </div>

          <div className="border-t border-slate-800 pt-4 space-y-3">
            <RuleHeader title="曝光不良" desc="以平均亮度（0–255）判斷過暗或過曝" checked={draft.rule_exposure_enabled} onChange={(v) => setBool('rule_exposure_enabled', v)} />
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {numField('under_exposure_threshold', '過暗：平均亮度低於', { min: 0, max: 255, step: 1, decimals: 0, disabled: !draft.rule_exposure_enabled })}
              {numField('over_exposure_threshold', '過曝：平均亮度高於', { min: 0, max: 255, step: 1, decimals: 0, accent: 'accent-amber-500', disabled: !draft.rule_exposure_enabled })}
            </div>
          </div>
        </div>
      </Card>

      {/* 4. 進階（收合） */}
      <section className="bg-slate-900 border border-slate-800 rounded-2xl">
        <button
          type="button"
          onClick={() => setAdvancedOpen((o) => !o)}
          className="w-full flex items-center justify-between gap-3 px-5 py-4 text-left cursor-pointer"
        >
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl bg-slate-800 border border-slate-700 text-slate-400">
              <Cpu className="w-4 h-4" />
            </div>
            <div>
              <div className="text-sm font-semibold text-slate-100">進階：分析效能</div>
              <div className="text-xs text-slate-500">只影響分析速度，不影響偵測結果；儲存時不會重新評估</div>
            </div>
          </div>
          {advancedOpen ? <ChevronUp className="w-4 h-4 text-slate-500" /> : <ChevronDown className="w-4 h-4 text-slate-500" />}
        </button>
        {advancedOpen && (
          <div className="px-5 pb-5 space-y-4 border-t border-slate-800 pt-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {numField('clip_batch_size', 'CLIP 批次大小（0 = 依顯存自動）', { min: 0, max: 256, step: 8, decimals: 0, hint: '顯存不足時會自動減半重試' })}
              {numField('clip_runners', 'CLIP 讀圖執行緒', { min: 1, max: 16, step: 1, decimals: 0, hint: '負責讀縮圖與前處理；GPU 推理本身固定單一流水線' })}
              {numField('basic_runners', '縮圖與基本特徵執行緒', { min: 1, max: 16, step: 1, decimals: 0, hint: 'CPU 密集' })}
              {numField('hash_runners', '雜湊執行緒', { min: 1, max: 16, step: 1, decimals: 0, hint: 'I/O 密集（NAS 上不宜太高）' })}
              {numField('video_runners', '影片探查執行緒', { min: 1, max: 16, step: 1, decimals: 0 })}
            </div>
            <Note>
              CLIP 讀圖執行緒太少時 GPU 會等圖；夠多之後瓶頸是 GPU，再加沒有幫助。建議 2–4，實際以分析時的每秒張數為準。
            </Note>
          </div>
        )}
      </section>

      <p className="text-[11px] text-slate-600 leading-relaxed">
        未顯示的設定（保留者排序、跨路徑重複、近似群組上限、影片規則、CLIP 模型、刪除模式）使用預設值。
        刪除一律移入資源回收筒。
      </p>
    </div>
  )
}
