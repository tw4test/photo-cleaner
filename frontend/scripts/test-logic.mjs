// 播放器純邏輯的測試（按鍵對應、時間格式、倍速…）。不需要新增套件：用專案已有的 typescript 轉譯後直接在 Node 執行。
//   cd frontend; npm run test:logic
import assert from 'node:assert/strict'
import fs from 'node:fs'
import ts from 'typescript'

const load = async (rel) => {
  const file = new URL(rel, import.meta.url)
  const js = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
  }).outputText
  return import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'))
}
const L = await load('../src/components/video/videoPlayerLogic.ts')
const R = await load('../src/components/video/videoReviewLogic.ts')
const P = await load('../src/components/common/progressLogic.ts')
const SNAPS = JSON.parse(fs.readFileSync(new URL('./fixtures/pipeline-snapshots.json', import.meta.url), 'utf8'))

let n = 0
const t = (name, fn) => { fn(); n++; console.log('ok', name) }
const k = (key, extra = {}) => L.resolvePlayerKey({ key, ...extra })

t('arrows seek, shift+arrows prev/next', () => {
  assert.deepEqual(k('ArrowLeft'), { type: 'seekBy', seconds: -5 })
  assert.deepEqual(k('ArrowRight'), { type: 'seekBy', seconds: 5 })
  assert.deepEqual(k('ArrowLeft', { shiftKey: true }), { type: 'prev' })
  assert.deepEqual(k('ArrowRight', { shiftKey: true }), { type: 'next' })
})
t('PageUp/PageDown prev/next', () => {
  assert.deepEqual(k('PageUp'), { type: 'prev' })
  assert.deepEqual(k('PageDown'), { type: 'next' })
})
t('D / K / R / I / Esc / Enter are NOT consumed by the player (review keeps them)', () => {
  for (const key of ['d', 'D', 'k', 'K', 'r', 'R', 'i', 'I', 'Escape', 'Enter', 'z']) assert.equal(k(key), null, key)
})
t('ctrl/meta/alt combos are never consumed (Ctrl+Z undo, Ctrl+A...)', () => {
  for (const mod of ['ctrlKey', 'metaKey', 'altKey']) {
    assert.equal(k(' ', { [mod]: true }), null)
    assert.equal(k('ArrowLeft', { [mod]: true }), null)
    assert.equal(k('z', { [mod]: true }), null)
    assert.equal(k('f', { [mod]: true }), null)
  }
})
t('play / mute / fullscreen / theater / pip / j l', () => {
  assert.deepEqual(k(' '), { type: 'togglePlay' })
  assert.deepEqual(k('m'), { type: 'toggleMute' })
  assert.deepEqual(k('M'), { type: 'toggleMute' })
  assert.deepEqual(k('f'), { type: 'toggleFullscreen' })
  assert.deepEqual(k('t'), { type: 'toggleTheater' })
  assert.deepEqual(k('p'), { type: 'togglePip' })
  assert.deepEqual(k('j'), { type: 'seekBy', seconds: -10 })
  assert.deepEqual(k('L'), { type: 'seekBy', seconds: 10 })
})
t('volume, speed, percent, home', () => {
  assert.deepEqual(k('ArrowUp'), { type: 'volumeBy', delta: 0.05 })
  assert.deepEqual(k('ArrowDown'), { type: 'volumeBy', delta: -0.05 })
  assert.deepEqual(k(','), { type: 'speedStep', direction: -1 })
  assert.deepEqual(k('<', { shiftKey: true }), { type: 'speedStep', direction: -1 })
  assert.deepEqual(k('.'), { type: 'speedStep', direction: 1 })
  assert.deepEqual(k('>', { shiftKey: true }), { type: 'speedStep', direction: 1 })
  assert.deepEqual(k('0'), { type: 'seekPercent', percent: 0 })
  assert.deepEqual(k('5'), { type: 'seekPercent', percent: 50 })
  assert.deepEqual(k('9'), { type: 'seekPercent', percent: 90 })
  assert.deepEqual(k('Home'), { type: 'seekStart' })
})
t('formatClock', () => {
  assert.equal(L.formatClock(0), '0:00')
  assert.equal(L.formatClock(2.9), '0:02')
  assert.equal(L.formatClock(65), '1:05')
  assert.equal(L.formatClock(600), '10:00')
  assert.equal(L.formatClock(3725), '1:02:05')
  assert.equal(L.formatClock(36000), '10:00:00')
  for (const bad of [null, undefined, NaN, Infinity, -1]) assert.equal(L.formatClock(bad), '--:--')
})
t('stepSpeed walks the list and stops at both ends', () => {
  assert.equal(L.stepSpeed(1, 1), 1.25)
  assert.equal(L.stepSpeed(1, -1), 0.75)
  assert.equal(L.stepSpeed(2, 1), 2)
  assert.equal(L.stepSpeed(0.25, -1), 0.25)
  assert.equal(L.stepSpeed(1.1, 1), 1.25)      // 不在清單上：先取最近的 1，再往上
  assert.equal(L.stepSpeed(3, -1), 1.75)       // 超出範圍：最近的是 2，再往下
})
t('fractionFromPointer clamps', () => {
  assert.equal(L.fractionFromPointer(150, 100, 200), 0.25)
  assert.equal(L.fractionFromPointer(50, 100, 200), 0)
  assert.equal(L.fractionFromPointer(999, 100, 200), 1)
  assert.equal(L.fractionFromPointer(150, 100, 0), 0)
})
t('effectiveDuration falls back when video.duration is Infinity / NaN / 0', () => {
  assert.equal(L.effectiveDuration(30, 99), 30)
  assert.equal(L.effectiveDuration(Infinity, 99), 99)
  assert.equal(L.effectiveDuration(NaN, 12.5), 12.5)
  assert.equal(L.effectiveDuration(0, null), 0)
  assert.equal(L.effectiveDuration(NaN, undefined), 0)
})
t('resolution helpers', () => {
  assert.equal(L.formatResolution(1920, 1080), '1920×1080')
  assert.equal(L.formatResolution(null, 1080), null)
  assert.equal(L.resolutionLabel(3840, 2160), '4K')
  assert.equal(L.resolutionLabel(1920, 1080), '1080p')
  assert.equal(L.resolutionLabel(1080, 1920), '1080p')   // 直式
  assert.equal(L.resolutionLabel(1280, 720), '720p')
  assert.equal(L.resolutionLabel(640, 480), '480p')
  assert.equal(L.resolutionLabel(320, 240), 'SD')
  assert.equal(L.resolutionLabel(null, null), null)
})
t('describeMediaError', () => {
  assert.match(L.describeMediaError(4), /格式/)
  assert.match(L.describeMediaError(2), /NAS/)
  assert.match(L.describeMediaError(undefined), /無法播放/)
})

