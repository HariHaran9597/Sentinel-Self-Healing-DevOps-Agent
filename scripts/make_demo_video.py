#!/usr/bin/env python3
"""Build a 60-second visual walkthrough from the checked-in evaluation record."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "results" / "sentinel-comparison.json"
OUTPUT = ROOT / "docs" / "sentinel-60s.mp4"
WIDTH, HEIGHT, FPS = 1280, 720, 24
BG = (13, 17, 23)
PANEL = (22, 27, 34)
WHITE = (240, 246, 252)
MUTED = (139, 148, 158)
BLUE = (88, 166, 255)
GREEN = (63, 185, 80)
YELLOW = (210, 153, 34)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    candidates = [Path("C:/Windows/Fonts") / name for name in names]
    candidates += [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    raise RuntimeError("Install a system TrueType font before generating the video")


def wrap(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=face)[2] <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def card(title: str, subtitle: str, bullets: list[tuple[str, str]], accent=BLUE, number="") -> np.ndarray:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((54, 42, WIDTH - 54, HEIGHT - 42), radius=22, fill=PANEL, outline=(48, 54, 61), width=2)
    draw.rounded_rectangle((54, 42, WIDTH - 54, 52), radius=5, fill=accent)
    draw.text((92, 84), "SENTINEL  /  SELF-HEALING DEVOPS", font=font(18, True), fill=BLUE)
    if number:
        draw.text((WIDTH - 170, 82), number, font=font(18), fill=MUTED)
    draw.text((92, 138), title, font=font(42, True), fill=WHITE)
    y = 204
    for line in wrap(draw, subtitle, font(21), WIDTH - 190):
        draw.text((94, y), line, font=font(21), fill=MUTED)
        y += 31
    y += 27
    for heading, detail in bullets:
        draw.rounded_rectangle((94, y + 5, 103, y + 43), radius=4, fill=accent)
        draw.text((126, y), heading, font=font(24, True), fill=WHITE)
        y += 39
        for line in wrap(draw, detail, font(19), WIDTH - 240):
            draw.text((126, y), line, font=font(19), fill=MUTED)
            y += 28
        y += 18
    draw.line((92, HEIGHT - 106, WIDTH - 92, HEIGHT - 106), fill=(48, 54, 61), width=1)
    draw.text((92, HEIGHT - 88), "CONTROLLED LOCAL FAULT FIXTURES  ·  NOT PRODUCTION RELIABILITY EVIDENCE", font=font(14, True), fill=YELLOW)
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def main() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    sentinel = next(row for row in report["summaries"] if row["strategy"] == "sentinel_runbook")
    baseline = next(row for row in report["summaries"] if row["strategy"] == "restart_only")
    dictionary = next(row for row in report["summaries"] if row["strategy"] == "static_dictionary")
    semantic = next(row for row in report["summaries"] if row["strategy"] == "semantic_retrieval")
    def seconds(value: float | None) -> str:
        return "n/a" if value is None else f"{value / 1000:.2f}s"

    latency_line = (
        f"{seconds(sentinel['recovery_injection_to_first_healthy_ms_median'])} median · "
        f"{seconds(sentinel['recovery_injection_to_first_healthy_ms_p95_nearest_rank'])} p95"
    )
    scenes = [
        card("A fault is injected.", "Sentinel watches a disposable payments API and its ledger dependency.", [
            (f"{len(report['faults'])} scoped fault fixtures", "Bad release rollback · no-code paraphrased config · impacting unknown escalation."),
            ("Matched comparison", f"Same {report['method']['replicates_per_scenario']} seeds across four remediation strategies."),
        ], BLUE, "01 / 06"),
        card("The controls start simple.", "A restart-only baseline and a static error-code dictionary use the same allowlisted executor.", [
            (f"Restart-only recovery: {baseline['correct_sustained_recoveries']}/{baseline['expected_recovery_trials']}", "A restart cannot roll back the bad release or infer the no-code paraphrased fault."),
            (f"Dictionary recovery: {dictionary['correct_sustained_recoveries']}/{dictionary['expected_recovery_trials']}", "Exact error codes map directly to fixed actions; unknown codes abstain."),
        ], YELLOW, "02 / 06"),
        card("Does retrieval add value?", "ChromaDB matches the log evidence to a runbook, then the allowlisted executor applies its action.", [
            (f"Semantic-only recovery: {semantic['correct_sustained_recoveries']}/{semantic['expected_recovery_trials']}", "Bypasses LangGraph and the LLM to isolate retrieval behavior."),
            ("No-code paraphrase", "The retry configuration fixture has no ERR_* signature."),
        ], BLUE, "03 / 06"),
        card("Sentinel uses the same evidence.", "The full LangGraph workflow retrieves a runbook, diagnoses, and proposes an action.", [
            ("Human approval stays in the loop", "The benchmark driver auto-approves proposals; that timing is not counted as human review."),
            ("Actions are allowlisted", "Config revert · start dependency · rollback release. No arbitrary shell execution."),
        ], BLUE, "04 / 06"),
        card("Recovery is measured outside Sentinel.", "The host evaluator probes the separate /payments/status business endpoint.", [
            (f"{report['method']['sustained_health_seconds']}s of continuous health", "Recovery counts only after the independent probe remains healthy for the full soak."),
            (f"{report['method']['observation_window_seconds']}s equal maximum window", "Every strategy shares the same injection-relative observation limit."),
        ], GREEN, "05 / 06"),
        card("Semantic retrieval ties Sentinel.", "Both recovered all 10 expected-recovery trials in this small controlled fixture set.", [
            (f"Sentinel recovery: {sentinel['correct_sustained_recoveries']}/{sentinel['expected_recovery_trials']} · {latency_line}", "Latency is injection to first independent probe success; n=10, p95 equals max."),
            (f"Sentinel safe escalation: {sentinel['correct_sentinel_escalations']}/{sentinel['expected_safe_handling_trials']}", "Retrieval-only controls abstained safely; see all trial records and Wilson intervals in JSON."),
        ], BLUE, "06 / 06"),
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(OUTPUT), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT))
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not initialize MP4 writer")
    try:
        for scene in scenes:
            for _ in range(FPS * 10):
                writer.write(scene)
    finally:
        writer.release()
    print(f"wrote {OUTPUT} ({len(scenes) * 10}s)")


if __name__ == "__main__":
    main()
