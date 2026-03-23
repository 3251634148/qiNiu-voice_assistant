# -*- coding: utf-8 -*-
"""离线 OCR 对照实验：对企业微信 chat_header_verify 截图用不同 ROI 参数做 OCR。

目标：确认当前 ROI (0.42, 0.10, 0.56, 0.12) 是否覆盖了标题文字"顾老师"，
以及哪组 ROI 能稳定识别到目标文本。

用法:
    cd voice_assistant
    python test_scripts/debug_wecom_header_ocr_roi.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend_py"))

from services.macos_ui_automation import MacOSUIAutomation, OcrBox

# ── 配置 ──────────────────────────────────────────────────────────
SCREENSHOT = os.path.expanduser(
    "~/Documents/VoiceAssistant/ui_debug/wecom_stable_send_1772358599/"
    "wecom_chat_header_verify_1772358608904.png"
)

TARGET_CONTACT = "顾老师"

OUTPUT_DIR = os.path.expanduser(
    "~/Documents/VoiceAssistant/ui_debug/wecom_header_ocr_experiment"
)

# ROI 组合：每个元素 (label, (x, y, w, h), scale, accurate)
ROI_VARIANTS = [
    # 当前生产配置
    ("current_roi_s3.2_fast", (0.42, 0.10, 0.56, 0.12), 3.2, False),
    ("current_roi_s3.2_accurate", (0.42, 0.10, 0.56, 0.12), 3.2, True),
    # 左移：覆盖从 35% 开始
    ("left_shift_roi_s3.2_fast", (0.35, 0.08, 0.30, 0.14), 3.2, False),
    ("left_shift_roi_s3.2_accurate", (0.35, 0.08, 0.30, 0.14), 3.2, True),
    # 更窄聚焦标题
    ("narrow_focus_roi_s3.2_fast", (0.36, 0.02, 0.25, 0.08), 3.2, False),
    ("narrow_focus_roi_s3.2_accurate", (0.36, 0.02, 0.25, 0.08), 3.2, True),
    # 标题区从左侧列表右边界开始
    ("from_list_edge_roi_s3.2_fast", (0.30, 0.04, 0.40, 0.10), 3.2, False),
    ("from_list_edge_roi_s3.2_accurate", (0.30, 0.04, 0.40, 0.10), 3.2, True),
    # 不同 scale
    ("current_roi_s2.0_fast", (0.42, 0.10, 0.56, 0.12), 2.0, False),
    ("current_roi_s4.0_fast", (0.42, 0.10, 0.56, 0.12), 4.0, False),
    ("left_shift_roi_s4.0_accurate", (0.35, 0.08, 0.30, 0.14), 4.0, True),
    # 无 ROI（全图）
    ("full_image_s3.2_fast", None, 3.2, False),
    ("full_image_s3.2_accurate", None, 3.2, True),
]


def save_cropped_image(image_path: str, roi: tuple, label: str, output_dir: str) -> str:
    """将 ROI 裁剪后的图像保存为 PNG，方便目视检查。"""
    from Quartz import (
        CGImageDestinationCreateWithURL,
        CGImageDestinationAddImage,
        CGImageDestinationFinalize,
    )
    from CoreFoundation import CFURLCreateWithFileSystemPath, kCFAllocatorDefault, kCFURLPOSIXPathStyle
    from LaunchServices import kUTTypePNG

    cg, w, h = MacOSUIAutomation._load_cgimage_sync(image_path)
    if roi is not None:
        cg_cropped, _, _, cw, ch = MacOSUIAutomation._crop_cgimage_sync(
            cg, image_w=w, image_h=h, roi=roi,
        )
    else:
        cg_cropped = cg

    out_path = os.path.join(output_dir, f"crop_{label}.png")
    url = CFURLCreateWithFileSystemPath(kCFAllocatorDefault, out_path, kCFURLPOSIXPathStyle, False)
    dest = CGImageDestinationCreateWithURL(url, kUTTypePNG, 1, None)
    CGImageDestinationAddImage(dest, cg_cropped, None)
    CGImageDestinationFinalize(dest)
    return out_path


def run_experiment() -> None:
    """执行所有 ROI 变体的 OCR 并汇总结果。"""
    if not os.path.isfile(SCREENSHOT):
        print(f"[ERROR] 截图不存在: {SCREENSHOT}")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    results = []

    for label, roi, scale, accurate in ROI_VARIANTS:
        print(f"\n{'='*60}")
        print(f"[EXPERIMENT] {label}")
        print(f"  ROI: {roi}, scale: {scale}, accurate: {accurate}")

        # 保存裁剪图
        try:
            crop_path = save_cropped_image(SCREENSHOT, roi, label, OUTPUT_DIR)
            print(f"  裁剪图已保存: {crop_path}")
        except Exception as e:
            crop_path = f"ERROR: {e}"
            print(f"  裁剪图保存失败: {e}")

        # 执行 OCR
        try:
            boxes = MacOSUIAutomation._ocr_image_advanced_sync(
                SCREENSHOT,
                roi=roi,
                scale=scale,
                grayscale=True,
                accurate=accurate,
                custom_words=[TARGET_CONTACT],
            )
        except Exception as e:
            print(f"  OCR 失败: {e}")
            results.append({
                "label": label,
                "roi": roi,
                "scale": scale,
                "accurate": accurate,
                "error": str(e),
            })
            continue

        # 分析结果
        box_dicts = []
        has_target = False
        best_sim = 0.0

        import difflib
        import re

        def norm(t: str) -> str:
            v = re.sub(r"\s+", "", str(t or ""))
            return v.strip().lower()

        target_norm = norm(TARGET_CONTACT)

        for b in boxes:
            text = str(getattr(b, "text", "") or "").strip()
            conf = float(getattr(b, "confidence", 0.0) or 0.0)
            t_norm = norm(text)

            # _has_any 检查
            contains_target = target_norm in t_norm

            # _best_match_box 检查
            sim = difflib.SequenceMatcher(None, target_norm, t_norm).ratio()

            if contains_target:
                has_target = True
            best_sim = max(best_sim, sim)

            box_dicts.append({
                "text": text,
                "confidence": round(conf, 4),
                "norm": t_norm,
                "contains_target": contains_target,
                "similarity": round(sim, 4),
                "x": round(float(getattr(b, "x", 0)), 1),
                "y": round(float(getattr(b, "y", 0)), 1),
                "w": round(float(getattr(b, "width", 0)), 1),
                "h": round(float(getattr(b, "height", 0)), 1),
            })

        # 打印关键信息
        print(f"  识别到 {len(boxes)} 个文本框:")
        for bd in box_dicts:
            marker = ""
            if bd["contains_target"]:
                marker = " <<<< HAS_ANY 命中!"
            elif bd["similarity"] >= 0.78:
                marker = f" <<<< BEST_MATCH 命中 (sim={bd['similarity']})"
            print(f"    [{bd['confidence']:.2f}] \"{bd['text']}\" (sim={bd['similarity']:.2f}){marker}")

        verdict = "PASS" if has_target or best_sim >= 0.78 else "FAIL"
        print(f"  结论: {verdict} (has_any={has_target}, best_sim={best_sim:.4f})")

        results.append({
            "label": label,
            "roi": roi,
            "scale": scale,
            "accurate": accurate,
            "box_count": len(boxes),
            "boxes": box_dicts,
            "has_any_hit": has_target,
            "best_similarity": round(best_sim, 4),
            "verdict": verdict,
            "crop_path": crop_path,
        })

    # 落盘汇总 JSON
    summary_path = os.path.join(OUTPUT_DIR, f"experiment_summary_{int(time.time())}.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n{'='*60}")
    print(f"[DONE] 汇总结果已保存: {summary_path}")

    # 汇总表
    print(f"\n{'='*60}")
    print("汇总表:")
    print(f"{'Label':<40} {'Verdict':<8} {'BoxCount':<10} {'HasAny':<8} {'BestSim':<10}")
    print("-" * 76)
    for r in results:
        if "error" in r:
            print(f"{r['label']:<40} {'ERROR':<8}")
        else:
            print(
                f"{r['label']:<40} {r['verdict']:<8} {r['box_count']:<10} "
                f"{r['has_any_hit']!s:<8} {r['best_similarity']:<10.4f}"
            )


if __name__ == "__main__":
    run_experiment()