t('先試播原檔：偵測沒有 error 事件的失敗（沒有 HEVC 解碼器的瀏覽器只播出聲音、沒有畫面）', () => {
  const d = L.diagnoseVideoElement
  // 這是在沙盒 Chromium 實測 HEVC 檔得到的真實數值：能讀、有聲音（currentTime 前進）、videoWidth = 0、解碼畫格 = 0、沒有 error
  assert.match(d({ videoWidth: 0, decodedFrames: 0, currentTime: 1.74 }, 'hevc'), /沒有影像輸出（hevc）/)
  assert.match(d({ videoWidth: 0, decodedFrames: null, currentTime: 1.2 }), /只播得出聲音/)         // 瀏覽器不提供畫格數時，只看 videoWidth
  assert.match(d({ videoWidth: 1080, decodedFrames: 0, currentTime: 2 }), /沒有解出任何畫面/)
  // 正常播放不會誤判
  assert.equal(d({ videoWidth: 1080, decodedFrames: 45, currentTime: 1.8 }), null)
  assert.equal(d({ videoWidth: 1080, decodedFrames: null, currentTime: 1.8 }), null)
  // 剛開始載入（還沒播出東西）不判斷
  assert.equal(d({ videoWidth: 0, decodedFrames: 0, currentTime: 0 }), null)
  assert.equal(d({ videoWidth: 0, decodedFrames: 0, currentTime: 0.3 }), null)
})

