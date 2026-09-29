from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import io

# TODO(GPU): 替換為 rawpy (LibRaw) GPU / 多線程高品質解碼與相機色彩設定轉換
class MockRawDecoder:
    """
    Mock RAW 檔案解碼器。
    在無 GPU / 無 rawpy 模式下，嘗試提取內嵌預覽圖或生成代表 RAW 的 Mock 影像。
    """

    RAW_EXTENSIONS = {'.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.pef', '.raf'}

    def is_raw(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in self.RAW_EXTENSIONS

    def decode_thumbnail(self, file_path: str, target_size: tuple[int, int] = (320, 240)) -> Image.Image:
        """
        生成代表 RAW 的預覽圖。若無法讀取則生成帶有 RAW 標記的 placeholder。
        """
        try:
            # 嘗試使用 Pillow 讀取 (某些 TIFF/DNG 格式原生支援)
            with Image.open(file_path) as img:
                img = img.convert("RGB")
                img.thumbnail(target_size)
                return img
        except Exception:
            # 建立 Mock RAW 點陣圖
            img = Image.new("RGB", target_size, color=(30, 41, 59))
            draw = ImageDraw.Draw(img)
            ext = Path(file_path).suffix.upper().lstrip('.')
            draw.rectangle([10, 10, target_size[0] - 10, target_size[1] - 10], outline=(56, 189, 248), width=2)
            draw.text((target_size[0] // 2 - 30, target_size[1] // 2 - 10), f"RAW ({ext})", fill=(241, 245, 249))
            return img

raw_decoder = MockRawDecoder()
