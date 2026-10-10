import React, { useState, useEffect } from 'react'
import { 
  Sliders, 
  ShieldCheck, 
  Sparkles, 
  RotateCcw, 
  Save, 
  CheckCircle2, 
  AlertCircle,
  Camera,
  FileText,
  Smartphone,
  Laugh,
  EyeOff,
  Cpu,
  Zap,
  Plus,
  Minus
} from 'lucide-react'
import { api } from '../services/api'

export const Settings: React.FC = () => {
  const [config, setConfig] = useState<Record<string, any>>({})
  const [loading, setLoading] = useState<boolean>(true)
  const [saving, setSaving] = useState<boolean>(false)
  const [reEvaluate, setReEvaluate] = useState<boolean>(true)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const loadSettings = async () => {
    try {
      setLoading(true)
      const data = await api.getSettings()
      setConfig(data)
    } catch (err) {
      console.error('Failed to load settings:', err)
      setMessage({ type: 'error', text: '無法載入系統設定。' })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSettings()
  }, [])

  const handleChange = (key: string, value: any) => {
    setConfig(prev => ({ ...prev, [key]: value }))
  }

  const handleSave = async () => {
    try {
      setSaving(true)
      setMessage(null)
      const res = await api.updateSettings(config, reEvaluate)
      setMessage({ type: 'success', text: res.message || '設定已成功儲存！' })
      setTimeout(() => setMessage(null), 4000)
    } catch (err: any) {
      setMessage({ type: 'error', text: '儲存設定失敗。' })
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    if (!window.confirm('確定要將所有設定還原為預設值嗎？')) return
    try {
      setSaving(true)
      setMessage(null)
      const res = await api.resetSettings()
      setMessage({ type: 'success', text: res.message || '已還原預設設定！' })
      loadSettings()
    } catch (err) {
      setMessage({ type: 'error', text: '還原設定失敗。' })
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="p-8 text-center text-slate-500">載入設定中...</div>
  }

  return (
    <div className="p-8 space-y-8 overflow-y-auto max-w-7xl mx-auto w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-100 flex items-center gap-2.5">
            <Sliders className="w-6 h-6 text-sky-400" />
            <span>系統與偵測規則設定</span>
          </h2>
          <p className="text-slate-400 text-sm mt-1">
            配置 AI 語義內容分析（截圖/文件/梗圖/誤拍）、重複比對門檻與安全刪除偏好
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleReset}
            disabled={saving}
            className="flex items-center gap-1.5 px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-medium border border-slate-700 transition-colors"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>還原預設</span>
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-5 py-2 bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white rounded-xl text-sm font-semibold transition-all shadow-lg shadow-sky-600/20"
          >
            <Save className="w-4 h-4" />
            <span>{saving ? '儲存中...' : '儲存變更'}</span>
          </button>
        </div>
      </div>

      {/* Notice Banner */}
      {message && (
        <div className={`p-4 rounded-xl text-sm flex items-center gap-2 border animate-fade-in ${
          message.type === 'success' 
            ? 'bg-emerald-950/40 text-emerald-300 border-emerald-800/60' 
            : 'bg-rose-950/40 text-rose-300 border-rose-800/60'
        }`}>
          {message.type === 'success' ? <CheckCircle2 className="w-4 h-4 shrink-0" /> : <AlertCircle className="w-4 h-4 shrink-0" />}
          <span>{message.text}</span>
        </div>
      )}

      {/* Auto Re-evaluate Checkbox */}
      <div className="bg-sky-950/30 border border-sky-800/40 rounded-xl p-4 flex items-center justify-between text-xs">
        <div className="flex items-center gap-2.5 text-sky-300">
          <Sparkles className="w-4 h-4 text-sky-400" />
          <span>儲存時自動重新評估所有已掃描之相片庫（立即產出新候選清單，不需重新解碼相片）</span>
        </div>
        <label className="flex items-center gap-2 text-slate-300 cursor-pointer">
          <input
            type="checkbox"
            checked={reEvaluate}
            onChange={(e) => setReEvaluate(e.target.checked)}
            className="rounded border-slate-700 bg-slate-950 text-sky-500 focus:ring-sky-500"
          />
          <span>自動重新評估</span>
        </label>
      </div>

      {/* Section 1: AI 內容語義分類 (重點核心價值) */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <div>
            <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-amber-400" />
              <span>AI 內容語義分析分類（CLIP Zero-Shot 分類）</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              依據相片實際圖像內容自動辨識分類，適合清理相簿中佔用空間之各類日常雜圖與廢片
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* AI 1: 螢幕截圖 */}
          <div className="p-4 bg-slate-950/60 rounded-xl border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Smartphone className="w-5 h-5 text-sky-400 shrink-0" />
                <div>
                  <span className="font-semibold text-slate-200 text-sm">螢幕截圖 (Screenshots)</span>
                  <p className="text-xs text-slate-500">手機、電腦螢幕擷取畫面與聊天對話紀錄截圖</p>
                </div>
              </div>
              <input
                type="checkbox"
                checked={config.rule_screenshot_enabled ?? true}
                onChange={(e) => handleChange('rule_screenshot_enabled', e.target.checked)}
                className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500"
              />
            </div>

            {config.rule_screenshot_enabled && (
              <div className="space-y-1.5 pt-2 border-t border-slate-800/80">
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">AI 判定信心度門檻：</span>
                  <span className="text-sky-400 font-mono font-bold">≥ {Math.round((config.screenshot_threshold ?? 0.7) * 100)}%</span>
                </div>
                <input
                  type="range"
                  min="0.5"
                  max="0.95"
                  step="0.05"
                  value={config.screenshot_threshold ?? 0.7}
                  onChange={(e) => handleChange('screenshot_threshold', parseFloat(e.target.value))}
                  className="w-full accent-sky-500 bg-slate-800 rounded-lg h-1.5 cursor-pointer"
                />
              </div>
            )}
          </div>

          {/* AI 2: 文件/收據/白板 */}
          <div className="p-4 bg-slate-950/60 rounded-xl border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <FileText className="w-5 h-5 text-emerald-400 shrink-0" />
                <div>
                  <span className="font-semibold text-slate-200 text-sm">文件 / 收據 / 發票 / 白板筆記 (Documents)</span>
                  <p className="text-xs text-slate-500">拍攝的文件、報表、發票收據、名片或會議白板筆記</p>
                </div>
              </div>
              <input
                type="checkbox"
                checked={config.rule_document_enabled ?? true}
                onChange={(e) => handleChange('rule_document_enabled', e.target.checked)}
                className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500"
              />
            </div>

            {config.rule_document_enabled && (
              <div className="space-y-1.5 pt-2 border-t border-slate-800/80">
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">AI 判定信心度門檻：</span>
                  <span className="text-emerald-400 font-mono font-bold">≥ {Math.round((config.document_threshold ?? 0.65) * 100)}%</span>
                </div>
                <input
                  type="range"
                  min="0.5"
                  max="0.95"
                  step="0.05"
                  value={config.document_threshold ?? 0.65}
                  onChange={(e) => handleChange('document_threshold', parseFloat(e.target.value))}
                  className="w-full accent-emerald-500 bg-slate-800 rounded-lg h-1.5 cursor-pointer"
                />
              </div>
            )}
          </div>

          {/* AI 3: 梗圖/表情包/迷因 */}
          <div className="p-4 bg-slate-950/60 rounded-xl border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Laugh className="w-5 h-5 text-purple-400 shrink-0" />
                <div>
                  <span className="font-semibold text-slate-200 text-sm">網路梗圖 / 表情包 / 迷因 (Memes)</span>
                  <p className="text-xs text-slate-500">網路下載的搞笑圖片、梗圖、社交貼圖與表情包</p>
                </div>
              </div>
              <input
                type="checkbox"
                checked={config.rule_meme_enabled ?? true}
                onChange={(e) => handleChange('rule_meme_enabled', e.target.checked)}
                className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500"
              />
            </div>

            {config.rule_meme_enabled && (
              <div className="space-y-1.5 pt-2 border-t border-slate-800/80">
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">AI 判定信心度門檻：</span>
                  <span className="text-purple-400 font-mono font-bold">≥ {Math.round((config.meme_threshold ?? 0.75) * 100)}%</span>
                </div>
                <input
                  type="range"
                  min="0.5"
                  max="0.95"
                  step="0.05"
                  value={config.meme_threshold ?? 0.75}
                  onChange={(e) => handleChange('meme_threshold', parseFloat(e.target.value))}
                  className="w-full accent-purple-500 bg-slate-800 rounded-lg h-1.5 cursor-pointer"
                />
              </div>
            )}
          </div>

          {/* AI 4: 誤拍廢片 */}
          <div className="p-4 bg-slate-950/60 rounded-xl border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <EyeOff className="w-5 h-5 text-rose-400 shrink-0" />
                <div>
                  <span className="font-semibold text-slate-200 text-sm">誤拍 / 口袋 / 地板 / 手指遮擋 (Mistouches)</span>
                  <p className="text-xs text-slate-500">誤觸快門拍到之純黑口袋、地板、大腿或手指遮擋畫面</p>
                </div>
              </div>
              <input
                type="checkbox"
                checked={config.rule_mistouch_enabled ?? true}
                onChange={(e) => handleChange('rule_mistouch_enabled', e.target.checked)}
                className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500"
              />
            </div>

            {config.rule_mistouch_enabled && (
              <div className="space-y-1.5 pt-2 border-t border-slate-800/80">
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">AI 判定信心度門檻：</span>
                  <span className="text-rose-400 font-mono font-bold">≥ {Math.round((config.mistouch_threshold ?? 0.6) * 100)}%</span>
                </div>
                <input
                  type="range"
                  min="0.4"
                  max="0.9"
                  step="0.05"
                  value={config.mistouch_threshold ?? 0.6}
                  onChange={(e) => handleChange('mistouch_threshold', parseFloat(e.target.value))}
                  className="w-full accent-rose-500 bg-slate-800 rounded-lg h-1.5 cursor-pointer"
                />
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Section 2: 傳統重複與質量規則 */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <div>
            <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
              <Camera className="w-5 h-5 text-sky-400" />
              <span>傳統特徵與質量缺陷規則</span>
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">完全重複 Hash 比對、近似圖片、模糊與曝光不良檢查</p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* 完全重複 */}
          <div className="p-4 bg-slate-950/60 rounded-xl border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <span className="font-semibold text-slate-200 text-sm">完全重複 (Exact Duplicate)</span>
                <p className="text-xs text-slate-500">SHA-256 雜湊值 100% 相同，跨路徑比對並保留最佳一份</p>
              </div>
              <input
                type="checkbox"
                checked={config.rule_exact_dup_enabled ?? true}
                onChange={(e) => handleChange('rule_exact_dup_enabled', e.target.checked)}
                className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500"
              />
            </div>
          </div>

          {/* 近似重複 */}
          <div className="p-4 bg-slate-950/60 rounded-xl border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <span className="font-semibold text-slate-200 text-sm">近似重複 (Near Duplicate)</span>
                <p className="text-xs text-slate-500">使用感知雜湊 (pHash) 找出視覺相近之相片</p>
              </div>
              <input
                type="checkbox"
                checked={config.rule_near_dup_enabled ?? true}
                onChange={(e) => handleChange('rule_near_dup_enabled', e.target.checked)}
                className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500"
              />
            </div>

            {config.rule_near_dup_enabled && (
              <div className="space-y-1.5 pt-2 border-t border-slate-800/80">
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">pHash 漢明距離門檻：</span>
                  <span className="text-sky-400 font-mono font-bold">{config.phash_threshold ?? 6}</span>
                </div>
                <input
                  type="range"
                  min="1"
                  max="15"
                  step="1"
                  value={config.phash_threshold ?? 6}
                  onChange={(e) => handleChange('phash_threshold', parseInt(e.target.value))}
                  className="w-full accent-sky-500 bg-slate-800 rounded-lg h-1.5 cursor-pointer"
                />
              </div>
            )}
          </div>

          {/* 模糊相片 */}
          <div className="p-4 bg-slate-950/60 rounded-xl border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <span className="font-semibold text-slate-200 text-sm">模糊相片偵測 (Blur Detection)</span>
                <p className="text-xs text-slate-500">透過拉普拉斯方差 (Laplacian Variance) 分析邊緣清晰度</p>
              </div>
              <input
                type="checkbox"
                checked={config.rule_blur_enabled ?? true}
                onChange={(e) => handleChange('rule_blur_enabled', e.target.checked)}
                className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500"
              />
            </div>

            {config.rule_blur_enabled && (
              <div className="space-y-1.5 pt-2 border-t border-slate-800/80">
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">模糊清晰度門檻：</span>
                  <span className="text-amber-400 font-mono font-bold">&lt; {config.blur_threshold ?? 100} 分</span>
                </div>
                <input
                  type="range"
                  min="30"
                  max="250"
                  step="5"
                  value={config.blur_threshold ?? 100}
                  onChange={(e) => handleChange('blur_threshold', parseFloat(e.target.value))}
                  className="w-full accent-amber-500 bg-slate-800 rounded-lg h-1.5 cursor-pointer"
                />
              </div>
            )}
          </div>

          {/* 曝光不良 */}
          <div className="p-4 bg-slate-950/60 rounded-xl border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <span className="font-semibold text-slate-200 text-sm">曝光不良檢查 (Exposure)</span>
                <p className="text-xs text-slate-500">分析亮度直方圖，找出全黑、過暗或死白過曝相片</p>
              </div>
              <input
                type="checkbox"
                checked={config.rule_exposure_enabled ?? true}
                onChange={(e) => handleChange('rule_exposure_enabled', e.target.checked)}
                className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500"
              />
            </div>

            {config.rule_exposure_enabled && (
              <div className="grid grid-cols-2 gap-3 pt-2 border-t border-slate-800/80 text-xs">
                <div>
                  <span className="text-slate-400">過暗亮度下限：</span>
                  <span className="text-sky-400 font-mono font-bold ml-1">{config.under_exposure_threshold ?? 30}</span>
                  <input
                    type="range"
                    min="10"
                    max="60"
                    value={config.under_exposure_threshold ?? 30}
                    onChange={(e) => handleChange('under_exposure_threshold', parseFloat(e.target.value))}
                    className="w-full accent-sky-500 bg-slate-800 rounded-lg h-1.5 cursor-pointer mt-1"
                  />
                </div>
                <div>
                  <span className="text-slate-400">過曝亮度上限：</span>
                  <span className="text-amber-400 font-mono font-bold ml-1">{config.over_exposure_threshold ?? 225}</span>
                  <input
                    type="range"
                    min="200"
                    max="250"
                    value={config.over_exposure_threshold ?? 225}
                    onChange={(e) => handleChange('over_exposure_threshold', parseFloat(e.target.value))}
                    className="w-full accent-amber-500 bg-slate-800 rounded-lg h-1.5 cursor-pointer mt-1"
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Section 3: 多線程並行處理設定 (Multi-threading Runners) */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
        <div className="border-b border-slate-800 pb-4">
          <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
            <Cpu className="w-5 h-5 text-sky-400" />
            <span>並行效能與線程設定（Multi-threading Runners）</span>
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            配置相片分析工作台四個獨立階段的背景並行線程數。適當調高可巨幅縮短處理數千至數萬張相片的時間。
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[
            {
              key: 'hash_runners',
              name: '檔案雜湊 (Hash 階段)',
              desc: '計算檔案 SHA-256 雜湊值（I/O 密集型）。',
              rec: '推薦：CPU 核心數一半 (例如 4~8)',
              icon: <Zap className="w-4 h-4 text-sky-400" />,
            },
            {
              key: 'basic_runners',
              name: '縮圖與基本特徵 (Basic 階段)',
              desc: '生成縮圖快取與 Laplacian 清晰度/亮度計算（CPU 密集型）。',
              rec: '推薦：CPU 核心數一半 (例如 2~6)',
              icon: <Zap className="w-4 h-4 text-emerald-400" />,
            },
            {
              key: 'clip_runners',
              name: 'CLIP 語義向量 (CLIP 階段)',
              desc: 'AI 語義向量提取與零樣本分類（神經網路推理）。',
              rec: '推薦：CPU 模式 2~4；CUDA (GPU) 模式建議設為 1 (GPU 單流水線最高效)',
              icon: <Zap className="w-4 h-4 text-purple-400" />,
            },
            {
              key: 'video_runners',
              name: '影片資訊探查 (Video 階段)',
              desc: '解析影片容器時長、解析度與編碼（I/O 與解碼型）。',
              rec: '推薦：2~4',
              icon: <Zap className="w-4 h-4 text-amber-400" />,
            },
          ].map((item) => {
            const currentVal = Math.max(1, Math.min(16, Number(config[item.key] ?? 4)))
            return (
              <div key={item.key} className="p-4 bg-slate-950/60 rounded-xl border border-slate-800 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {item.icon}
                    <span className="font-semibold text-slate-200 text-sm">{item.name}</span>
                  </div>
                  <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-sky-950 text-sky-300 border border-sky-800/60">
                    ⚡ {currentVal} Runners
                  </span>
                </div>

                <p className="text-xs text-slate-400 leading-relaxed">{item.desc}</p>
                <div className="text-[11px] text-slate-500 font-mono">{item.rec}</div>

                <div className="flex items-center justify-between pt-2 border-t border-slate-800/80">
                  <span className="text-xs text-slate-400 font-medium">線程數 (1 ~ 16)：</span>
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => handleChange(item.key, Math.max(1, currentVal - 1))}
                      disabled={currentVal <= 1}
                      className="p-1 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-30 text-slate-300 transition-colors cursor-pointer"
                      title="減少 1 個線程"
                    >
                      <Minus className="w-3.5 h-3.5" />
                    </button>
                    <input
                      type="number"
                      min="1"
                      max="16"
                      value={currentVal}
                      onChange={(e) => {
                        const parsed = parseInt(e.target.value, 10)
                        if (!isNaN(parsed)) {
                          handleChange(item.key, Math.max(1, Math.min(16, parsed)))
                        }
                      }}
                      className="w-14 text-center bg-slate-900 border border-slate-700 rounded-lg text-slate-100 font-mono text-xs py-1 focus:ring-1 focus:ring-sky-500 focus:outline-none"
                    />
                    <button
                      type="button"
                      onClick={() => handleChange(item.key, Math.min(16, currentVal + 1))}
                      disabled={currentVal >= 16}
                      className="p-1 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-30 text-slate-300 transition-colors cursor-pointer"
                      title="增加 1 個線程"
                    >
                      <Plus className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Section 4: 安全與刪除偏好 */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
        <div className="border-b border-slate-800 pb-4">
          <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-purple-400" />
            <span>刪除模式與防護鐵則（Safety & Deletion）</span>
          </h3>
          <p className="text-xs text-slate-500 mt-0.5">控制實體檔案在 Process 頁執行時的處置方式</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
          <div
            onClick={() => handleChange('deletion_mode', 'trash')}
            className={`p-4 rounded-xl border cursor-pointer transition-all ${
              (config.deletion_mode ?? 'trash') === 'trash'
                ? 'bg-sky-950/40 border-sky-500 ring-1 ring-sky-500 text-slate-100'
                : 'bg-slate-950/60 border-slate-800 text-slate-400 hover:border-slate-700'
            }`}
          >
            <div className="font-bold text-sm text-sky-400 flex items-center justify-between">
              <span>資源回收筒 (預設)</span>
              {(config.deletion_mode ?? 'trash') === 'trash' && <CheckCircle2 className="w-4 h-4 text-sky-400" />}
            </div>
            <p className="mt-2 text-slate-400 leading-relaxed">
              使用 Windows 資源回收筒安全刪除，隨時可至桌面回收筒救回，兼具安全性與操作彈性。
            </p>
          </div>

          <div
            onClick={() => handleChange('deletion_mode', 'quarantine')}
            className={`p-4 rounded-xl border cursor-pointer transition-all ${
              config.deletion_mode === 'quarantine'
                ? 'bg-sky-950/40 border-sky-500 ring-1 ring-sky-500 text-slate-100'
                : 'bg-slate-950/60 border-slate-800 text-slate-400 hover:border-slate-700'
            }`}
          >
            <div className="font-bold text-sm text-emerald-400 flex items-center justify-between">
              <span>隔離資料夾模式</span>
              {config.deletion_mode === 'quarantine' && <CheckCircle2 className="w-4 h-4 text-emerald-400" />}
            </div>
            <p className="mt-2 text-slate-400 leading-relaxed">
              將待刪檔案移入 <code>_quarantine/</code> 資料夾，保留完整路徑結構，適合需要一鍵復原之場景。
            </p>
          </div>

          <div
            onClick={() => handleChange('deletion_mode', 'permanent')}
            className={`p-4 rounded-xl border cursor-pointer transition-all ${
              config.deletion_mode === 'permanent'
                ? 'bg-rose-950/40 border-rose-500 ring-1 ring-rose-500 text-slate-100'
                : 'bg-slate-950/60 border-slate-800 text-slate-400 hover:border-slate-700'
            }`}
          >
            <div className="font-bold text-sm text-rose-400 flex items-center justify-between">
              <span>永久刪除 (不可逆)</span>
              {config.deletion_mode === 'permanent' && <CheckCircle2 className="w-4 h-4 text-rose-400" />}
            </div>
            <p className="mt-2 text-slate-400 leading-relaxed">
              直接從磁碟清除檔案，不可復原。執行時需在對話框額外輸入二次確認字串。
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
