import os
from pathlib import Path
from typing import List, Dict, Any

class PairingEngine:
    """
    資產配對引擎：
    1. RAW + JPG 配對：同一目錄下同檔名，以 RAW 為主檔。
    2. Live Photo 配對：同一目錄下同檔名之 HEIC/JPG + MOV，以圖片為主檔。
    3. 單一檔案視為獨立 Asset。
    """

    def pair_files(self, scanned_files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # 分組：按 (目錄路徑, 主檔名 stem 小寫) 分群
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for f in scanned_files:
            p = Path(f["path"])
            key = (str(p.parent).lower(), p.stem.lower())
            groups.setdefault(key, []).append(f)

        assets = []
        for key, files in groups.items():
            if len(files) == 1:
                f = files[0]
                media_type = "raw" if f["is_raw"] else "video" if f["is_video"] else "photo"
                assets.append({
                    "media_type": media_type,
                    "primary_file": f,
                    "paired_files": [],
                    "size_total": f["size"],
                })
                continue

            # 多個檔案在同目錄同檔名
            raw_files = [f for f in files if f["is_raw"]]
            photo_files = [f for f in files if f["is_photo"]]
            video_files = [f for f in files if f["is_video"]]

            # 狀況 1: RAW + JPG
            if raw_files and photo_files:
                primary = raw_files[0]
                pairs = [f for f in files if f["path"] != primary["path"]]
                total_size = sum(f["size"] for f in files)
                assets.append({
                    "media_type": "raw",
                    "primary_file": primary,
                    "paired_files": pairs,
                    "size_total": total_size,
                })
            # 狀況 2: Live Photo (HEIC/JPG + MOV)
            elif photo_files and video_files:
                primary = photo_files[0]
                pairs = [f for f in files if f["path"] != primary["path"]]
                total_size = sum(f["size"] for f in files)
                assets.append({
                    "media_type": "photo",
                    "primary_file": primary,
                    "paired_files": pairs,
                    "size_total": total_size,
                })
            else:
                # 其他狀況：各自作為獨立資產
                for f in files:
                    m_type = "raw" if f["is_raw"] else "video" if f["is_video"] else "photo"
                    assets.append({
                        "media_type": m_type,
                        "primary_file": f,
                        "paired_files": [],
                        "size_total": f["size"],
                    })

        return assets

pairing_engine = PairingEngine()
