from PyQt5.QtWidgets import QWidget, QPushButton, QHBoxLayout, QLabel
from PyQt5.QtCore import Qt, QPoint, QRect, QTimer
from PyQt5.QtGui import QFont


BTN_STYLE = """
    QPushButton {
        background: rgba(255,255,255,210);
        border: 1px solid rgba(200,200,200,180);
        border-radius: 18px;
        font-size: 18px;
        padding: 4px;
        min-width: 36px;
        min-height: 36px;
    }
    QPushButton:hover {
        background: rgba(255,200,150,230);
        border: 1px solid #ff9966;
    }
"""

PIN_STYLE_ON = """
    QPushButton {
        background: rgba(255,255,255,210);
        border: 1px solid rgba(200,200,200,180);
        border-radius: 18px;
        font-size: 18px;
        padding: 4px;
        min-width: 36px;
        min-height: 36px;
    }
    QPushButton:hover {
        background: rgba(255,200,150,230);
        border: 1px solid #ff9966;
    }
"""

PIN_STYLE_OFF = """
    QPushButton {
        background: rgba(180,220,255,230);
        border: 2px solid #66aaff;
        border-radius: 18px;
        font-size: 18px;
        padding: 4px;
        min-width: 36px;
        min-height: 36px;
    }
    QPushButton:hover {
        background: rgba(140,200,255,240);
        border: 2px solid #3388ff;
    }
"""


class HoverButtons(QWidget):
    def __init__(self, pet):
        super().__init__(pet.parent() if pet.parent() else None)
        self.pet = pet
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedHeight(48)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # 亲密度标签
        self._aff_label = QLabel("❤️50")
        self._aff_label.setStyleSheet("""
            QLabel {
                background: rgba(255,255,255,210);
                border: 1px solid rgba(200,200,200,180);
                border-radius: 14px;
                font-size: 12px; color: #e55;
                padding: 2px 8px;
            }
        """)
        self._aff_label.setFixedHeight(36)
        layout.addWidget(self._aff_label)

        buttons = [
            ("🍎", "喂零食", self._on_feed),
            ("🎵", "唱歌",   self._on_sing),
            ("🤚", "摸摸",   self._on_pet),
            ("💬", "对话",   self._on_chat),
            ("✈️", "飞行",   self._on_fly),
            ("😈", "捣乱",   self._on_mischief),
            ("😱", "受惊",   self._on_startle),
        ]
        for icon, tip, cb in buttons:
            btn = QPushButton(icon)
            btn.setToolTip(tip)
            btn.setStyleSheet(BTN_STYLE)
            btn.setFixedSize(40, 40)
            btn.clicked.connect(cb)
            layout.addWidget(btn)

        # 曲别针按钮（移动开关）
        self._pin_btn = QPushButton("📎")
        self._pin_btn.setToolTip("固定/取消固定（开关自主移动）")
        self._pin_btn.setStyleSheet(PIN_STYLE_ON)
        self._pin_btn.setFixedSize(40, 40)
        self._pin_btn.clicked.connect(self._on_pin)
        layout.addWidget(self._pin_btn)

        self.adjustSize()

        # 鼠标离开检测
        self._hide_timer = QTimer(self)
        self._hide_timer.timeout.connect(self._check_hide)
        self._hide_timer.start(120)

    def show_at(self, pet_widget):
        geo = pet_widget.geometry()
        x = geo.x() + geo.width() // 2 - self.width() // 2
        y = geo.y() - self.height() - 4
        if y < 0:
            y = geo.y() + geo.height() + 4
        self.move(x, y)
        self.show()
        self.raise_()
        # 同步按钮状态和亲密度
        self._sync_pin_style()
        self.update_affection(int(self.pet.behavior.affection))

    def update_affection(self, value: int):
        self._aff_label.setText(f"❤️{value}")

    def _sync_pin_style(self):
        pinned = not self.pet.behavior.movement_enabled
        self._pin_btn.setStyleSheet(PIN_STYLE_OFF if pinned else PIN_STYLE_ON)
        self._pin_btn.setToolTip("取消固定（恢复自主移动）" if pinned else "固定（停止自主移动）")

    def _check_hide(self):
        if not self.isVisible():
            return
        from PyQt5.QtGui import QCursor
        cursor = QCursor.pos()
        pet_geo = self.pet.geometry()
        btn_geo = QRect(self.pos(), self.size())
        if not pet_geo.contains(cursor) and not btn_geo.contains(cursor):
            self.hide()

    def _on_feed(self):
        self.pet.feed()

    def _on_sing(self):
        self.pet.sing()

    def _on_pet(self):
        self.pet.pet()

    def _on_chat(self):
        self.pet.show_chat()

    def _on_fly(self):
        self.pet.fly()

    def _on_mischief(self):
        self.pet.mischief()

    def _on_startle(self):
        self.pet.startle()

    def _on_pin(self):
        self.pet.toggle_movement()
        self._sync_pin_style()
