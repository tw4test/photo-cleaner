// 審核頁（v0.7.0）影片相關的純邏輯，不依賴 React / DOM，可用 `npm run test:logic` 在 Node 直接測。

/**
 * VD-30：影片候選的分數是**規則固定值**（video_same_size = 1.0、video_tiny = 0.7），不是 AI 信心度。
 * 所以：不顯示成百分比、不顯示在預覽的「分數」欄、不當作「依分數排序」的依據。
 */
export const FIXED_SCORE_RULES: ReadonlySet<string> = new Set(['video_same_size', 'video_tiny'])

export const isFixedScoreRule = (rule: string | null | undefined): boolean => !!rule && FIXED_SCORE_RULES.has(rule)

export type SortKeyLike = 'score' | 'size' | 'path' | 'group'

/**
 * 實際使用的排序。影片分類沒有「分數」可排（見上），使用者在別的分類選過「分數」時，
 * 切到影片分類要退回該分類的預設排序，而不是用一個沒有意義的分數排。
 */
export function resolveSort(category: string, choice: SortKeyLike | null, defaultFor: SortKeyLike): SortKeyLike {
  if (choice === null) return defaultFor
  if (choice === 'score' && category === 'video') return defaultFor
  return choice
}

/** 排序下拉選單要不要列出「分數」：影片分類不列 */
export const sortOffersScore = (category: string): boolean => category !== 'video'

export interface ActionKeyDef<T extends string = string> {
  key: string
  target: T
}

export interface ActionKeyEvent {
  key: string
  ctrlKey?: boolean
  metaKey?: boolean
  altKey?: boolean
  shiftKey?: boolean
}

/**
 * 審核動作的快速鍵（D / K / R）：回傳這個按鍵對應的目標狀態，沒有對應回 null。
 * 只認「目前狀態下有提供的動作」（例如候選頁只有 K 與 D，沒有 R），這與大圖預覽的行為一致。
 * 帶 Ctrl / Meta / Alt（例如 Ctrl+Z 復原）一律不算。
 */
export function matchActionKey<T extends string>(e: ActionKeyEvent, actions: ReadonlyArray<ActionKeyDef<T>>): T | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null
  const k = e.key.toLowerCase()
  const hit = actions.find((a) => a.key.toLowerCase() === k)
  return hit ? hit.target : null
}
