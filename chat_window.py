"""
对话窗口 - 接入 DeepSeek，UI 只显示 text，动画/TTS 后台执行
"""

import threading
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
                              QLineEdit, QPushButton, QLabel, QCheckBox, QFrame)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer
from PyQt5.QtGui import QFont

import deepseek_client
from deepseek_client import DeepSeekError
from llm_parser import parse_llm_response
from llm_mapper import apply_llm_command

GREETING_TEXT = "啾……主人，我回来了"

# 对话亲密度加成关键词
_AFFECTION_KEYWORDS = {
    "喜欢你": 5, "想你了": 5, "想你": 4, "肥鸡真可爱": 4, "好可爱": 3,
    "陪陪我": 4, "陪我": 3, "今天开心": 2,
    "爱你": 5, "宝贝": 3, "乖": 2, "聪明": 2, "漂亮": 2,
}


class _Signals(QObject):
    reply_ready = pyqtSignal(dict)   # payload dict
    error       = pyqtSignal(str)
    thinking    = pyqtSignal()


class ChatWindow(QWidget):
    def __init__(self, tts_engine, pet=None):
        super().__init__()
        self.tts = tts_engine
        self.pet = pet                  # PetWindow 引用，用于触发动画
        self._signals = _Signals()
        self._signals.reply_ready.connect(self._on_reply)
        self._signals.error.connect(self._on_error)
        self._signals.thinking.connect(self._on_thinking)
        self._history = []
        self._voice_enabled = True
        self._last_user_text = ""

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(320)

        self._build_ui()

        # 10 秒无操作自动关闭
        self._inactivity_timer = QTimer(self)
        self._inactivity_timer.setSingleShot(True)
        self._inactivity_timer.timeout.connect(self._on_inactivity)
        self._mouse_inside = False

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        card = QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet("""
            QFrame#card {
                background: rgba(255,255,255,220);
                border-radius: 16px;
                border: 1px solid rgba(200,200,200,180);
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # 标题栏
        title_row = QHBoxLayout()
        title = QLabel("🐦 肥鸡")
        title.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        title.setStyleSheet("color: #444;")
        self._affection_label = QLabel("❤️ 亲密度 50")
        self._affection_label.setStyleSheet("color: #e55; font-size: 11px;")
        close_btn = QPushButton("×")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; color: #888; font-size: 16px; }
            QPushButton:hover { color: #e55; }
        """)
        close_btn.clicked.connect(self.hide)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self._affection_label)
        title_row.addSpacing(6)
        title_row.addWidget(close_btn)
        layout.addLayout(title_row)

        # 对话记录
        self._chat_area = QTextEdit()
        self._chat_area.setReadOnly(True)
        self._chat_area.setFixedHeight(200)
        self._chat_area.setStyleSheet("""
            QTextEdit {
                background: rgba(245,245,245,200);
                border: none; border-radius: 8px;
                font-size: 12px; color: #333; padding: 6px;
            }
        """)
        layout.addWidget(self._chat_area)

        # 输入框 + 发送
        input_row = QHBoxLayout()
        self._msg_input = QLineEdit()
        self._msg_input.setPlaceholderText("跟肥鸡说点什么…")
        self._msg_input.setStyleSheet(self._input_style())
        self._msg_input.returnPressed.connect(self._send)
        self._msg_input.textChanged.connect(self._reset_inactivity_timer)
        self._msg_input.focusInEvent = self._on_input_focus_in
        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedWidth(52)
        self._send_btn.setStyleSheet("""
            QPushButton {
                background: #ff9966; color: white;
                border: none; border-radius: 8px;
                font-size: 12px; padding: 4px;
            }
            QPushButton:hover { background: #ff7744; }
            QPushButton:disabled { background: #ccc; }
        """)
        self._send_btn.clicked.connect(self._send)
        input_row.addWidget(self._msg_input)
        input_row.addWidget(self._send_btn)
        layout.addLayout(input_row)

        # 语音开关 + 取消唱歌
        voice_row = QHBoxLayout()
        self._voice_cb = QCheckBox("朗读回复")
        self._voice_cb.setChecked(True)
        self._voice_cb.setStyleSheet("color:#666; font-size:11px;")
        self._voice_cb.stateChanged.connect(
            lambda s: setattr(self, "_voice_enabled", s == Qt.Checked)
        )
        self._stop_sing_btn = QPushButton("让肥鸡安静")
        self._stop_sing_btn.setFixedWidth(80)
        self._stop_sing_btn.setStyleSheet("""
            QPushButton {
                background: #aaa; color: white;
                border: none; border-radius: 8px;
                font-size: 11px; padding: 3px;
            }
            QPushButton:hover { background: #888; }
        """)
        self._stop_sing_btn.clicked.connect(self._on_stop_sing)
        self._stop_sing_btn.hide()
        voice_row.addWidget(self._stop_sing_btn)
        voice_row.addStretch()
        voice_row.addWidget(self._voice_cb)
        layout.addLayout(voice_row)

        outer.addWidget(card)

    def _input_style(self):
        return """
            QLineEdit {
                background: rgba(245,245,245,200);
                border: 1px solid #ddd; border-radius: 8px;
                padding: 4px 8px; font-size: 12px; color: #333;
            }
            QLineEdit:focus { border: 1px solid #ff9966; }
        """

    def _send(self):
        text = self._msg_input.text().strip()
        if not text:
            return

        self._msg_input.clear()
        self._send_btn.setEnabled(False)
        self._append_msg("你", text)
        self._last_user_text = text

        # 传给 worker 的是发送前的 history（不含本轮 user）
        history_snapshot = list(self._history)
        # 再把本轮 user 加入 history
        self._history.append({"role": "user", "content": text})

        threading.Thread(
            target=self._worker,
            args=(text, history_snapshot),
            daemon=True,
        ).start()

    def _worker(self, user_text: str, history: list):
        self._signals.thinking.emit()
        try:
            raw = deepseek_client.send_message(user_text, history)
            payload = parse_llm_response(raw)
            # history 里存原始 raw（完整 JSON），保持上下文格式一致
            # 如果 raw 是合法 JSON 就存 raw，否则存最小合法 JSON
            import json as _json
            try:
                _json.loads(raw)
                history_content = raw
            except Exception:
                history_content = _json.dumps(
                    {"text": payload["text"], "emotion": payload["emotion"],
                     "action": payload["action"], "animation": payload["animation"], "tts": True},
                    ensure_ascii=False
                )
            self._history.append({"role": "assistant", "content": history_content})
            # 只保留最近 20 条
            if len(self._history) > 20:
                self._history = self._history[-20:]
            self._signals.reply_ready.emit(payload)
        except DeepSeekError as e:
            fallback_json = '{"text": "啾……我刚刚有点没听清，但我还在这里。", "emotion": "normal", "action": "idle", "animation": "idle_anim", "tts": true}'
            self._history.append({"role": "assistant", "content": fallback_json})
            if len(self._history) > 20:
                self._history = self._history[-20:]
            self._signals.error.emit(str(e))
        except Exception as e:
            fallback_json = '{"text": "啾……我刚刚有点没听清，但我还在这里。", "emotion": "normal", "action": "idle", "animation": "idle_anim", "tts": true}'
            self._history.append({"role": "assistant", "content": fallback_json})
            if len(self._history) > 20:
                self._history = self._history[-20:]
            self._signals.error.emit(f"未知错误：{e}")

    def _on_thinking(self):
        self._append_msg("肥鸡", "……（正在想）", color="#aaa")

    def _on_reply(self, payload: dict):
        # 移除"正在想"占位行
        self._remove_last_thinking()

        # 只显示 text，其余字段后台处理
        self._append_msg("肥鸡", payload["text"])
        self._send_btn.setEnabled(True)

        # TTS 先启动（后台线程，不阻塞），让朗读尽快开始
        if self._voice_enabled and payload.get("tts", True):
            self.tts.speak(payload["text"])

        # 亲密度：基础 +2，关键词加成
        if self.pet is not None:
            delta = 2
            for kw, bonus in _AFFECTION_KEYWORDS.items():
                if kw in self._last_user_text:
                    delta += bonus
                    break
            self.pet.add_affection(delta)

        # 触发动画
        if self.pet is not None:
            try:
                apply_llm_command(self.pet, payload)
            except Exception:
                pass

        # 重置自动关闭计时
        self._reset_inactivity_timer()

    def _on_error(self, msg: str):
        self._remove_last_thinking()
        self._append_msg("系统", f"啾……{msg}", color="#999")
        self._send_btn.setEnabled(True)

        # 错误时播放 idle_anim 兜底
        if self.pet is not None:
            try:
                apply_llm_command(self.pet, {
                    "animation": "idle_anim",
                    "emotion": "normal",
                })
            except Exception:
                pass

    def _append_msg(self, sender: str, text: str, color: str = None):
        if color is None:
            color = "#ff7744" if sender == "肥鸡" else ("#4488cc" if sender == "你" else "#999")
        html = f'<span style="color:{color};font-weight:bold;">{sender}：</span>{text}<br>'
        self._chat_area.append(html)
        sb = self._chat_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _remove_last_thinking(self):
        """移除最后一条"正在想"占位消息。"""
        cursor = self._chat_area.textCursor()
        from PyQt5.QtGui import QTextCursor
        cursor.movePosition(QTextCursor.End)
        cursor.select(QTextCursor.BlockUnderCursor)
        if "正在想" in cursor.selectedText():
            cursor.removeSelectedText()
            # 删除多余空行
            cursor.movePosition(QTextCursor.End)
            cursor.select(QTextCursor.BlockUnderCursor)
            if cursor.selectedText().strip() == "":
                cursor.removeSelectedText()

    def _on_stop_sing(self):
        if self.pet is not None:
            try:
                self.pet.stop_sing()
            except Exception:
                pass
        self._stop_sing_btn.hide()

    def show_singing_controls(self, visible: bool):
        """唱歌开始时显示取消按钮，结束时隐藏。"""
        if visible:
            self._stop_sing_btn.show()
        else:
            self._stop_sing_btn.hide()

    def show_near(self, cx: int, cy: int):
        self.adjustSize()
        x = cx - self.width() // 2
        y = cy - self.height() - 10
        try:
            screen = self.screen().geometry()
            x = max(0, min(x, screen.width() - self.width()))
            y = max(0, y)
        except Exception:
            pass
        self.move(x, y)
        self.show()
        self._msg_input.setFocus()
        self._reset_inactivity_timer()

    def show_greeting(self, speak: bool = True):
        """显示本地固定开场白，不走 DeepSeek，不加入 LLM history。"""
        self._append_msg("肥鸡", GREETING_TEXT)
        self._reset_inactivity_timer()
        if speak and self._voice_enabled:
            try:
                # 开场白固定文本，使用缓存版本，第二次启动几乎立即播放
                self.tts.speak_cached(GREETING_TEXT)
            except Exception:
                pass

    def update_affection_label(self, value: int):
        self._affection_label.setText(f"❤️ 亲密度 {value}")

    # ── 自动关闭 ──────────────────────────────────────────────

    def _on_input_focus_in(self, event):
        self._inactivity_timer.stop()
        QLineEdit.focusInEvent(self._msg_input, event)

    def _reset_inactivity_timer(self):
        """任何活跃操作都重置 10 秒计时。"""
        self._inactivity_timer.start(10_000)

    def _on_inactivity(self):
        """10 秒无操作且鼠标不在窗口内时自动关闭。"""
        if not self._mouse_inside and not self._msg_input.hasFocus():
            self.hide()

    def enterEvent(self, event):
        self._mouse_inside = True
        self._inactivity_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._mouse_inside = False
        # 鼠标离开后开始倒计时
        if not self._msg_input.hasFocus():
            self._reset_inactivity_timer()
        super().leaveEvent(event)
