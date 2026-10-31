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
    scenes = [
        card("A fault is injected.", "Sentinel watches a disposable payments API and its ledger dependency.", [
            ("Four seeded scenarios", "Persistent invalid config · dependency outage · bad release · unknown signal"),
            ("Matched comparison", "Same five seeds for restart-only and Sentinel runbook strategies."),
        ], BLUE, "01 / 06"),
        card("The baseline is simple.", "Restart-only restarts the payments container and has no diagnosis or conditional action.", [
            ("It cannot fix persistent state", "Bad config and bad release survive process restart."),
            ("It cannot restore a dependency", "The ledger runs in a separate labeled container."),
        ], YELLOW, "02 / 06"),
        card("Sentinel uses evidence.", "A log monitor opens an incident; LangGraph retrieves a matching runbook and proposes a specific action.", [
            ("Human approval stays in the loop", "The benchmark driver auto-approves proposals; that timing is not counted as human review."),
            ("Actions are allowlisted", "Config revert · start dependency · rollback release. No arbitrary shell execution."),
        ], BLUE, "03 / 06"),
        card("Recovery is measured outside Sentinel.", "The host evaluator probes the separate /payments/status business endpoint.", [
            ("30 seconds of continuous health", "An incident is counted recovered only after the independent probe stays healthy."),
            ("Unknown signals should not trigger action", "Sentinel should escalate; restart-only is expected to restart unnecessarily."),
        ], GREEN, "04 / 06"),
        card(f"{sentinel['sustained_recoveries']}/{sentinel['actionable_trials']} sustained recoveries", "Measured against the matched restart-only baseline.", [
            (f"Sentinel: {sentinel['successful_recovery_injection_to_independent_probe_ms_median'] / 1000:.2f}s median · {sentinel['successful_recovery_injection_to_independent_probe_ms_p95_nearest_rank'] / 1000:.2f}s p95", "Fault injection to first external probe success; all 15 recoveries then passed the 30-second soak."),
            (f"Restart-only: {baseline['sustained_recoveries']}/{baseline['actionable_trials']} sustained recoveries", "All three actionable faults remained broken after a restart."),
        ], GREEN, "05 / 06"),
        card("Five seeds per scenario.", "40 matched trials on one local Docker Compose lab; the JSON contains every trial.", [
            ("Unknown control", f"Sentinel escalated {sentinel['correct_escalations_on_unknown']}/5 with zero actions; Wilson 95% CI for escalation: 56.6–100%."),
            ("Scope and uncertainty", "Small repeated sample; broad confidence bounds; no production adoption, SLO or customer savings claim."),
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
