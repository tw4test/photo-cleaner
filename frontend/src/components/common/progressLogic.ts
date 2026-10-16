// 進度卡片（ScanProgressBar）的純邏輯（v0.7.0，E 項）：步驟對應、完成摘要、是否顯示 N/N、預留空間。
// 不依賴 React / DOM，可用 `npm run test:logic` 在 Node 直接測。
// 後端的欄位來源：backend/app/core/progress.py（job_type / stage / current_stage / pipeline_step / stage_stats …）。

/** 進度卡片需要用到的欄位（ScanProgressState 的子集；欄位都可能缺少） */
export interface ProgressLike {
  is_scanning?: boolean
  is_running?: boolean
  job_type?: string
  stage?: string
  current_stage?: string
  pipeline_step?: string
  total_files?: number
  stage_stats?: Record<string, any> | null
  scan_summary?: Record<string, any> | null
  evaluate_result?: Record<string, any> | null
  new_candidates_count?: number
}

export const isActive = (p: ProgressLike): boolean => !!(p.is_scanning || p.is_running)

/* ───────── 步驟 ───────── */

/** 一鍵流水線的六個步驟：掃描 → 四個分析階段 → 評估 */
export const PIPELINE_STEP_KEYS = ['scan', 'hash', 'basic', 'clip', 'video', 'evaluate'] as const
export type PipelineStepKey = (typeof PIPELINE_STEP_KEYS)[number]

export const ANALYZE_STEP_KEYS = ['hash', 'basic', 'clip', 'video'] as const

const ANALYZE_INDEX: Record<string, number> = { hash: 0, basic: 1, clip: 2, video: 3 }

/**
 * 流水線目前在第幾步（0 起算）；全部完成 = 6（步驟數）。
 * - pipeline_step：scan | analyze | evaluate | done（後端 set_pipeline_step / complete_pipeline）
 * - analyze 期間用 current_stage（hash | basic | clip | video）決定是第幾個分析階段；
 *   剛進入分析、還沒有 current_stage 時算第一個（hash）
 * - 完成（stage = completed 或 pipeline_step = done）→ 全部完成
 * - 失敗 / 取消後後端不會清掉 pipeline_step 與 current_stage，所以仍停在出錯的那一步
 */
export function pipelineStepIndex(p: ProgressLike): number {
  if (p.stage === 'completed' || p.pipeline_step === 'done') return PIPELINE_STEP_KEYS.length
  switch (p.pipeline_step) {
    case 'evaluate':
      return 5
    case 'analyze': {
      const i = ANALYZE_INDEX[p.current_stage ?? '']
      return 1 + (i === undefined ? 0 : i)
    }
    default:
      return 0
  }
}

/** 單獨分析工作的目前階段（0 起算）；完成 = 4；其他狀態（還沒開始）= 0 */
export function analyzeStepIndex(p: ProgressLike): number {
  if (p.stage === 'completed') return ANALYZE_STEP_KEYS.length
  const i = ANALYZE_INDEX[p.current_stage || p.stage || '']
  return i === undefined ? 0 : i
}

/* ───────── 要不要顯示 N / N ───────── */

/**
 * 「處理了 N / N」只在有意義時顯示：
 * - 進行中：目前階段的進度
 * - 分析 / 流水線完成後：總數是**最後一個階段**的（例如 clip 的 N/N 或 video 的 0/0），放在完成卡片上會誤導 → 不顯示
 * - 掃描、補算縮圖完成後：總數就是這次處理的筆數 → 顯示
 */
export function shouldShowCounts(p: ProgressLike): boolean {
  if (!((p.total_files ?? 0) > 0)) return false
  if (isActive(p)) return true
  return p.job_type !== 'analyze' && p.job_type !== 'pipeline'
}

/** 流水線進行中，進度條是「目前這一步」的進度（每一步各自從 0 到 100） */
export const progressLabel = (p: ProgressLike): string =>
  p.job_type === 'pipeline' && isActive(p) ? '目前步驟' : '進度'

/* ───────── 完成摘要 ───────── */

