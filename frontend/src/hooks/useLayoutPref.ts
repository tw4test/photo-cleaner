import { useCallback, useSyncExternalStore } from 'react'

/**
 * 版面偏好（側邊欄是否收合）。
 * - 存在 localStorage，重新整理後維持原狀。
 * - 用 useSyncExternalStore 做成共用狀態：AppShell（主選單）、Review（篩選欄）、
 *   以及「專注模式」快速鍵都讀寫同一份，彼此即時同步。
 */
export const NAV_COLLAPSED_KEY = 'pc.layout.nav.collapsed'
export const REVIEW_FILTERS_COLLAPSED_KEY = 'pc.layout.review.filters.collapsed'

const listeners = new Set<() => void>()
const cache = new Map<string, boolean>()

export function getLayoutPref(key: string): boolean {
  const hit = cache.get(key)
  if (hit !== undefined) return hit
  let v = false
  try {
    v = window.localStorage.getItem(key) === '1'
  } catch {
    /* localStorage 不可用時預設展開 */
  }
  cache.set(key, v)
  return v
}

export function setLayoutPref(key: string, value: boolean): void {
  cache.set(key, value)
  try {
    window.localStorage.setItem(key, value ? '1' : '0')
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l())
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

export function useLayoutPref(key: string): [boolean, (v: boolean) => void, () => void] {
  const value = useSyncExternalStore(subscribe, () => getLayoutPref(key), () => false)
  const set = useCallback((v: boolean) => setLayoutPref(key, v), [key])
  const toggle = useCallback(() => setLayoutPref(key, !getLayoutPref(key)), [key])
  return [value, set, toggle]
}
