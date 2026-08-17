"""Phase 0 native BigFish helper.

The DSH plugin owns this process and sends newline-delimited JSON over stdin.
Closing stdin is a lifecycle signal: the helper exits instead of becoming an
independent desktop application.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any, TextIO

try:
    from .animation_model import AnimationModel
    from .layout_store import default_layout_path, load_layout, save_layout
except ImportError:
    from animation_model import AnimationModel
    from layout_store import default_layout_path, load_layout, save_layout


PROTOCOL_VERSION = 1
STATES = {"IDLE", "THINKING", "WORKING", "WAITING", "SUCCESS", "ERROR", "DISCONNECTED"}
SEARCH_FRAME_MS = 800
SEARCH_MICRO_CLIPS = ("searching_sigh", "searching_throw", "searching_got_it")
SEARCH_DONE_PHASES = ("done_starry", "done_happy")
# 工作会话中切到查资料的宽限期：查询持续超过此时长才真正拿起书（短暂查询保持坐姿）
SEARCH_GRACE_MS = 1200
# 少于此时长的查询不播星眼/开心收尾，直接回到工作姿态
SEARCH_EXIT_MIN_MS = 2400
WORKING_MICRO_CLIPS = ("working_confused", "working_delight", "working_idea", "working_sigh", "working_tired")
# 回答表情展示时长（3 × 0.8s），之后回到底图
QUESTION_ANSWER_MS = 2400


def bundle_root() -> Path:
    """Locate packaged assets both from source and a PyInstaller one-file build."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        return Path(frozen_root)
    return Path(__file__).resolve().parent.parent