/* ── 審核頁（videoReviewLogic）── */
const ACTIONS = {
  candidate: [{ target: 'kept', key: 'K' }, { target: 'queued', key: 'D' }],
  queued: [{ target: 'candidate', key: 'R' }, { target: 'kept', key: 'K' }],
  kept: [{ target: 'candidate', key: 'R' }, { target: 'queued', key: 'D' }],
}
t('D / K / R 對應（只認目前狀態有提供的動作）', () => {
  assert.equal(R.matchActionKey({ key: 'd' }, ACTIONS.candidate), 'queued')
  assert.equal(R.matchActionKey({ key: 'D' }, ACTIONS.candidate), 'queued')
  assert.equal(R.matchActionKey({ key: 'k' }, ACTIONS.candidate), 'kept')
  assert.equal(R.matchActionKey({ key: 'r' }, ACTIONS.candidate), null)       // 候選頁沒有 R
  assert.equal(R.matchActionKey({ key: 'r' }, ACTIONS.queued), 'candidate')
  assert.equal(R.matchActionKey({ key: 'd' }, ACTIONS.queued), null)          // 待刪頁沒有 D
  assert.equal(R.matchActionKey({ key: 'd' }, ACTIONS.kept), 'queued')
  assert.equal(R.matchActionKey({ key: 'x' }, ACTIONS.candidate), null)
})
t('Ctrl / Meta / Alt + D/K/R 不算動作（Ctrl+Z 等留給外層）', () => {
  for (const m of ['ctrlKey', 'metaKey', 'altKey']) for (const key of ['d', 'k', 'r', 'z']) assert.equal(R.matchActionKey({ key, [m]: true }, ACTIONS.kept), null)
})
t('D/K/R 與播放器的按鍵完全不重疊（外層先處理也不會搶到播放器的鍵）', () => {
  const taken = ['d', 'k', 'r', 'D', 'K', 'R']
  for (const key of taken) assert.equal(L.resolvePlayerKey({ key }), null, key)
  for (const a of Object.values(ACTIONS).flat()) assert.equal(L.resolvePlayerKey({ key: a.key }), null, a.key)
})
t('影片分數是固定值：不顯示分數', () => {
  assert.equal(R.isFixedScoreRule('video_same_size'), true)
  assert.equal(R.isFixedScoreRule('video_tiny'), true)
  for (const r of ['screenshot', 'document', 'meme', 'mistouch', 'exact_dup', 'near_dup', 'blur', '', null, undefined]) assert.equal(R.isFixedScoreRule(r), false, String(r))
})
t('影片分類不能依分數排序；別的分類不受影響', () => {
  assert.equal(R.sortOffersScore('video'), false)
  for (const c of ['all', 'screenshot', 'exact_dup', 'blur']) assert.equal(R.sortOffersScore(c), true)
  assert.equal(R.resolveSort('video', 'score', 'size'), 'size')               // 在別處選過「分數」→ 影片分類退回預設
  assert.equal(R.resolveSort('video', null, 'size'), 'size')
  assert.equal(R.resolveSort('video', 'path', 'size'), 'path')                // 其他排序照常
  assert.equal(R.resolveSort('video', 'group', 'size'), 'group')
  assert.equal(R.resolveSort('screenshot', 'score', 'score'), 'score')
  assert.equal(R.resolveSort('all', 'score', 'group'), 'score')
  assert.equal(R.resolveSort('blur', null, 'size'), 'size')
})

