import React, { useState, useEffect } from 'react'
import { NavLink } from 'react-router-dom'
import { 
  LayoutDashboard, 
  FolderSync, 
  Sparkles, 
  Trash2, 
  History as HistoryIcon,
  Sliders,
  ShieldCheck,
  Zap,
  Cpu
} from 'lucide-react'
import { api } from '../../services/api'
import { GpuDiagnosticStatus } from '../../services/types'

export interface AppShellProps {
  children: React.ReactNode
}

export const AppShell = ({ children }: AppShellProps) => {
  const [gpuStatus, setGpuStatus] = useState<GpuDiagnosticStatus | null>(null)

  useEffect(() => {
    api.getGpuStatus().then(setGpuStatus).catch(() => {})
  }, [])

  const navItems = [
    { to: '/', label: '首頁總覽', icon: LayoutDashboard, exact: true },
    { to: '/library', label: '資料庫掃描', icon: FolderSync },
    { to: '/review', label: '審核工作台', icon: Sparkles },
    { to: '/process', label: '執行刪除', icon: Trash2 },
    { to: '/history', label: '歷史記錄', icon: HistoryIcon },
    { to: '/settings', label: '系統設定', icon: Sliders },
  ]

  return (
    <div className="flex h-screen bg-slate-950 text-slate-100 overflow-hidden">
      {/* Sidebar */}
      <aside className="w-64 border-r border-slate-800 bg-slate-900/50 flex flex-col justify-between">
        <div>
          {/* Logo / Brand */}
          <div className="p-6 border-b border-slate-800">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-sky-500/20 text-sky-400 rounded-lg border border-sky-500/30">
                <ShieldCheck className="w-6 h-6" />
              </div>
              <div>
                <h1 className="font-bold text-base text-slate-100 tracking-wide">相片瘦身</h1>
                <p className="text-xs text-slate-400">本地 AI 輔助清理工具</p>
              </div>
            </div>
          </div>

          {/* Navigation Links */}
          <nav className="p-4 space-y-1.5">
            {navItems.map((item) => {
              const Icon = item.icon
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.exact}
                  className={({ isActive }) =>
                    `flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all ${
                      isActive
                        ? 'bg-sky-600 text-white shadow-lg shadow-sky-600/30'
                        : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                    }`
                  }
                >
                  <Icon className="w-5 h-5" />
                  <span>{item.label}</span>
                </NavLink>
              )
            })}
          </nav>
        </div>

        {/* Footer info */}
        <div className="p-4 border-t border-slate-800 text-xs text-slate-500 space-y-2">
          <div>
            <div className="flex items-center justify-between mb-0.5">
              <span>運算引擎</span>
              {gpuStatus?.is_ready ? (
                <span className="text-emerald-400 font-mono flex items-center gap-1">
                  <Zap className="w-3 h-3 text-amber-300" />
                  <span>GPU ({gpuStatus.torch_cuda_version || 'CUDA'})</span>
                </span>
              ) : gpuStatus?.has_nvidia_gpu ? (
                <span className="text-purple-400 font-mono flex items-center gap-1">
                  <Cpu className="w-3 h-3 text-purple-400" />
                  <span>CPU (可升級 GPU)</span>
                </span>
              ) : (
                <span className="text-slate-400 font-mono">PyTorch (CPU)</span>
              )}
            </div>
            {gpuStatus?.has_nvidia_gpu && (
              <div className="text-[10px] text-slate-500 truncate font-mono" title={gpuStatus.gpu_name}>
                {gpuStatus.gpu_name}
              </div>
            )}
          </div>

          <div className="flex items-center justify-between pt-1 border-t border-slate-850">
            <span>主機綁定</span>
            <span className="text-slate-400 font-mono">127.0.0.1:8000</span>
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {children}
      </main>
    </div>
  )
}