export interface SummaryTile {
  key: string
  label: string
  /** 主要數字（已加單位） */
  value: string
  /** 次要說明（例如「免雜湊 12 筆」） */
  sub?: string
  /** 有失敗時顯示 */
  failed?: number
  /** 這個階段整個失敗（run_analyze_job 把例外記成 {error}） */
  error?: string
  tone: 'slate' | 'emerald' | 'purple' | 'amber'
}

const num = (v: unknown): number => (typeof v === 'number' && isFinite(v) ? v : 0)
export const hasKeys = (o: unknown): boolean => !!o && typeof o === 'object' && Object.keys(o as object).length > 0

/**
 * 分析完成摘要：每個階段取後端回傳的**對應欄位**（不是一律用 done）。
 *   hash  → done（實際算了雜湊）與 not_needed（大小唯一、不必算）
 *   basic → done
 *   clip  → processed（這次真的處理的張數；done 是「本來就完成」的）
 *   video → processed（同上）
 * 只列出這次有執行的階段（stage_stats 裡有的）。
 */
export function analysisTiles(stats: Record<string, any> | null | undefined): SummaryTile[] {
  if (!hasKeys(stats)) return []
  const s = stats as Record<string, any>
  const out: SummaryTile[] = []
  const fail = (r: any) => (num(r?.failed) > 0 ? num(r.failed) : undefined)
  const err = (r: any) => (typeof r?.error === 'string' && r.error ? r.error : undefined)

  if (s.hash) {
    out.push({
      key: 'hash', label: 'Hash 雜湊', tone: 'slate',
      value: `${num(s.hash.done)} 筆`,
      sub: s.hash.not_needed !== undefined ? `免雜湊 ${num(s.hash.not_needed)} 筆` : undefined,
      failed: fail(s.hash), error: err(s.hash),
    })
  }
  if (s.basic) {
    out.push({ key: 'basic', label: 'Basic 特徵', tone: 'emerald', value: `${num(s.basic.done)} 筆`, failed: fail(s.basic), error: err(s.basic) })
  }
  if (s.clip) {
    out.push({ key: 'clip', label: 'CLIP 向量', tone: 'purple', value: `${num(s.clip.processed)} 筆`, failed: fail(s.clip), error: err(s.clip) })
  }
  if (s.video) {
    out.push({ key: 'video', label: 'Video 解析', tone: 'amber', value: `${num(s.video.processed)} 筆`, failed: fail(s.video), error: err(s.video) })
  }
  return out
}

export interface EvaluateSummary {
  skipped: boolean
  total: number
  added: number
  removed: number
}

/** 評估摘要：沒有評估結果（例如流水線在評估前失敗）回 null */
export function evaluateSummary(p: ProgressLike): EvaluateSummary | null {
  const r = p.evaluate_result
  if (!hasKeys(r) && !(p.new_candidates_count && p.new_candidates_count > 0)) return null
  return {
    skipped: !!r?.skipped,
    total: num(r?.total) || num(p.new_candidates_count),
    added: num(r?.added),
    removed: num(r?.removed),
  }
}

/** 完成卡片要顯示哪些摘要區塊（依工作類型；後端預設的空物件 {} 不算有資料） */
export function summaryBlocks(p: ProgressLike): { scan: boolean; analyze: boolean; evaluate: boolean } {
  const t = p.job_type
  return {
    scan: (t === 'scan' || t === 'pipeline') && p.scan_summary?.added !== undefined,
    analyze: (t === 'analyze' || t === 'pipeline') && hasKeys(p.stage_stats),
    evaluate: (t === 'evaluate' || t === 'pipeline') && evaluateSummary(p) !== null,
  }
}

/* ───────── 版面 ───────── */

/** 卡片是固定在視窗底部的（bottom-5 = 20px）；頁面底部要多留的空間 = 卡片高度 + 底部距離 + 一點緩衝 */
export const CARD_BOTTOM_OFFSET = 20
export const CARD_BREATHING = 20
export const BASE_PAGE_BOTTOM = 32

export function pageBottomPadding(cardHeight: number): number {
  if (!(cardHeight > 0)) return BASE_PAGE_BOTTOM
  return Math.max(BASE_PAGE_BOTTOM, Math.ceil(cardHeight) + CARD_BOTTOM_OFFSET + CARD_BREATHING)
}
