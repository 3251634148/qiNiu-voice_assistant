# -*- coding: utf-8 -*-
"""离线 OCR 对照实验 v2：围绕 narrow_focus 区域微调 ROI。

v1 实验已确认标题文字在 y=0.02~0.08 区域，当前生产 ROI y=0.10 太靠下。
本轮精细搜索最优 ROI 配置。

用法:
    cd voice_assistant
    source backend_py/.venv/bin/activate
    python test_scripts/debug_wecom_header_ocr_roi_v2.py
"""

import difflib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend_py"))

from services.macos_ui_automation import MacOSUIAutomation

# ── 配置 ──────────────────────────────────────────────────────────
SCREENSHOT = os.path.expanduser(
    "~/Documents/VoiceAssistant/ui_debug/wecom_stable_send_1772358599/"
    "wecom_chat_header_verify_1772358608904.png"
)

TARGET_CONTACT = "顾老师"

OUTPUT_DIR = os.path.expanduser(
    "~/Documents/VoiceAssistant/ui_debug/wecom_header_ocr_experiment_v2"
)

# ROI 变体：(label, (x, y, w, h), scale, accurate)
# 基于 v1 结论：标题在 y≈0.02, h≈0.06, x≈0.36
ROI_VARIANTS = [
    # v1 命中的配置作为 baseline
    ("baseline_v1", (0.36, 0.02, 0.25, 0.08), 3.2, False),
    # x 左移/右移
    ("x0.33", (0.33, 0.02, 0.28, 0.08), 3.2, False),
    ("x0.30", (0.30, 0.02, 0.30, 0.08), 3.2, False),
    ("x0.38", (0.38, 0.02, 0.22, 0.08), 3.2, False),
    ("x0.40", (0.40, 0.02, 0.20, 0.08), 3.2, False),
    ("x0.42", (0.42, 0.02, 0.20, 0.08), 3.2, False),
    # y 微调
    ("y0.00", (0.36, 0.00, 0.25, 0.08), 3.2, False),
    ("y0.01", (0.36, 0.01, 0.25, 0.07), 3.2, False),
    ("y0.03", (0.36, 0.03, 0.25, 0.07), 3.2, False),
    ("y0.04", (0.36, 0.04, 0.25, 0.06), 3.2, False),
    # h 微调（更高/更矮）
    ("h0.06", (0.36, 0.02, 0.25, 0.06), 3.2, False),
    ("h0.10", (0.36, 0.02, 0.25, 0.10), 3.2, False),
    # 宽幅覆盖（更宽的标题区，确保不同联系人名都能被覆盖）
    ("wide_title", (0.33, 0.01, 0.35, 0.08), 3.2, False),
    ("wider_title", (0.30, 0.01, 0.40, 0.08), 3.2, False),
    # 不同 scale
    ("baseline_s2.4", (0.36, 0.02, 0.25, 0.08), 2.4, False),
    ("baseline_s4.0", (0.36, 0.02, 0.25, 0.08), 4.0, False),
    ("wide_title_s2.4", (0.33, 0.01, 0.35, 0.08), 2.4, False),
    ("wide_title_s4.0", (0.33, 0.01, 0.35, 0.08), 4.0, False),
    # accurate 模式
    ("wide_title_accurate", (0.33, 0.01, 0.35, 0.08), 3.2, True),
    ("baseline_accurate", (0.36, 0.02, 0.25, 0.08), 3.2, True),
]


def norm(t: str) -> str:
    """归一化文本（与 wecom_ui_controller._norm_text 保持一致）。"""
    v = re.sub(r"\s+", "", str(t or ""))
    return v.strip().lower()


def run_experiment() -> None:
    """执行所有 ROI 变体的 OCR 并汇总结果。"""
    if not os.path.isfile(SCREENSHOT):
        print(f"[ERROR] 截图不存在: {SCREENSHOT}")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    target_norm = norm(TARGET_CONTACT)
    results = []

    for label, roi, scale, accurate in ROI_VARIANTS:
        print(f"\n{'='*60}")
        print(f"[EXPERIMENT] {label}")
        print(f"  ROI: {roi}, scale: {scale}, accurate: {accurate}")

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
            results.append({"label": label, "roi": roi, "scale": scale, "accurate": accurate, "error": str(e)})
            continue

        has_target = False
        best_sim = 0.0
        box_dicts = []

        for b in boxes:
            text = str(getattr(b, "text", "") or "").strip()
            conf = float(getattr(b, "confidence", 0.0) or 0.0)
            t_norm = norm(text)
            contains_target = target_norm in t_norm
            sim = difflib.SequenceMatcher(None, target_norm, t_norm).ratio()

            if contains_target:
                has_target = True
            best_sim = max(best_sim, sim)

            box_dicts.append({
                "text": text,
                "confidence": round(conf, 4),
                "contains_target": contains_target,
                "similarity": round(sim, 4),
            })

        for bd in box_dicts:
            marker = ""
            if bd["contains_target"]:
                marker = " <<<< HAS_ANY 命中!"
            elif bd["similarity"] >= 0.78:
                marker = f" <<<< BEST_MATCH (sim={bd['similarity']})"
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
        })

    # 落盘
    summary_path = os.path.join(OUTPUT_DIR, f"experiment_v2_{int(time.time())}.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n{'='*60}")
    print(f"[DONE] 汇总已保存: {summary_path}")

    # 汇总表
    print(f"\n{'Label':<30} {'Verdict':<8} {'Boxes':<6} {'HasAny':<8} {'BestSim':<10}")
    print("-" * 62)
    for r in results:
        if "error" in r:
            print(f"{r['label']:<30} ERROR")
        else:
            print(
                f"{r['label']:<30} {r['verdict']:<8} {r['box_count']:<6} "
                f"{r['has_any_hit']!s:<8} {r['best_similarity']:<10.4f}"
            )


if __name__ == "__main__":
    run_experiment()
