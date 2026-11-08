"""图片转 webp —— 跟 App 的 ``app/services/image_transcode.py`` 同一套口径。

为什么引擎侧就要转 webp（而不是让 App 导入时转）：

- App 的 ``to_webp`` **幂等**：已经是 webp 就原样返回，不重编码
  （``image_transcode.py:157-159``）。⇒ 引擎出 webp，App 导入时**零转码**，
  落盘字节 == 包内字节，sha256 一致，没有代际损失。
- App 的 ``IMPORT_QUALITY = 95``（``image_transcode.py:79``），导出档；
  ``DEFAULT_QUALITY = 82`` 是桌面截屏档（权衡 OCR 字框粒度），跟导入无关。
  这里用 95，对齐导入档。
- 顺带一提：webp q95 比引擎原来那套 jpeg q85 **更小**（实测 150dpi 扫描页
  94KB vs 113KB），换过去包不会变大。

**不引入新的运行时依赖声明**：Pillow 已经是 App 侧的依赖；这个子项目在自己的
``requirements.txt`` 里要 pillow。
"""

from __future__ import annotations

import io

#: webp 有损质量 —— 对齐 App 的 ``IMPORT_QUALITY``。
QUALITY = 95

#: Pillow 的 webp ``method``：0=最快 6=最小（默认 4）。跟 App 一致。
METHOD = 4


def _normalize_mode(im):  # type: ignore[no-untyped-def]
    """把任意 PIL 模式收敛到 webp 支持的模式（RGB / RGBA）。

    抄 App 的 ``image_transcode._normalize_mode``（:127-136）。
    """
    if im.mode in ("RGB", "RGBA"):
        return im
    if im.mode in ("LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        return im.convert("RGBA")
    return im.convert("RGB")


def to_webp_bytes(data: bytes, *, quality: int = QUALITY) -> bytes:
    """任意图片字节 → webp 字节（尺寸不变）。

    尺寸不变很重要：卡上的 bbox（``occurrences[].x/y/w/h``）是**原图像素坐标**，
    App 导入时按这个框在图上画高亮。换编码不能换像素尺寸。

    已经是 webp 的原样返回（幂等，跟 App 一致）。
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return data

    from PIL import Image  # 延迟导入：没装 Pillow 时也能用纯文本场景的包

    with Image.open(io.BytesIO(data)) as im:
        if getattr(im, "n_frames", 1) > 1:
            raise ValueError("动图不转（会丢帧）")
        im.load()
        out_im = _normalize_mode(im)
        buf = io.BytesIO()
        out_im.save(buf, format="WEBP", quality=quality, method=METHOD)
        return buf.getvalue()


def webp_name(jpg_name: str) -> str:
    """``images/p0012.jpg`` → ``images/p0012.webp``。"""
    if jpg_name.lower().endswith(".webp"):
        return jpg_name
    return jpg_name[: -len(".jpg")] + ".webp" if jpg_name.lower().endswith(".jpg") else (
        jpg_name.rsplit(".", 1)[0] + ".webp"
    )
