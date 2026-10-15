import React from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'

export interface SampleThumb {
  asset_id: number
  filename: string
  thumbnail_url: string
}

interface Props {
  title: string
  confirmLabel: string
  danger?: boolean
  busy?: boolean
  sample?: SampleThumb[]
  sampleLabel?: string
  onConfirm: () => void
  onCancel: () => void
  children: React.ReactNode
}

export const ConfirmDialog: React.FC<Props> = ({
  title, confirmLabel, danger, busy, sample, sampleLabel, onConfirm, onCancel, children,
}) => (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" onClick={busy ? undefined : onCancel}>
    <div
      className="flex max-h-[90vh] w-full max-w-2xl flex-col gap-4 overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 p-6 shadow-2xl"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-start gap-3">
        <div className={`rounded-xl border p-2 ${danger ? 'border-rose-500/30 bg-rose-500/10 text-rose-400' : 'border-sky-500/30 bg-sky-500/10 text-sky-400'}`}>
          <AlertTriangle className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1 space-y-2 text-sm text-slate-300">
          <h3 className="text-base font-semibold text-slate-100">{title}</h3>
          {children}
        </div>
      </div>

      {sample && sample.length > 0 && (
        <div>
          <div className="mb-1.5 text-[11px] text-slate-500">{sampleLabel ?? '隨機抽樣'}</div>
          <div className="grid grid-cols-6 gap-2">
            {sample.map((s) => (
              <div key={s.asset_id} className="aspect-square overflow-hidden rounded-lg border border-slate-800 bg-slate-950" title={s.filename}>
                <img src={s.thumbnail_url} alt={s.filename} loading="lazy" className="h-full w-full object-contain" />
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={onCancel}
          className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-700 disabled:opacity-40 cursor-pointer"
        >
          取消
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onConfirm}
          className={`flex items-center gap-1.5 rounded-lg px-4 py-2 text-xs font-semibold text-white disabled:opacity-40 cursor-pointer ${
            danger ? 'bg-rose-600 hover:bg-rose-500' : 'bg-sky-600 hover:bg-sky-500'
          }`}
        >
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {confirmLabel}
        </button>
      </div>
    </div>
  </div>
)
