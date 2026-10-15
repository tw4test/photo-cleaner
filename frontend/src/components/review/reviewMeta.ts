import type { ComponentType } from 'react'
import { Camera, Copy, EyeOff, FileText, Images, Laugh, Layers, Smartphone, Sparkles, Sun, Video } from 'lucide-react'
import { CategoryKey, KeeperMode, SortKey } from '../../services/reviewApi'

export type IconType = ComponentType<{ className?: string }>

export interface CategoryDef {
  key: CategoryKey
  label: string
  icon: IconType
  tag?: string
  /** 分區：側邊欄在不同區之間畫分隔線 */
  section: 'all' | 'ai' | 'dup' | 'quality'
}

export const CATEGORIES: CategoryDef[] = [
  { key: 'all', label: '全部候選', icon: Sparkles, section: 'all' },
  { key: 'screenshot', label: '螢幕截圖', icon: Smartphone, tag: 'AI', section: 'ai' },
  { key: 'document', label: '文件 / 收據', icon: FileText, tag: 'AI', section: 'ai' },
  { key: 'meme', label: '梗圖 / 迷因', icon: Laugh, tag: 'AI', section: 'ai' },
  { key: 'mistouch', label: '誤拍 / 廢片', icon: EyeOff, tag: 'AI', section: 'ai' },
  { key: 'exact_dup', label: '完全重複', icon: Copy, section: 'dup' },
  { key: 'near_dup', label: '近似重複', icon: Layers, section: 'dup' },
  { key: 'burst', label: '連拍', icon: Images, section: 'dup' },
  { key: 'blur', label: '模糊相片', icon: Camera, section: 'quality' },
  { key: 'exposure', label: '曝光不良', icon: Sun, section: 'quality' },
  { key: 'video', label: '影片', icon: Video, section: 'quality' },
]

export const AI_CATEGORIES: CategoryKey[] = ['screenshot', 'document', 'meme', 'mistouch']
export const DUP_CATEGORIES: CategoryKey[] = ['exact_dup', 'near_dup', 'burst']
/** 這些分類的「螢幕截圖 / 文件」操作不受群組保護（使用者決定） */
export const WAIVABLE_CATEGORIES: CategoryKey[] = ['screenshot', 'document']

export const isAi = (c: CategoryKey) => AI_CATEGORIES.includes(c)

export function defaultSort(c: CategoryKey): SortKey {
  if (isAi(c)) return 'score'
  if (c === 'blur' || c === 'exposure' || c === 'video') return 'size'
  return 'group'
}

/** 重複類別預設顯示 Keeper（看得到群組全貌）；其他分類（含「全部」）預設隱藏，數量才會和側邊欄、首頁一致 */
export function defaultKeepers(c: CategoryKey): KeeperMode {
  return DUP_CATEGORIES.includes(c) ? 'show' : 'hide'
}

export interface RuleBadge {
  label: string
  color: string
  icon: IconType
}

export function ruleBadge(rule: string): RuleBadge {
  switch (rule) {
    case 'screenshot': return { label: '螢幕截圖', color: 'bg-sky-500/20 text-sky-300 border-sky-500/30', icon: Smartphone }
    case 'document': return { label: '文件/收據', color: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30', icon: FileText }
    case 'meme': return { label: '梗圖迷因', color: 'bg-purple-500/20 text-purple-300 border-purple-500/30', icon: Laugh }
    case 'mistouch': return { label: '誤拍廢片', color: 'bg-rose-500/20 text-rose-300 border-rose-500/30', icon: EyeOff }
    case 'exact_dup': return { label: '完全重複', color: 'bg-amber-500/20 text-amber-300 border-amber-500/30', icon: Copy }
    case 'near_dup': return { label: '近似重複', color: 'bg-indigo-500/20 text-indigo-300 border-indigo-500/30', icon: Layers }
    case 'burst': return { label: '連拍', color: 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30', icon: Images }
    case 'blur': return { label: '模糊', color: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30', icon: Camera }
    case 'over_exposure': return { label: '過曝', color: 'bg-orange-500/20 text-orange-300 border-orange-500/30', icon: Sun }
    case 'under_exposure': return { label: '過暗', color: 'bg-orange-500/20 text-orange-300 border-orange-500/30', icon: Sun }
    case 'video_same_size': return { label: '影片重複', color: 'bg-slate-500/20 text-slate-300 border-slate-500/30', icon: Video }
    case 'video_tiny': return { label: '極小影片', color: 'bg-slate-500/20 text-slate-300 border-slate-500/30', icon: Video }
    default: return { label: rule, color: 'bg-slate-800 text-slate-300 border-slate-700', icon: Sparkles }
  }
}

/** 分數是 CLIP 機率（0–1）的規則：只有這些規則才顯示百分比 */
export const SCORE_AS_PERCENT_RULES = new Set<string>(['screenshot', 'document', 'meme', 'mistouch'])