/* ── 進度卡片（progressLogic）：用「真實跑一次流水線」錄下的狀態序列（scripts/fixtures/pipeline-snapshots.json）── */
const running = SNAPS.filter((s) => s.is_running)
const doneSnap = SNAPS[SNAPS.length - 1]
t('真實流水線：步驟只會往前、不會倒退，且依序經過六個步驟', () => {
  const idx = running.map((s) => P.pipelineStepIndex(s))
  for (let i = 1; i < idx.length; i += 1) assert.ok(idx[i] >= idx[i - 1], `倒退：${idx.join(',')}`)
  assert.deepEqual([...new Set(idx)], [0, 1, 2, 3, 4, 5])
  assert.equal(P.PIPELINE_STEP_KEYS.length, 6)
})
t('真實流水線：每個狀態對應到正確的步驟', () => {
  const want = { discovering: 0, processing: 0, hash: 1, basic: 2, clip: 3, video: 4, evaluating_rules: 5 }
  for (const s of running) {
    // 掃描結束、分析剛開始（pipeline_step = analyze，但還沒有 current_stage）要算 Hash（1）
    const expected = s.pipeline_step === 'analyze' && !s.current_stage ? 1 : want[s.stage]
    assert.equal(P.pipelineStepIndex(s), expected, `${s.stage}/${s.current_stage}/${s.pipeline_step}`)
  }
})
t('流水線完成：六個步驟全部完成（= 6），不再有進行中的步驟', () => {
  assert.equal(doneSnap.stage, 'completed')
  assert.equal(P.pipelineStepIndex(doneSnap), 6)
  assert.equal(P.pipelineStepIndex({ stage: 'completed' }), 6)
  assert.equal(P.pipelineStepIndex({ pipeline_step: 'done' }), 6)
})
t('流水線失敗 / 取消：停在出錯的那一步（後端不會清掉 pipeline_step 與 current_stage）', () => {
  assert.equal(P.pipelineStepIndex({ stage: 'error', pipeline_step: 'analyze', current_stage: 'clip' }), 3)
  assert.equal(P.pipelineStepIndex({ stage: 'error', pipeline_step: 'scan' }), 0)
  assert.equal(P.pipelineStepIndex({ stage: 'error', pipeline_step: 'evaluate', current_stage: 'video' }), 5)
  assert.equal(P.pipelineStepIndex({}), 0)
})
t('單獨分析的階段對應', () => {
  assert.equal(P.analyzeStepIndex({ stage: 'hash', current_stage: 'hash' }), 0)
  assert.equal(P.analyzeStepIndex({ stage: 'clip', current_stage: 'clip' }), 2)
  assert.equal(P.analyzeStepIndex({ stage: 'video', current_stage: 'video' }), 3)
  assert.equal(P.analyzeStepIndex({ stage: 'pending' }), 0)
  assert.equal(P.analyzeStepIndex({ stage: 'completed', current_stage: 'video' }), 4)
})
t('完成後不再顯示最後階段的 N / N；進行中才顯示', () => {
  assert.equal(doneSnap.total_files, 2)          // 後端的確留著最後一個階段（video）的 2 / 2
  assert.equal(P.shouldShowCounts(doneSnap), false)
  assert.equal(P.shouldShowCounts({ job_type: 'analyze', stage: 'completed', total_files: 7, is_running: false }), false)
  assert.equal(P.shouldShowCounts(running.find((s) => s.stage === 'clip' && s.total_files > 0 && s.processed_files > 0)), true)
  assert.equal(P.shouldShowCounts({ job_type: 'pipeline', is_running: true, total_files: 0 }), false)   // 沒有總數就不顯示 0 / 0
  // 掃描 / 補算縮圖完成：總數就是這次處理的筆數 → 仍顯示
  assert.equal(P.shouldShowCounts({ job_type: 'scan', stage: 'completed', is_running: false, total_files: 9 }), true)
  assert.equal(P.shouldShowCounts({ job_type: 'rebuild_thumbs', stage: 'completed', is_running: false, total_files: 5 }), true)
})
t('進度條標籤：流水線進行中是「目前步驟」', () => {
  assert.equal(P.progressLabel({ job_type: 'pipeline', is_running: true }), '目前步驟')
  assert.equal(P.progressLabel({ job_type: 'pipeline', is_running: false }), '進度')
  assert.equal(P.progressLabel({ job_type: 'analyze', is_running: true }), '進度')
})
t('真實流水線的完成摘要：依各階段回傳欄位（hash: done / not_needed；basic: done；clip / video: processed）', () => {
  const tiles = P.analysisTiles(doneSnap.stage_stats)
  assert.deepEqual(tiles.map((x) => x.key), ['hash', 'basic', 'clip', 'video'])
  const by = Object.fromEntries(tiles.map((x) => [x.key, x]))
  const st = doneSnap.stage_stats
  assert.equal(by.hash.value, `${st.hash.done} 筆`)
  assert.equal(by.hash.sub, `免雜湊 ${st.hash.not_needed} 筆`)
  assert.equal(by.basic.value, `${st.basic.done} 筆`)
  // clip / video 的 done 是「本來就完成」的數量（這次是 0），要用 processed（這次真的處理的）
  assert.equal(st.clip.done, 0); assert.equal(st.clip.processed, 7)
  assert.equal(by.clip.value, '7 筆')
  assert.equal(st.video.done, 0); assert.equal(st.video.processed, 2)
  assert.equal(by.video.value, '2 筆')
})
t('摘要的各種情況：免雜湊、只跑部分階段、失敗、階段整個出錯、缺欄位、空物件', () => {
  const a = P.analysisTiles({ hash: { processed: 20, done: 5, not_needed: 15, failed: 0 }, video: { processed: 3, done: 40, failed: 1 } })
  assert.deepEqual(a.map((x) => x.key), ['hash', 'video'])
  assert.equal(a[0].value, '5 筆'); assert.equal(a[0].sub, '免雜湊 15 筆'); assert.equal(a[0].failed, undefined)
  assert.equal(a[1].value, '3 筆'); assert.equal(a[1].failed, 1)
  const e = P.analysisTiles({ clip: { error: 'CUDA out of memory' } })
  assert.equal(e[0].error, 'CUDA out of memory')
  assert.equal(P.analysisTiles({ basic: {} })[0].value, '0 筆')       // 缺欄位 → 0，不是 NaN / undefined
  assert.deepEqual(P.analysisTiles({}), [])
  assert.deepEqual(P.analysisTiles(null), [])
  assert.equal(P.analysisTiles({ hash: { done: 5 } })[0].sub, undefined)
})
t('完成卡片的區塊：後端預設的空物件 {} 不算有資料（以前會顯示「+ 筆」與空的分析格）', () => {
  const empty = { scan_summary: {}, stage_stats: {}, evaluate_result: {}, new_candidates_count: 0 }
  assert.deepEqual(P.summaryBlocks({ ...empty, job_type: 'scan' }), { scan: false, analyze: false, evaluate: false })
  assert.deepEqual(P.summaryBlocks({ ...empty, job_type: 'analyze' }), { scan: false, analyze: false, evaluate: false })
  assert.deepEqual(P.summaryBlocks({ ...empty, job_type: 'scan', scan_summary: { added: 0, modified: 0, missing: 0, unchanged: 5 } }), { scan: true, analyze: false, evaluate: false })
  assert.deepEqual(P.summaryBlocks({ ...empty, job_type: 'analyze', stage_stats: { hash: { done: 1 } } }), { scan: false, analyze: true, evaluate: false })
  assert.deepEqual(P.summaryBlocks({ ...empty, job_type: 'evaluate', evaluate_result: { skipped: false, added: 2, removed: 0, total: 9 } }), { scan: false, analyze: false, evaluate: true })
  assert.deepEqual(P.summaryBlocks({ ...empty, job_type: 'rebuild_thumbs' }), { scan: false, analyze: false, evaluate: false })
  // 流水線：三個區塊都有（掃描 + 分析 + 評估）
  assert.deepEqual(P.summaryBlocks(doneSnap), { scan: true, analyze: true, evaluate: true })
  // 流水線在分析階段失敗：沒有分析 / 評估摘要
  assert.deepEqual(P.summaryBlocks({ ...empty, job_type: 'pipeline', stage: 'error' }), { scan: false, analyze: false, evaluate: false })
})
t('評估摘要', () => {
  assert.deepEqual(P.evaluateSummary(doneSnap), { skipped: false, total: 8, added: 8, removed: 0 })
  assert.deepEqual(P.evaluateSummary({ evaluate_result: { skipped: true, total: 12 } }), { skipped: true, total: 12, added: 0, removed: 0 })
  assert.deepEqual(P.evaluateSummary({ evaluate_result: {}, new_candidates_count: 4 }), { skipped: false, total: 4, added: 0, removed: 0 })
  assert.equal(P.evaluateSummary({ evaluate_result: {}, new_candidates_count: 0 }), null)
})
t('頁面底部留白 = 卡片高度 + 卡片離底部的距離 + 緩衝；沒有卡片時只留基本值', () => {
  assert.equal(P.pageBottomPadding(0), P.BASE_PAGE_BOTTOM)
  assert.equal(P.pageBottomPadding(-5), P.BASE_PAGE_BOTTOM)
  assert.equal(P.pageBottomPadding(NaN), P.BASE_PAGE_BOTTOM)
  assert.equal(P.pageBottomPadding(300), 300 + P.CARD_BOTTOM_OFFSET + P.CARD_BREATHING)
  assert.equal(P.pageBottomPadding(120.2), 121 + P.CARD_BOTTOM_OFFSET + P.CARD_BREATHING)
  assert.ok(P.pageBottomPadding(10) >= P.BASE_PAGE_BOTTOM)
  assert.ok(P.pageBottomPadding(300) > P.pageBottomPadding(100))     // 卡片收合變矮 → 留白跟著變少
})

console.log(`\n${n} groups passed`)
