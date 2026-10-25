"""指标汇总：从评分判定结果确定性计算 Precision / Recall / F1 / TP / FP / FN。"""
from __future__ import annotations


def compute_case_metrics(case: dict, eval_info: dict, score_info: dict) -> dict:
    """单案例指标：TP = 判定为 TP 的意见数；FP = 判定为 FP 的意见数；
    FN = 未被任何 TP 命中的 GT 数（优先用 matched_gt 集合反推，防御评分输出不完整）。"""
    gt_ids = [g["id"] for g in case.get("ground_truth", [])]
    gt_set = set(gt_ids)

    results = score_info.get("results") or []
    tp = sum(1 for r in results if r.get("verdict") == "TP")
    fp = sum(1 for r in results if r.get("verdict") == "FP")

    matched = {
        r.get("matched_gt")
        for r in results
        if r.get("verdict") == "TP" and r.get("matched_gt") in gt_set
    }
    unmatched_declared = [g for g in score_info.get("unmatched_gt", []) if g in gt_set]
    fn = len(gt_set - matched) if gt_set else 0
    if gt_set and len(matched) + len(unmatched_declared) == len(gt_set):
        # 评分器声明的 unmatched 与 matched 反推一致，取两者并集更稳妥
        fn = len((gt_set - matched) | set(unmatched_declared))

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "case_id": case["id"],
        "gt_count": len(gt_ids),
        "finding_count": len(eval_info.get("findings") or []),
        "output_valid": eval_info.get("output_valid", False),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "is_clean_case": case.get("is_clean_case", False),
    }


def compute_round_metrics(case_metrics: list[dict]) -> dict:
    tp = sum(m["tp"] for m in case_metrics)
    fp = sum(m["fp"] for m in case_metrics)
    fn = sum(m["fn"] for m in case_metrics)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    clean_fps = sum(m["fp"] for m in case_metrics if m["is_clean_case"])
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "clean_case_fp": clean_fps,
        "gt_total": sum(m["gt_count"] for m in case_metrics),
        "finding_total": sum(m["finding_count"] for m in case_metrics),
    }
