import React, { useState, useEffect } from 'react'
import { 
  Sliders, 
  ShieldCheck, 
  Sparkles, 
  RotateCcw, 
  Save, 
  CheckCircle2, 
  AlertCircle,
  Layers,
  Camera
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
            調整 AI 與規則引擎之偵測門檻、重複保留優先策略及安全刪除偏好
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

      {/* Section 1: 偵測規則開關與門檻 */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <div>
            <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
              <Camera className="w-5 h-5 text-sky-400" />
              <span>相片與影片偵測規則（Detection Rules）</span>
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">控制各類重複與缺陷檢查之開關與靈敏度門檻</p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Rule 1: 完全重複 */}
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

          {/* Rule 2: 近似重複 */}
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
                <div className="flex justify-between text-[10px] text-slate-600">
                  <span>嚴格 (較少候選)</span>
                  <span>寬鬆 (較多候選)</span>
                </div>
              </div>
            )}
          </div>

          {/* Rule 3: 模糊相片 */}
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
                <div className="flex justify-between text-[10px] text-slate-600">
                  <span>僅抓極度模糊 (30)</span>
                  <span>標準 (100)</span>
                  <span>輕微手震亦納入 (250)</span>
                </div>
              </div>
            )}
          </div>

          {/* Rule 4: 過暗與過曝 */}
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

          {/* Rule 5: 連拍多餘 */}
          <div className="p-4 bg-slate-950/60 rounded-xl border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <span className="font-semibold text-slate-200 text-sm">連拍多餘相片 (Burst Photos)</span>
                <p className="text-xs text-slate-500">時間差極短且畫面高度相似之連續拍攝，組內自動保留最清晰者</p>
              </div>
              <input
                type="checkbox"
                checked={config.rule_burst_enabled ?? true}
                onChange={(e) => handleChange('rule_burst_enabled', e.target.checked)}
                className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500"
              />
            </div>
            {config.rule_burst_enabled && (
              <div className="flex items-center justify-between text-xs pt-2 border-t border-slate-800/80">
                <span className="text-slate-400">判定時間窗口：</span>
                <span className="font-mono text-slate-200">{config.burst_time_window_seconds ?? 2.0} 秒以內</span>
              </div>
            )}
          </div>

          {/* Rule 6: 影片大小檢查 */}
          <div className="p-4 bg-slate-950/60 rounded-xl border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <span className="font-semibold text-slate-200 text-sm">影片相同大小檢查 (Video Check)</span>
                <p className="text-xs text-slate-500">依據檔案大小預篩，並以 Hash 確認是否為相同影片副本</p>
              </div>
              <input
                type="checkbox"
                checked={config.rule_video_same_size_enabled ?? true}
                onChange={(e) => handleChange('rule_video_same_size_enabled', e.target.checked)}
                className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Section 2: 重複組保留優先偏好 */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
        <div className="border-b border-slate-800 pb-4">
          <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
            <Layers className="w-5 h-5 text-emerald-400" />
            <span>重複組保留優先決策（Keeper Strategy）</span>
          </h3>
          <p className="text-xs text-slate-500 mt-0.5">當多張相片完全重複或近似時，系統自動判定「建議保留」之排序依據</p>
        </div>

        <div className="space-y-4 text-xs">
          <div className="flex items-center justify-between p-4 bg-slate-950/60 rounded-xl border border-slate-800">
            <div>
              <div className="font-semibold text-slate-200">跨路徑全庫重複比對</div>
              <div className="text-slate-500 mt-0.5">開啟時比對所有已加入之相片目錄；關閉時僅在同一個目錄內比對</div>
            </div>
            <input
              type="checkbox"
              checked={config.cross_path_duplicates ?? true}
              onChange={(e) => handleChange('cross_path_duplicates', e.target.checked)}
              className="w-4 h-4 rounded border-slate-700 bg-slate-900 text-sky-500 focus:ring-sky-500"
            />
          </div>

          <div className="p-4 bg-slate-950/60 rounded-xl border border-slate-800 space-y-2">
            <div className="font-semibold text-slate-200">系統內建自動保留仲裁優先級：</div>
            <ol className="list-decimal list-inside space-y-1 text-slate-400 font-mono">
              <li><strong>路徑優先順序</strong>（依據 Library 頁面中已登錄路徑之排序，最上方者最優先）</li>
              <li><strong>影像解析度最高</strong>（寬 × 高 乘積最大者優先保留）</li>
              <li><strong>實體檔案最大</strong>（保留細節更豐富之檔案）</li>
              <li><strong>最舊拍攝時間</strong>（最早建立或拍攝之原始版本）</li>
            </ol>
          </div>
        </div>
      </div>

      {/* Section 3: 安全與刪除偏好 */}
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

        {/* Locked Iron Rules status */}
        <div className="p-4 bg-slate-950/80 rounded-xl border border-slate-800 text-xs space-y-2">
          <div className="font-semibold text-slate-300">強制啟用的系統安全鐵則：</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-slate-400">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
              <span>「保留最後一張」防呆保護（任何重複組不得全滅）</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
              <span>配對完整性保護（Live Photo 與 RAW+JPG 同進同出）</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
              <span>Review 審核操作僅變更資料庫狀態，絕不刪檔</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
              <span>全本地離線運作，綁定 127.0.0.1</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
