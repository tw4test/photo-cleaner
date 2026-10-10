export interface RootItem {
  id: number
  path: string
  recursive: boolean
  exclude_patterns: string
  priority: number
  last_scan_at: string | null
  available: boolean
}

export interface ScanSummary {
  added: number
  modified: number
  missing: number
  unchanged: number
}

export interface StageStats {
  pending: number
  done: number
  not_needed?: number
  failed: number
  stale?: number
}

export interface AnalysisStatus {
  stages: {
    hash: StageStats
    basic: StageStats
    clip: StageStats
    video: StageStats
  }
  clip_model: string
  clip_pretrained: string
  device: string
  runners?: {
    hash: number
    basic: number
    clip: number
    video: number
  }
}

export interface CudaInstallProgress {
  status: 'idle' | 'downloading' | 'installing' | 'verifying' | 'success' | 'failed' | 'cancelled'
  percentage: number
  message: string
  current_step?: string
  downloaded_bytes?: number
  total_bytes?: number
  speed?: string
  logs: string[]
  error?: string | null
  started_at?: number | null
  finished_at?: number | null
}

export interface GpuDiagnosticStatus {
  has_nvidia_gpu: boolean
  gpu_name: string
  driver_version: string
  max_cuda_version: string
  vram_mb: number
  torch_version: string
  torch_cuda_available: boolean
  torch_cuda_version: string | null
  is_ready: boolean
  status_code: 'cuda_ready' | 'gpu_detected_need_cuda' | 'no_nvidia_gpu'
  status_desc: string
  recommended_variant: string
  install_state: CudaInstallProgress
}

export interface AnalysisFailureItem {
  id: number
  asset_id: number
  stage: string
  error_msg: string | null
  occurred_at: string | null
  primary_path: string
  media_type: string
}

export interface ScanProgressState {
  is_scanning: boolean
  is_running?: boolean
  job_type?: string // 'idle' | 'scan' | 'analyze' | 'rebuild_thumbs' | 'evaluate'
  stage: string
  stage_label: string
  current_stage?: string
  device_used?: string
  current_runners?: number
  total_files: number
  processed_files: number
  percentage: number
  current_file: string
  speed_fps: number
  eta_seconds: number | null
  new_assets_count: number
  new_candidates_count: number
  scan_summary?: ScanSummary
  stage_stats?: Record<string, any>
  error_message: string | null
  duration_seconds: number
}

export interface DashboardStats {
  assets: {
    total: number
    photo_count: number
    raw_count: number
    video_count: number
    total_size_bytes: number
  }
  candidates: {
    total: number
    screenshot: number
    document: number
    meme: number
    mistouch: number
    exact_dup: number
    near_dup: number
    blur: number
    exposure: number
    releasable_size_bytes: number
  }
  history: {
    deleted_size_bytes: number
    deleted_batches: number
  }
}

export interface CandidateItem {
  id: number
  asset_id: number
  filename: string
  primary_path: string
  media_type: 'photo' | 'raw' | 'video'
  size_total: number
  rule: string
  rules?: string[]
  reason: string
  reasons?: string[]
  score: number
  group_id: number | null
  is_keeper: boolean
  state: 'candidate' | 'queued' | 'kept'
  thumbnail_url: string
}

export interface QueuedItem {
  asset_id: number
  filename: string
  primary_path: string
  media_type: string
  size_total: number
  paired_paths: string[]
  rule?: string
  reason?: string
  thumbnail_url: string
}

export interface QueuedSummary {
  total_count: number
  total_size_bytes: number
  items: QueuedItem[]
}

export interface DeletionResult {
  batch_id: string
  total_requested: number
  success_count: number
  failed_count: number
  deleted_size_bytes: number
  failed_items: Array<{ asset_id: number; primary_path: string; reason: string }>
}

export interface BatchSummaryItem {
  batch_id: string
  action: string
  mode: string
  total_files: number
  at: string
}

export interface ActionLogItem {
  id: number
  batch_id: string
  asset_id: number | null
  action: string
  mode: string
  original_path: string
  at: string
}
