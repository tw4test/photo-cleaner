# v0.6.2 – 設定頁無法捲動修正

- frontend/src/pages/Settings.tsx：最外層改為 `h-full min-h-0 overflow-y-auto`，頁面自己負責捲動
  （AppShell 的 main 不一定允許捲動）。儲存列的 sticky 也改為相對於此捲動容器。
- 其他檔案沒有變更（backend、settingsApi.ts 沿用 v0.6.1）。