def configure_stdio() -> None:
    """Make the JSONL pipe UTF-8 regardless of the Windows console code page."""
    for stream, errors in ((sys.stdin, "strict"), (sys.stdout, "backslashreplace"), (sys.stderr, "backslashreplace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors=errors)


def parse_message(line: str) -> dict[str, Any]:
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("message must be an object")
    if message.get("protocolVersion") != PROTOCOL_VERSION:
        raise ValueError("unsupported protocol version")
    kind = message.get("kind")
    if kind in {"state", "pulse"} and message.get("state") not in STATES:
        raise ValueError("unsupported companion state")
    return message


def emit_reply(kind: str, **payload: Any) -> None:
    print(
        json.dumps(
            {"protocolVersion": PROTOCOL_VERSION, "kind": kind, "timestamp": int(time.time() * 1000), **payload},
            ensure_ascii=False,
        ),
        flush=True,
    )


class EventRecorder:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._stream: TextIO | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = path.open("a", encoding="utf-8")

    def record(self, message: dict[str, Any]) -> None:
        if self._stream is None:
            return
        self._stream.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._stream.flush()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()


def run_headless(recorder: EventRecorder) -> int:
    try:
        emit_reply("ready")
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                message = parse_message(line)
            except (ValueError, json.JSONDecodeError) as error:
                print(json.dumps({"kind": "error", "message": str(error)}), flush=True)
                continue
            recorder.record(message)
            if message.get("kind") == "ping":
                emit_reply("pong")
                continue
            if message.get("kind") == "shutdown":
                break
    finally:
        recorder.close()
    return 0


def run_visual(recorder: EventRecorder, snapshot_path: Path | None = None) -> int:
    try:
        from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
        from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPen, QPixmap
        from PySide6.QtWidgets import QApplication, QMenu, QWidget
    except ImportError:
        print(
            "PySide6 is required for visual mode. Run with --headless for protocol tests.",
            file=sys.stderr,
        )
        recorder.close()
        return 2

    class Inbox(QObject):
        message = Signal(dict)
        closed = Signal()

    manifest_path = bundle_root() / "assets" / "pet-manifest.json"
    asset_root = manifest_path.parent / "pet"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"Unable to load BigFish asset manifest: {error}", file=sys.stderr)
        recorder.close()
        return 2

    class CompanionWindow(QWidget):
        LABELS = {
            "IDLE": "休息中",
            "THINKING": "思考中",
            "WORKING": "干活中",
            "WAITING": "等你呢",
            "SUCCESS": "完成啦",
            "ERROR": "出问题了",
            "DISCONNECTED": "已断开",
        }

        def __init__(self) -> None:
            super().__init__()
            self.layout_path = default_layout_path()
            self.layout = load_layout(self.layout_path)
            configured_scale = os.environ.get("DSH_DAFEIYU_SCALE")
            try:
                self.scale = min(1.4, max(0.7, float(configured_scale))) if configured_scale else self.layout["scale"]
            except ValueError:
                self.scale = self.layout["scale"]
            configured_reduced_motion = os.environ.get("DSH_DAFEIYU_REDUCED_MOTION")
            self.reduced_motion = (
                configured_reduced_motion == "1"
                if configured_reduced_motion is not None
                else self.layout["reducedMotion"]
            )
            self.activity_level = os.environ.get("DSH_DAFEIYU_ACTIVITY_LEVEL", "normal")
            self.model = AnimationModel(manifest)
            self.asset_scale = int(manifest.get("assetScale", 1))
            self.pixmaps: dict[str, QPixmap] = {}
            for clip in self.model.clips.values():
                for frame in clip.frames:
                    if frame in self.pixmaps:
                        continue
                    pixmap = QPixmap(str(asset_root / frame))
                    if pixmap.isNull():
                        raise RuntimeError(f"Unable to load BigFish frame: {frame}")
                    self.pixmaps[frame] = pixmap

            self.display_state = "IDLE"
            self.status_state = "IDLE"
            self.status_message = "我在这儿等新任务哦"
            self.status_detail = "DSH · 等待下一次任务"
            self.status_deadline_ms: int | None = self._now_ms() + 4200
            self.overlay_state: str | None = None
            self.overlay_message = ""
            self.overlay_detail = ""
            self.overlay_deadline_ms: int | None = None
            self.task = ""
            self.drag_origin: QPoint | None = None
            self.window_origin: QPoint | None = None
            self.dragging = False
            self.drag_phase = "none"
            self.release_start_ms = 0
            self.landed_start_ms = 0
            self.cry_start_ms = 0
            self.success_start_ms = 0
            self.leaving = False
            self.search_phase = "none"
            self.searching_active = False
            self.search_phase_ms = 0
            self.search_micro_next_ms = 0
            self.search_queued = False
            self.search_queued_ms = 0
            self.search_queued_book_base = ""
            self.search_started_ms = 0
            self.idle_micro_end_ms = None
            self.work_phase = "none"
            self.working_active = False
            self.work_phase_ms = 0
            self.work_micro_next_ms = 0
            self.question_phase = "none"
            self.question_phase_ms = 0
            self.debug_log_path = self._debug_log_path()
            self._last_animation_log = None
            self.theme_preference = "system"
            self.is_dark = self._system_is_dark()
            self.last_tick_ms = self._now_ms()
            self.type_target_title = ""
            self.type_target_detail = ""
            self.type_typed_title = 0
            self.type_typed_detail = 0
            self.type_alpha = 1.0
            self.type_accum_ms = 0
            self.animation_timer = QTimer(self)
            self.animation_timer.timeout.connect(self._tick)
            self.animation_timer.start(40)
            self.micro_timer = QTimer(self)
            self.micro_timer.setSingleShot(True)
            self.micro_timer.timeout.connect(self._play_idle_micro)
            if not self.reduced_motion:
                self._schedule_micro()
            self.snapshot_saved = False
            self.setWindowTitle("DSH 大肥鱼")
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self._apply_window_size()
            QTimer.singleShot(0, self._restore_visible_position)
            if "enter" in self.model.clips:
                self.model.play_overlay("enter")
            try:
                QApplication.styleHints().colorSchemeChanged.connect(self._on_system_theme_changed)
            except Exception:
                pass

        @staticmethod
        def _system_is_dark() -> bool:
            try:
                return QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
            except Exception:
                return False

        def _on_system_theme_changed(self, _scheme: Any) -> None:
            if self.theme_preference == "system":
                self.is_dark = self._system_is_dark()
                self.update()

        def set_theme(self, preference: str) -> None:
            self.theme_preference = preference if preference in {"light", "dark", "system"} else "system"
            if self.theme_preference == "dark":
                self.is_dark = True
            elif self.theme_preference == "light":
                self.is_dark = False
            else:
                self.is_dark = self._system_is_dark()

        def apply_message(self, message: dict[str, Any]) -> None:
            recorder.record(message)
            kind = message.get("kind")
            if kind == "theme":
                self.set_theme(str(message.get("preference", "system")))
                self.update()
                return
            if kind == "question":
                self._apply_question(message)
                self.update()
                return
            if kind == "shutdown":
                if "leave" in self.model.clips and not self.leaving:
                    # 播放离场动画（stay -> leave01 -> leave02 -> door），播完停在 door 帧再退出
                    self.leaving = True
                    self.model.hold_overlay = True
                    self.model.play_overlay("leave")
                    leave_clip = self.model.clips["leave"]
                    leave_ms = len(leave_clip.frames) * leave_clip.frame_ms
                    QTimer.singleShot(leave_ms + 200, QApplication.quit)
                else:
                    QApplication.quit()
                return
            if kind == "task":
                self.task = str(message.get("task", ""))
                self._show_status(
                    str(message.get("message", self.task)),
                    str(message.get("detail", "")),
                    self.model.base_state,
                    None if self.model.base_state in {"THINKING", "WORKING", "WAITING", "ERROR"} else 6000,
                )
            elif kind in {"state", "pulse"}:
                state = str(message.get("state", "IDLE"))
                self.display_state = state
                if state == "SUCCESS":
                    self.success_start_ms = self._now_ms()
                if kind == "pulse":
                    ttl_ms = max(250, int(message.get("ttlMs", 1800)))
                    resume_state = str(message.get("resumeState", self.model.base_state))
                    resume_activity = message.get("resumeActivity")
                    searching_was_active = self.searching_active
                    working_was_active = self.working_active and self.work_phase != "seat_out"
                    # A turn that completes while we are still searching/working, or
                    # while their exit sequence is still playing, must not let the
                    # success face cover the exit animation. Defer the success pulse
                    # until searching/working finishes its exit.
                    if state == "SUCCESS" and (
                        searching_was_active or self.search_phase in SEARCH_DONE_PHASES
                    ):
                        if searching_was_active:
                            self._finish_searching()
                        ttl_ms += self._done_remaining_ms()
                    if state == "SUCCESS" and working_was_active:
                        self._finish_working()
                        ttl_ms += self._work_remaining_ms()
                    self.model.apply_pulse(state, ttl_ms, self._now_ms(), resume_state, resume_activity)
                    if searching_was_active and state != "SUCCESS":
                        self._cancel_searching()
                        self.model.clear_overlay()
                    if working_was_active and state != "SUCCESS":
                        self._cancel_working()
                        self.model.clear_overlay()
                    self._show_status(
                        str(message.get("resumeMessage", self.LABELS.get(resume_state, resume_state))),
                        str(message.get("resumeDetail", "")),
                        resume_state,
                        None if resume_state in {"THINKING", "WORKING", "WAITING", "ERROR"} else ttl_ms + 2200,
                    )
                    self._show_overlay(
                        str(message.get("message", self.LABELS.get(state, state))),
                        str(message.get("detail", "")),
                        state,
                        ttl_ms,
                    )
                    self._log_animation(f"pulse:{state}")
                else:
                    activity = None if self.reduced_motion else message.get("activity")
                    is_searching = activity == "searching"
                    previous_base = self.model.base_clip_name
                    self.model.apply_state(state, activity)
                    # 提问/回答表情展示期间不切换搜索/工作的进出场动画，避免盖掉 question/answer 表情
                    if self.question_phase == "none":
                        if state == "WORKING":
                            # 工作状态（查资料是其子态）：只在进入时掏出凳子/打开笔记本，
                            # 之后保持 working 默认坐姿，不因活动在编辑/搜索之间变化而反复坐下
                            if not self.working_active and self.work_phase != "seat_out":
                                self._begin_working()
                            if is_searching:
                                if not self.searching_active and self.search_phase not in SEARCH_DONE_PHASES:
                                    if (self.working_active and self.work_phase in {"stay", "micro"}
                                            and self.model.overlay_clip_name is None):
                                        # 坐姿干活中的查资料：先等宽限期，短查询保持坐姿不切书
                                        self.search_queued = True
                                        self.search_queued_ms = self._now_ms()
                                        self.search_queued_book_base = self.model.base_clip_name
                                        self.model.base_clip_name = previous_base
                                        self.model._activate(previous_base)
                                    else:
                                        self._begin_searching()
                            elif self.searching_active:
                                self._finish_searching()
                            elif self.search_queued:
                                # 查询在宽限期内就结束了：取消切书，保持工作姿态
                                self.search_queued = False
                                self.search_queued_ms = 0
                        elif self.working_active:
                            self._finish_working()
                    persistent = state in {"THINKING", "WORKING", "WAITING", "ERROR"}
                    self._show_status(
                        str(message.get("message", self.LABELS.get(state, state))),
                        str(message.get("detail", "")),
                        state,
                        None if persistent else 4200,
                    )
                    self._log_animation(f"state:{state}:{activity}")
            self.update()
            if snapshot_path is not None and not self.snapshot_saved:
                QTimer.singleShot(180, self._save_snapshot)

        def _begin_searching(self) -> None:
            """Enter the searching activity: book_ready -> book_reading."""
            self.searching_active = True
            self.search_phase = "ready"
            self.model.play_overlay("searching_ready")
            self.search_phase_ms = self._now_ms()
            self.search_started_ms = self._now_ms()
            self._log_animation("begin_searching")

        def _finish_searching(self) -> None:
            """Leave searching: starry_face -> book_happy；短查询直接回工作姿态。"""
            self.searching_active = False
            if self._now_ms() - self.search_started_ms < SEARCH_EXIT_MIN_MS:
                # 短暂查询不播星眼/开心收尾，避免工作与查资料频繁快切
                self.search_phase = "none"
                self.search_micro_next_ms = 0
                self.model.clear_overlay()
                self._log_animation("cancel_searching")
                return
            self.search_phase = "done_starry"
            self.model.play_overlay("searching_starry")
            self.search_phase_ms = self._now_ms()
            self._log_animation("finish_searching")

        def _cancel_searching(self) -> None:
            """Hard interrupt (drag) cancels the whole searching suite."""
            self.searching_active = False
            self.search_phase = "none"
            self.search_micro_next_ms = 0
            self.search_queued = False
            self.search_queued_ms = 0
            self._log_animation("cancel_searching")

        def _debug_log_path(self) -> Path:
            override = os.environ.get("DSH_DAFEIYU_DEBUG_LOG")
            if override:
                return Path(override)
            return default_layout_path().parent / "debug-animation.log"

        def _log_animation(self, event: str) -> None:
            """Append a one-line trace whenever the shown frame / phase changes."""
            try:
                key = (
                    self.model.active_clip_name,
                    self.model.frame,
                    self.search_phase,
                    self.searching_active,
                    self.work_phase,
                    self.question_phase,
                    self.working_active,
                    self.model.base_clip_name,
                    self.model.pulse_clip_name,
                    self.model.overlay_clip_name,
                )
                if key == self._last_animation_log:
                    return
                self._last_animation_log = key
                line = (
                    f"{self._now_ms()}ms {event} clip={key[0]} frame={key[1]} "
                    f"phase={key[2]} searching={key[3]} wphase={key[4]} qphase={key[5]} working={key[6]} "
                    f"base={key[7]} pulse={key[8]} overlay={key[9]}"
                )
                self.debug_log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.debug_log_path, "a", encoding="utf-8") as stream:
                    stream.write(line + "\n")
            except OSError:
                pass

        def _done_remaining_ms(self) -> int:
            if self.search_phase not in SEARCH_DONE_PHASES:
                return 0
            phase_index = SEARCH_DONE_PHASES.index(self.search_phase)
            elapsed = self._now_ms() - self.search_phase_ms
            remaining = (len(SEARCH_DONE_PHASES) - phase_index) * SEARCH_FRAME_MS - elapsed
            return max(0, remaining)

        def _begin_working(self) -> None:
            """Enter the working activity: seat entrance -> working default seat_05."""
            self.working_active = True
            self.work_phase = "seat_in"
            self.model.play_overlay("working_seat_in")
            self._log_animation("begin_working")

        def _finish_working(self) -> None:
            """Leave working: reversed seat sequence as the exit animation."""
            if self.work_phase == "seat_in":
                # Entrance never finished; cut to the base state instead of
                # playing a full exit animation for a barely-started task.
                self.working_active = False
                self.work_phase = "none"
                self.work_micro_next_ms = 0
                self.model.clear_overlay()
                self._log_animation("cancel_working")
                return
            self.work_phase = "seat_out"
            self.work_phase_ms = self._now_ms()
            self.model.play_overlay("working_seat_out")
            self._log_animation("finish_working")

        def _cancel_working(self) -> None:
            """Hard interrupt (drag) cancels the working suite."""
            self.working_active = False
            self.work_phase = "none"
            self.work_micro_next_ms = 0
            self._log_animation("cancel_working")

        def _work_remaining_ms(self) -> int:
            if self.work_phase != "seat_out":
                return 0
            clip = self.model.clips["working_seat_out"]
            total = len(clip.frames) * clip.frame_ms
            elapsed = self._now_ms() - self.work_phase_ms
            return max(0, total - elapsed)

        def _apply_question(self, message: dict[str, Any]) -> None:
            state = str(message.get("state", "asked"))
            if state == "asked":
                self._begin_question(str(message.get("question") or ""))
            elif state == "answered":
                self._finish_question()

        def _begin_question(self, question: str) -> None:
            """提问：打断进行中的进出场动画，播放 question 表情并保持到用户回答。"""
            self._cancel_working()
            self._cancel_searching()
            self.question_phase = "asked"
            self.question_phase_ms = self._now_ms()
            self.model.play_overlay("question")
            if question:
                # 状态卡字幕显示实际提问的问题
                self._show_status(question, "等你回答", self.display_state, None)
            else:
                self._show_status("问你一个问题", "请回答我", self.display_state, None)
            self._log_animation("begin_question")

        def _finish_question(self) -> None:
            """回答：播放 answer 表情一段时间后回到底图。"""
            self.question_phase = "answered"
            self.question_phase_ms = self._now_ms()
            self.model.play_overlay("answer")
            self._log_animation("finish_question")

        def _cancel_question(self) -> None:
            """硬打断（拖拽）取消 question/answer 表情。"""
            self.question_phase = "none"
            self.question_phase_ms = 0
            self._log_animation("cancel_question")

        def _tick(self) -> None:
            now_ms = self._now_ms()
            elapsed_ms = max(0, now_ms - self.last_tick_ms)
            self.last_tick_ms = now_ms
            had_pulse = self.model.pulse_state is not None
            model_elapsed = 0 if self.reduced_motion and self.model.active_clip.loop else elapsed_ms
            self.model.advance(model_elapsed, now_ms)
            if had_pulse and self.model.pulse_state is None:
                self.display_state = self.model.base_state
            if self.overlay_deadline_ms is not None and now_ms >= self.overlay_deadline_ms:
                self._clear_overlay()
            if self.drag_phase == "release" and now_ms - self.release_start_ms >= 250:
                self.drag_phase = "landed"
                self.model.play_overlay("dragging_landed")
                self.landed_start_ms = now_ms
            elif self.drag_phase == "landed" and now_ms - self.landed_start_ms >= 1000:
                self.drag_phase = "cry"
                self.model.play_overlay("dragging_cry")
                self.cry_start_ms = now_ms
            elif self.drag_phase == "cry" and now_ms - self.cry_start_ms >= 1000:
                self.model.clear_overlay()
                self.drag_phase = "none"
            # 查资料宽限：查询持续足够长才真正拿起书
            if self.search_queued and now_ms - self.search_queued_ms >= SEARCH_GRACE_MS:
                self.search_queued = False
                self.model.base_clip_name = self.search_queued_book_base
                self._begin_searching()
            # searching 多阶段状态机
            if self.search_phase == "ready" and now_ms - self.search_phase_ms >= SEARCH_FRAME_MS:
                self.search_phase = "reading"
                self.model.clear_overlay()
                self.search_phase_ms = now_ms
                self.search_micro_next_ms = now_ms + random.randint(3500, 8000)
            elif self.search_phase == "reading" and now_ms >= self.search_micro_next_ms:
                clip = random.choice(SEARCH_MICRO_CLIPS)
                self.search_phase = "micro"
                self.model.play_overlay(clip)
                self.search_phase_ms = now_ms
            elif self.search_phase == "micro" and now_ms - self.search_phase_ms >= SEARCH_FRAME_MS:
                self.search_phase = "reading"
                self.model.clear_overlay()
                self.search_phase_ms = now_ms
                self.search_micro_next_ms = now_ms + random.randint(3500, 8000)
            elif self.search_phase == "done_starry" and now_ms - self.search_phase_ms >= SEARCH_FRAME_MS:
                self.search_phase = "done_happy"
                self.model.play_overlay("searching_happy")
                self.search_phase_ms = now_ms
            elif self.search_phase == "done_happy" and now_ms - self.search_phase_ms >= SEARCH_FRAME_MS:
                self.search_phase = "none"
                self.model.clear_overlay()
            # working 多阶段状态机
            if self.work_phase == "seat_in" and self.model.overlay_clip_name is None:
                # 入场完成（自动回到 working_seat_05 底图）
                self.work_phase = "stay"
                self.work_micro_next_ms = now_ms + random.randint(3500, 8000)
            elif (self.work_phase == "stay" and now_ms >= self.work_micro_next_ms
                  and not self.searching_active and not self.search_queued):
                clip = random.choice(WORKING_MICRO_CLIPS)
                self.work_phase = "micro"
                self.model.play_overlay(clip)
                self.work_phase_ms = now_ms
            elif self.work_phase == "micro" and now_ms - self.work_phase_ms >= SEARCH_FRAME_MS:
                self.work_phase = "stay"
                self.model.clear_overlay()
                self.work_phase_ms = now_ms
                self.work_micro_next_ms = now_ms + random.randint(3500, 8000)
            elif self.work_phase == "seat_out" and self.model.overlay_clip_name is None:
                # 退场完成（自动回到恢复态的底图）
                self.work_phase = "none"
                self.working_active = False
            # question/answer 状态机：提问表情保持，回答表情到时自动回到底图
            if self.question_phase == "answered" and now_ms - self.question_phase_ms >= QUESTION_ANSWER_MS:
                self.question_phase = "none"
                if self.model.overlay_clip_name == "answer":
                    self.model.clear_overlay()
            # 待机微动作到时回到底图（扫地等单帧循环由计时控制，否则会一直播放）
            if (self.idle_micro_end_ms is not None and now_ms >= self.idle_micro_end_ms
                    and self.model.overlay_clip_name in self.model.idle_micro_clips):
                self.idle_micro_end_ms = None
                self.model.clear_overlay()
            self._log_animation("tick")
            card_now = self._current_card()
            if card_now:
                title_now, detail_now, _ = card_now
                title_done = self.type_typed_title >= len(self.type_target_title)
                title_changed = title_now != self.type_target_title
                if title_changed and title_done:
                    # 当前标题打字机打完才允许切换
                    self.type_target_title = title_now
                    self.type_target_detail = detail_now
                    self.type_typed_title = 0
                    self.type_typed_detail = 0
                    self.type_alpha = 0.0
                    self.type_accum_ms = 0
                elif not title_changed and detail_now != self.type_target_detail and title_done:
                    # 标题不变时，详情（进度/任务）实时更新
                    self.type_target_detail = detail_now
                    self.type_typed_detail = 0
                self.type_accum_ms += elapsed_ms
                chars = self.type_accum_ms // 42
                if chars > 0:
                    self.type_accum_ms -= chars * 42
                    if self.type_typed_title < len(self.type_target_title):
                        self.type_typed_title = min(len(self.type_target_title), self.type_typed_title + chars)
                    elif self.type_typed_detail < len(self.type_target_detail):
                        self.type_typed_detail = min(len(self.type_target_detail), self.type_typed_detail + chars)
                if self.type_alpha < 1.0:
                    self.type_alpha = min(1.0, self.type_alpha + 0.16)
            self.update()

        def _play_idle_micro(self) -> None:
            if self.reduced_motion:
                return
            self.model.play_idle_micro(random.randrange(max(1, len(self.model.idle_micro_clips))))
            if self.model.overlay_clip_name in self.model.idle_micro_clips:
                clip = self.model.clips[self.model.overlay_clip_name]
                # 非循环多帧 = 整段播放时长；单帧循环（如扫地）= 一帧时长（1.6s）
                duration = clip.frame_ms if clip.loop else len(clip.frames) * clip.frame_ms
                self.idle_micro_end_ms = self._now_ms() + max(300, duration)
            # 无论本次是否真的播放（可能被其他动画占用），都必须重新调度，
            # 否则定时器死亡后待机表情再也不会出现
            self._schedule_micro()

        def _schedule_micro(self) -> None:
            if self.reduced_motion:
                self.micro_timer.stop()
                return
            intervals = {
                "quiet": (12000, 24000),
                "normal": (6500, 12500),
                "lively": (3500, 8000),
            }
            lower, upper = intervals.get(self.activity_level, intervals["normal"])
            self.micro_timer.start(random.randint(lower, upper))

        def _apply_window_size(self) -> None:
            pet_width = round(int(manifest["maxFrameWidth"]) * self.scale)
            pet_height = round(int(manifest["maxFrameHeight"]) * self.scale)
            # 顶部净空 135：单行状态卡下人物贴近卡片（可见间距约 10px），
            # 两行卡片时坐姿放大帧（1.08）仍仅裁到透明留白。
            self.setFixedSize(max(448, pet_width + 50), pet_height + 135)

        def _restore_visible_position(self) -> None:
            saved_x = self.layout.get("x")
            saved_y = self.layout.get("y")
            primary = QApplication.primaryScreen()
            if primary is None:
                return
            if not isinstance(saved_x, int) or not isinstance(saved_y, int):
                geometry = primary.availableGeometry()
                saved_x = geometry.right() - self.width() - 24
                saved_y = geometry.bottom() - self.height() - 24
            self.move(saved_x, saved_y)
            self._clamp_to_visible_screen()

        def _clamp_to_visible_screen(self) -> None:
            center = QPoint(self.x() + self.width() // 2, self.y() + self.height() // 2)
            screen = QApplication.screenAt(center) or QApplication.primaryScreen()
            if screen is None:
                return
            geometry = screen.availableGeometry()
            x = min(max(self.x(), geometry.left()), max(geometry.left(), geometry.right() - self.width() + 1))
            y = min(max(self.y(), geometry.top()), max(geometry.top(), geometry.bottom() - self.height() + 1))
            self.move(x, y)

        def _save_layout(self) -> None:
            self.layout = {
                "version": 1,
                "x": self.x(),
                "y": self.y(),
                "scale": self.scale,
                "reducedMotion": self.reduced_motion,
            }
            try:
                save_layout(self.layout_path, self.layout)
            except OSError as error:
                print(f"Unable to save BigFish layout: {error}", file=sys.stderr)

        def _save_snapshot(self) -> None:
            if snapshot_path is None or self.snapshot_saved:
                return
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            self.snapshot_saved = self.grab().save(str(snapshot_path), "PNG")

        def _show_status(self, message: str, detail: str, state: str, ttl_ms: int | None) -> None:
            self.status_message = message
            self.status_detail = detail
            self.status_state = state
            self.status_deadline_ms = None if ttl_ms is None else self._now_ms() + ttl_ms

        def _show_overlay(self, message: str, detail: str, state: str, ttl_ms: int) -> None:
            self.overlay_message = message
            self.overlay_detail = detail or self.status_detail
            self.overlay_state = state
            self.overlay_deadline_ms = self._now_ms() + ttl_ms

        def _clear_overlay(self) -> None:
            self.overlay_message = ""
            self.overlay_detail = ""
            self.overlay_state = None
            self.overlay_deadline_ms = None

        @staticmethod
        def _now_ms() -> int:
            return int(time.monotonic() * 1000)

        def _current_card(self) -> tuple[str, str, str] | None:
            now_ms = self._now_ms()
            if self.overlay_message and (
                self.overlay_deadline_ms is None or now_ms < self.overlay_deadline_ms
            ):
                return self.overlay_message, self.overlay_detail, self.overlay_state or self.status_state
            if self.status_message and (
                self.status_deadline_ms is None or now_ms < self.status_deadline_ms
            ):
                return self.status_message, self.status_detail, self.status_state
            return None

        def _status_colors(self, state: str) -> tuple[QColor, QColor]:
            if self.is_dark:
                return {
                    "SUCCESS": (QColor(18, 184, 90, 46), QColor("#34d07c")),
                    "ERROR": (QColor(229, 72, 77, 46), QColor("#ff6b6b")),
                    "WAITING": (QColor(216, 138, 0, 46), QColor("#ffb340")),
                    "THINKING": (QColor(76, 120, 232, 51), QColor("#7ea2ff")),
                    "WORKING": (QColor(52, 120, 246, 51), QColor("#6ea8ff")),
                    "DISCONNECTED": (QColor(123, 129, 138, 46), QColor("#9aa2b1")),
                }.get(state, (QColor(123, 129, 138, 46), QColor("#9aa2b1")))
            return {
                "SUCCESS": (QColor("#D9F7E4"), QColor("#12B85A")),
                "ERROR": (QColor("#FDE3E3"), QColor("#E5484D")),
                "WAITING": (QColor("#FFF0CE"), QColor("#D88A00")),
                "THINKING": (QColor("#E2ECFF"), QColor("#4C78E8")),
                "WORKING": (QColor("#DDEBFF"), QColor("#3478F6")),
                "DISCONNECTED": (QColor("#ECEEF1"), QColor("#7B818A")),
            }.get(state, (QColor("#ECEEF1"), QColor("#747A84")))

        def _palette(self) -> dict[str, QColor]:
            # 与 DSH 主题 token 对齐：深色 = bg-layer-1 #232324 / label #F9FAFB / #CFD3D6
            # 浅色 = bg-base #FFFFFF / label #0F1115 / #61666B
            if self.is_dark:
                return {
                    "shadow1": QColor(0, 0, 0, 70),
                    "shadow2": QColor(0, 0, 0, 90),
                    "border": QColor(255, 255, 255, 28),
                    "card": QColor(35, 35, 36, 250),
                    "title": QColor(249, 250, 251),
                    "detail": QColor(207, 211, 214),
                }
            return {
                "shadow1": QColor(17, 24, 39, 13),
                "shadow2": QColor(17, 24, 39, 18),
                "border": QColor(0, 0, 0, 20),
                "card": QColor(255, 255, 255, 250),
                "title": QColor(15, 17, 21),
                "detail": QColor(97, 102, 107),
            }

        def _draw_status_icon(self, painter: QPainter, state: str, center_x: int, center_y: int) -> None:
            background, foreground = self._status_colors(state)
            radius = 23
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawEllipse(center_x - radius, center_y - radius, radius * 2, radius * 2)
            pen = QPen(foreground, 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if state == "SUCCESS":
                progress = min(1.0, max(0, self._now_ms() - self.success_start_ms) / 500.0)
                ax, ay = center_x - 10, center_y
                bx, by = center_x - 3, center_y + 8
                cx, cy = center_x + 12, center_y - 10
                if progress <= 0.35:
                    t = progress / 0.35
                    painter.drawLine(ax, ay, round(ax + (bx - ax) * t), round(ay + (by - ay) * t))
                else:
                    painter.drawLine(ax, ay, bx, by)
                    t = (progress - 0.35) / 0.65
                    painter.drawLine(bx, by, round(bx + (cx - bx) * t), round(by + (cy - by) * t))
            elif state == "ERROR":
                painter.drawLine(center_x - 8, center_y - 8, center_x + 8, center_y + 8)
                painter.drawLine(center_x + 8, center_y - 8, center_x - 8, center_y + 8)
            elif state == "WAITING":
                painter.drawLine(center_x, center_y - 10, center_x, center_y + 3)
                painter.setBrush(foreground)
                painter.drawEllipse(center_x - 2, center_y + 9, 4, 4)
            elif state in {"THINKING", "WORKING"}:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(foreground)
                phase = time.monotonic()
                for i, offset in enumerate((-9, 0, 9)):
                    jump = round(math.sin(phase * 5.0 - i * 0.8) * 5)
                    painter.drawEllipse(center_x + offset - 3, center_y + jump - 3, 6, 6)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(foreground)
                painter.drawEllipse(center_x - 5, center_y - 5, 10, 10)

        def paintEvent(self, _event: Any) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            card = self._current_card()
            bubble_height = 12
            if card:
                title, detail, card_state = card
                card_x = 14
                card_y = 7
                card_width = self.width() - 28
                text_width = card_width - 102
                text_x = card_x + 24
                palette = self._palette()
                # 标题支持换行：最多 3 行，超出截断加省略号；状态卡按需增高
                title_font = QFont("Microsoft YaHei UI", 11)
                title_font.setWeight(QFont.Weight.DemiBold)
                detail_font = QFont("Microsoft YaHei UI", 9)
                fm_title = QFontMetrics(title_font)
                line_h = max(1, fm_title.height())
                typed_title = title[:self.type_typed_title]
                typed_detail = detail[:self.type_typed_detail]

                def title_layout(text: str) -> tuple[str, int]:
                    """返回 (可绘制标题, 占用行高)：自动换行，行数不限，卡片动态增高。"""
                    if not text:
                        return "", max(27, line_h)
                    height = fm_title.boundingRect(
                        0, 0, text_width, 0, Qt.TextFlag.TextWordWrap, text,
                    ).height()
                    return text, max(27, height)

                display_title, title_height = title_layout(typed_title)
                detail_y = card_y + 15 + title_height + 4
                card_height = detail_y + 24 - card_y + 17
                bubble_height = card_y + card_height + 13

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(palette["shadow1"])
                painter.drawRoundedRect(card_x + 1, card_y + 13, card_width - 2, card_height, 30, 30)
                painter.setBrush(palette["shadow2"])
                painter.drawRoundedRect(card_x, card_y + 7, card_width, card_height, 30, 30)
                painter.setPen(QPen(palette["border"], 1))
                painter.setBrush(palette["card"])
                painter.drawRoundedRect(card_x, card_y, card_width, card_height, 30, 30)

                icon_center_x = card_x + card_width - 39
                icon_center_y = card_y + card_height // 2
                self._draw_status_icon(painter, card_state, icon_center_x, icon_center_y)

                painter.save()
                painter.setOpacity(self.type_alpha)
                painter.setFont(title_font)
                painter.setPen(palette["title"])
                painter.drawText(
                    text_x,
                    card_y + 15,
                    text_width,
                    title_height,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                    display_title,
                )
                painter.setFont(detail_font)
                painter.setPen(palette["detail"])
                detail_text = QFontMetrics(detail_font).elidedText(
                    typed_detail,
                    Qt.TextElideMode.ElideRight,
                    text_width,
                )
                painter.drawText(
                    text_x,
                    detail_y,
                    text_width,
                    24,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    detail_text,
                )
                painter.restore()

            pixmap = self.pixmaps[self.model.frame]
            phase = time.monotonic()
            motion = self.model.active_clip.motion
            offset_x = 0
            offset_y = 0
            if self.reduced_motion:
                motion = None
            if motion == "breathe":
                offset_y = round(math.sin(phase * 2.1) * 2)
            elif motion == "think":
                offset_y = round(math.sin(phase * 2.8) * 3)
            elif motion == "work":
                offset_x = round(math.sin(phase * 5.4) * 2)
            elif motion == "wait":
                offset_y = round(math.sin(phase * 1.8) * 2)
            elif motion == "bounce":
                offset_y = -round(abs(math.sin(phase * 5.2)) * 8)
            elif motion in {"shake", "dizzy"}:
                offset_x = round(math.sin(phase * 11.0) * 4)
            elif motion == "float":
                offset_y = round(math.sin(phase * 3.0) * 4)

            logical_w = pixmap.width() / self.asset_scale
            logical_h = pixmap.height() / self.asset_scale
            # clip.scale 是补偿性放大（如坐着干活的帧角色偏小），保持底部锚定。
            clip_scale = self.model.active_clip.scale
            pixmap_width = round(logical_w * self.scale * clip_scale)
            pixmap_height = round(logical_h * self.scale * clip_scale)
            if self.model.active_clip_name == "searching_throw":
                # book_throw is much wider than the other searching frames and the
                # book flies to the right, so its character sits ~191 physical px
                # (alpha centroid) from the frame's left edge instead of the middle.
                # Anchor that character point to the window center — matching the
                # centered non-throw frames — so only the book extends right and the
                # fish no longer jumps left when the wide frame is centered.
                fish_offset = round((191.0 / self.asset_scale) * self.scale)
                x = (self.width() - 2 * fish_offset) // 2 + offset_x
            else:
                x = (self.width() - pixmap_width) // 2 + offset_x
            # 先按底部锚定并做状态卡遮挡钳制，再叠加动作偏移（呼吸/弹跳等），
            # 否则放大后的坐姿帧 y 低于钳制线时呼吸的 offset_y 会被整体丢弃。
            y = self.height() - pixmap_height - 8
            if bubble_height > y:
                y = bubble_height
            y += offset_y
            painter.drawPixmap(x, y, pixmap_width, pixmap_height, pixmap)

        def mousePressEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.drag_origin = event.globalPosition().toPoint()
                self.window_origin = self.pos()
                self.dragging = False

        def mouseMoveEvent(self, event: QMouseEvent) -> None:
            if self.drag_origin is not None and self.window_origin is not None:
                if not self.dragging and (event.globalPosition().toPoint() - self.drag_origin).manhattanLength() > 5:
                    self.dragging = True
                    self.drag_phase = "hold"
                    self._cancel_searching()
                    self._cancel_working()
                    self._cancel_question()
                    self.model.play_overlay("dragging_hold")
                self.move(self.window_origin + event.globalPosition().toPoint() - self.drag_origin)

        def mouseReleaseEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                if self.dragging:
                    self.drag_phase = "release"
                    self.model.play_overlay("dragging_release")
                    self.release_start_ms = self._now_ms()
                    self._clamp_to_visible_screen()
                    self._save_layout()
                else:
                    self._play_click_interaction(event.position().x(), event.position().y())
            self.drag_origin = None
            self.window_origin = None
            self.dragging = False

        def _play_click_interaction(self, x: float, y: float) -> None:
            pet_height = int(manifest["maxFrameHeight"]) * self.scale
            pet_top = self.height() - pet_height - 8
            relative_y = max(0.0, y - pet_top)
            if relative_y < pet_height * 0.45:
                self.model.play_overlay("head_pat")
                self._show_overlay("摸摸也不能让我少干活哦~", self.status_detail, self.status_state, 1800)
            elif x > self.width() * 0.72:
                self.model.play_overlay("tail")
                self._show_overlay("尾巴不是进度条啦！", self.status_detail, self.status_state, 1500)
            else:
                self.model.play_overlay("poke")
                self._show_overlay("戳我干嘛，任务还在跑呢", self.status_detail, self.status_state, 1500)

        def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.model.play_overlay("head_pat")
                self._show_overlay("好啦好啦，知道你喜欢我~", self.status_detail, self.status_state, 1800)

        def contextMenuEvent(self, event: Any) -> None:
            menu = QMenu(self)
            size_menu = menu.addMenu("大小")
            size_actions = {}
            for label, scale in (("小", 0.8), ("标准", 1.0), ("大", 1.25)):
                action = size_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(abs(self.scale - scale) < 0.05)
                size_actions[action] = scale
            reduced_action = menu.addAction("减少动态")
            reduced_action.setCheckable(True)
            reduced_action.setChecked(self.reduced_motion)
            menu.addSeparator()
            hide_action = menu.addAction("本次隐藏")
            exit_action = menu.addAction("本次关闭")
            selected = menu.exec(event.globalPos())
            if selected in size_actions:
                self.scale = size_actions[selected]
                self._apply_window_size()
                self._clamp_to_visible_screen()
                self._save_layout()
                self.update()
            elif selected == reduced_action:
                self.reduced_motion = reduced_action.isChecked()
                if self.reduced_motion:
                    self.micro_timer.stop()
                else:
                    self._schedule_micro()
                self._save_layout()
                self.update()
            elif selected == hide_action:
                self.hide()
            elif selected == exit_action:
                self._save_layout()
                emit_reply("closed", reason="user")
                QApplication.quit()

    application = QApplication(sys.argv[:1])
    application.setQuitOnLastWindowClosed(False)
    inbox = Inbox()
    window = CompanionWindow()
    inbox.message.connect(window.apply_message)

    def on_stdin_closed() -> None:
        # 离场动画播放期间不立即退出，等动画播完由定时器退出
        if not window.leaving:
            application.quit()
    inbox.closed.connect(on_stdin_closed)

    def read_stdin() -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                message = parse_message(line)
                if message.get("kind") == "ping":
                    emit_reply("pong")
                inbox.message.emit(message)
            except (ValueError, json.JSONDecodeError) as error:
                print(json.dumps({"kind": "error", "message": str(error)}), flush=True)
        inbox.closed.emit()

    reader = threading.Thread(target=read_stdin, name="dsh-bigfish-stdin", daemon=True)
    reader.start()
    window.show()
    emit_reply("ready")
    code = application.exec()
    recorder.close()
    return code


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="DSH BigFish native helper")
    parser.add_argument("--headless", action="store_true", help="validate the protocol without opening a window")
    parser.add_argument("--event-log", type=Path, help="append received protocol messages to a JSONL file")
    parser.add_argument("--snapshot", type=Path, help="save one diagnostic visual frame after the first message")
    args = parser.parse_args()
    recorder = EventRecorder(args.event_log)
    return run_headless(recorder) if args.headless else run_visual(recorder, args.snapshot)


if __name__ == "__main__":
    raise SystemExit(main())
