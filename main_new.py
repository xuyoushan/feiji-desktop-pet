"""
主程序 - 整合新动画系统和行为引擎
"""

import sys
import math
from pathlib import Path
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel,
                              QSystemTrayIcon, QMenu, QAction)
from PyQt5.QtCore import Qt, QTimer, QPoint
from PyQt5.QtGui import QCursor, QIcon

from animation_new import AnimationManager, AnimGroup, ANIM_SEQUENCES
from behavior_new import BehaviorEngine, _load_size_mode, _save_state as _save_pet_state
from tts_engine import TTSEngine
from chat_window import ChatWindow
from hover_buttons import HoverButtons
from music_player import MusicPlayer
from app_paths import FRAMES_DIR, ICON_PATH

PHOTO_DIR  = FRAMES_DIR
PET_SIZE   = 158   # 基准尺寸（中档）

# 三档比例
SIZE_MODES = {
    "small":  int(PET_SIZE * 0.85),   # 134
    "medium": PET_SIZE,                # 158
    "large":  int(PET_SIZE * 1.20),   # 189
}
SIZE_LABELS = {"small": "小", "medium": "中", "large": "大"}
SIZE_HINTS  = {
    "small":  "啾，我缩小一点点啦！",
    "medium": "啾，保持刚刚好的大小。",
    "large":  "啾，我变大一点啦！",
}

# ── 入场动画参数（可微调）──────────────────────────────────────
LAND_X_OFFSET           = 100   # 落点距可用区域右边距
LAND_Y_OFFSET           = 60    # 落点距可用区域底边距
FLIGHT_DURATION_MS      = 2500  # 飞行总时长（毫秒）
GREETING_DELAY_MS       = 800   # 落地后到开场白的停顿
ENTRANCE_START_OFFSET_X = 80    # 起点 X：从左上角外侧偏移进来一点
ENTRANCE_START_OFFSET_Y = 80    # 起点 Y：从左上角外侧偏移进来一点


def _load_icon() -> QIcon:
    """加载图标，文件不存在时返回空 QIcon 不崩溃。"""
    if ICON_PATH.exists():
        return QIcon(str(ICON_PATH))
    print(f"[INFO] Icon not found: {ICON_PATH}，使用默认图标")
    return QIcon()


class PetWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAcceptDrops(True)

        # 程序图标
        self._icon = _load_icon()
        if not self._icon.isNull():
            self.setWindowIcon(self._icon)

        # 使用 availableGeometry 避开任务栏
        avail = QApplication.primaryScreen().availableGeometry()
        self._avail = avail
        self.screen_w = avail.width()
        self.screen_h = avail.height()

        # 从持久化读取尺寸档位
        self._size_mode = _load_size_mode()
        self.pet_size = SIZE_MODES[self._size_mode]
        self.setFixedSize(self.pet_size, self.pet_size)

        # 计算落点（随机位置）
        import random as _rand
        self._land_x = _rand.randint(0, max(0, avail.width() - self.pet_size))
        self._land_y = _rand.randint(0, max(0, avail.height() - self.pet_size))

        # 入场起点（左上角外侧，稍微偏进来一点让用户能看到飞入）
        self._enter_x = avail.x() - self.pet_size + ENTRANCE_START_OFFSET_X
        self._enter_y = avail.y() - self.pet_size + ENTRANCE_START_OFFSET_Y

        # 图片标签
        self.label = QLabel(self)
        self.label.setFixedSize(self.pet_size, self.pet_size)
        self.label.setAlignment(Qt.AlignCenter)

        # 动画管理器：启动时只加载当前尺寸，其他档位后台预热
        self.anim_mgr = AnimationManager(
            PHOTO_DIR, self.pet_size,
            all_sizes=list(SIZE_MODES.values())
        )

        # 行为引擎
        self.behavior = BehaviorEngine(self)

        # TTS引擎
        self.tts = TTSEngine()

        # 音乐播放器
        self.music = MusicPlayer(self)

        # 悬停按钮
        self.hover_btns = HoverButtons(self)
        self.hover_btns.hide()

        # 对话窗口
        self.chat_win = ChatWindow(self.tts, pet=self)
        self.chat_win.hide()
        # 同步从文件加载的亲密度到标签
        self.chat_win.update_affection_label(int(self.behavior.affection))

        # 系统托盘
        self._setup_tray()

        # 拖拽状态
        self._drag_pos = None
        self._is_dragging = False

        # 悬停检测定时器
        self._hover_timer = QTimer(self)
        self._hover_timer.timeout.connect(self._check_hover)
        self._hover_timer.start(100)
        self._mouse_over = False

        # 主定时器：每40ms tick一次（25fps）
        self._TICK_MS = 40
        self._behavior_accum = 0
        self._main_timer = QTimer(self)
        self._main_timer.timeout.connect(self._tick)
        self._main_timer.start(self._TICK_MS)

        # 入场状态
        self._entering = True
        self._fly_elapsed = 0
        self._fly_timer = QTimer(self)
        self._fly_timer.timeout.connect(self._fly_step)

        # 初始位置：左上角外侧
        self.move(self._enter_x, self._enter_y)

        # 立即设置飞行动画，确保 show() 时就是飞行状态
        self.anim_mgr.set_facing(-1)
        if AnimGroup.HOP_B in self.anim_mgr._cache:
            self.anim_mgr.play_single(AnimGroup.HOP_B, loop=True)

        # 延迟 0ms 启动入场（让事件循环先处理 show，再开始飞行序列）
        QTimer.singleShot(0, self._start_entrance)

    # ── 系统托盘 ──────────────────────────────────────────────

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(self)
        if not self._icon.isNull():
            self._tray.setIcon(self._icon)
        self._tray.setToolTip("肥鸡")

        menu = QMenu()
        act_show = QAction("显示肥鸡", self)
        act_show.triggered.connect(self._tray_show)
        act_hide = QAction("隐藏肥鸡", self)
        act_hide.triggered.connect(self._tray_hide)

        # 大小子菜单
        size_menu = QMenu("肥鸡大小", menu)
        self._size_actions = {}
        for mode in ("large", "medium", "small"):
            act = QAction(SIZE_LABELS[mode], self)
            act.setCheckable(True)
            act.setChecked(mode == self._size_mode)
            act.triggered.connect(lambda checked, m=mode: self._set_size(m))
            size_menu.addAction(act)
            self._size_actions[mode] = act

        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self._quit_app)

        menu.addAction(act_show)
        menu.addAction(act_hide)
        menu.addSeparator()
        menu.addMenu(size_menu)
        menu.addSeparator()
        menu.addAction(act_quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:  # 左键单击
            if self.isVisible():
                self._tray_hide()
            else:
                self._tray_show()

    def _tray_show(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _tray_hide(self):
        self.hide()
        self.hover_btns.hide()
        self.chat_win.hide()

    def _set_size(self, mode: str):
        if mode == self._size_mode:
            return
        self._size_mode = mode
        # 更新勾选状态
        for m, act in self._size_actions.items():
            act.setChecked(m == mode)
        # 持久化
        b = self.behavior
        _save_pet_state(b.affection, b.mood, b.energy, b.hunger, size_mode=mode)
        # 应用新尺寸
        self._apply_size()
        # 轻反馈
        self._show_pin_hint(SIZE_HINTS[mode])

    def _apply_size(self):
        new_size = SIZE_MODES[self._size_mode]
        self.pet_size = new_size

        # 重新计算落点和入场起点
        avail = self._avail
        self._land_x = avail.x() + avail.width()  - new_size - LAND_X_OFFSET
        self._land_y = avail.y() + avail.height() - new_size - LAND_Y_OFFSET
        self._enter_x = avail.x() - new_size + ENTRANCE_START_OFFSET_X
        self._enter_y = avail.y() - new_size + ENTRANCE_START_OFFSET_Y

        # 调整窗口和标签尺寸
        self.setFixedSize(new_size, new_size)
        self.label.setFixedSize(new_size, new_size)

        # 重新缩放动画帧缓存
        self.anim_mgr.resize(new_size)

        # 把肥鸡当前位置夹回屏幕内
        pos = self.pos()
        clamped_x = max(0, min(pos.x(), self.screen_w - new_size))
        clamped_y = max(0, min(pos.y(), self.screen_h - new_size))
        self.move(clamped_x, clamped_y)

        # 刷新当前帧
        self._update_frame()

    def _quit_app(self):
        """彻底退出：停止音乐/TTS，关闭所有窗口，退出事件循环。"""
        try:
            self.music.stop_immediately()
        except Exception:
            pass
        try:
            self.tts.stop()
        except Exception:
            pass
        self._main_timer.stop()
        self._hover_timer.stop()
        self._fly_timer.stop()
        self._tray.hide()
        self.chat_win.close()
        self.hover_btns.close()
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        """点击窗口关闭按钮时隐藏到托盘，不退出程序。"""
        event.ignore()
        self._tray_hide()

    # ── 入场动画流程 ──────────────────────────────────────────

    def _start_entrance(self):
        self._start_fly()

    def _start_fly(self):
        if AnimGroup.HOP_B in self.anim_mgr._cache:
            self.anim_mgr.play_single(AnimGroup.HOP_B, loop=True)
        elif AnimGroup.START_A in self.anim_mgr._cache:
            self.anim_mgr.play_single(AnimGroup.START_A, loop=True)
        self._fly_elapsed = 0
        self._fly_timer.start(self._TICK_MS)

    def _fly_step(self):
        self._fly_elapsed += self._TICK_MS
        t = min(1.0, self._fly_elapsed / FLIGHT_DURATION_MS)

        eased = t * t * (3.0 - 2.0 * t)

        x = int(self._enter_x + (self._land_x - self._enter_x) * eased)
        y = int(self._enter_y + (self._land_y - self._enter_y) * eased)

        arc = int(math.sin(t * math.pi) * -30)
        y += arc

        self.move(x, y)

        if t >= 1.0:
            self._fly_timer.stop()
            self._start_landing()

    def _start_landing(self):
        self.anim_mgr.set_facing(1)
        if "landing" in ANIM_SEQUENCES:
            self.anim_mgr.play_sequence("landing", loop=False, on_finish=self._on_landed)
        else:
            self._on_landed()

    def _on_landed(self):
        self.move(self._land_x, self._land_y)

        if AnimGroup.START_A in self.anim_mgr._cache:
            self.anim_mgr.play_single(AnimGroup.START_A, loop=True)
        elif AnimGroup.START_B in self.anim_mgr._cache:
            self.anim_mgr.play_single(AnimGroup.START_B, loop=True)

        self._entering = False

        # 落地后后台预热其他尺寸缓存，不阻塞 UI
        self.anim_mgr.start_background_warmup()

        # 每次启动都显示开场白
        QTimer.singleShot(GREETING_DELAY_MS, self._show_greeting)

    def _show_greeting(self):
        try:
            self.chat_win.show_greeting(speak=True)
        except Exception as e:
            print(f"[WARN] Greeting failed: {e}")

    # ── 主循环 ────────────────────────────────────────────────

    def _tick(self):
        needs_redraw = self.anim_mgr.tick(self._TICK_MS)
        if needs_redraw:
            self._update_frame()

        if self._entering:
            return

        self.behavior.move_tick(self._TICK_MS)

        self._behavior_accum += self._TICK_MS
        if self._behavior_accum >= 1000:
            self._behavior_accum = 0
            self.behavior.tick()

    def _update_frame(self):
        pixmap = self.anim_mgr.current_frame()
        if pixmap:
            self.label.setPixmap(pixmap)

    def _reposition_chat(self):
        if hasattr(self, 'chat_win') and self.chat_win.isVisible():
            self.show_chat()

    def moveEvent(self, event):
        super().moveEvent(event)
        new_pos = event.pos()
        old_pos = getattr(self, '_last_chat_reposition', None)
        if old_pos is None or (abs(new_pos.x() - old_pos.x()) > 5 or abs(new_pos.y() - old_pos.y()) > 5):
            self._last_chat_reposition = new_pos
            self._reposition_chat()

    def _check_hover(self):
        if self._entering:
            return

        cursor = QCursor.pos()
        geo = self.geometry()
        over = geo.contains(cursor)

        if over and not self._mouse_over:
            self._mouse_over = True
            self._show_hover_buttons()
        elif not over and self._mouse_over:
            btn_geo = self.hover_btns.geometry()
            global_btn = self.hover_btns.mapToGlobal(QPoint(0, 0))
            from PyQt5.QtCore import QRect
            btn_rect = QRect(global_btn, self.hover_btns.size())
            if not btn_rect.contains(cursor):
                self._mouse_over = False
                self._hide_hover_buttons()

    def _show_hover_buttons(self):
        self.hover_btns.show_at(self)

    def _hide_hover_buttons(self):
        self.hover_btns.hide()

    # ── 拖拽文件投喂 ──────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        event.acceptProposedAction()
        if self._entering:
            return
        # 将拖入的文件移入回收站
        from behavior_new import send_to_recycle_bin
        for url in event.mimeData().urls():
            filepath = url.toLocalFile()
            if filepath:
                send_to_recycle_bin(filepath)
        self.feed_file()

    # ── 拖拽（鼠标移动宠物）──────────────────────────────────

    def mousePressEvent(self, event):
        if self._entering:
            event.ignore()
            return
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            self._is_dragging = False
            event.accept()

    def mouseMoveEvent(self, event):
        if self._entering:
            event.ignore()
            return
        if event.buttons() & Qt.LeftButton and self._drag_pos:
            if not self._is_dragging:
                self._is_dragging = True
                self.behavior.on_grabbed()
                self._hide_hover_buttons()
                self._hover_timer.stop()
            new_pos = event.globalPos() - self._drag_pos
            new_pos.setX(max(0, min(new_pos.x(), self.screen_w - self.pet_size)))
            new_pos.setY(max(0, min(new_pos.y(), self.screen_h - self.pet_size)))
            dx = new_pos.x() - self.pos().x()
            if abs(dx) > 2:
                self.face_direction(dx)
            self.move(new_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._entering:
            event.ignore()
            return
        if event.button() == Qt.LeftButton:
            if self._is_dragging:
                self.behavior.on_released()
                self._hover_timer.start(100)
            self._drag_pos = None
            self._is_dragging = False
            event.accept()

    # ── 公共接口 ──────────────────────────────────────────────

    def get_pos(self):
        return self.pos()

    def set_pos(self, x, y):
        x = max(0, min(x, self.screen_w - self.pet_size))
        y = max(0, min(y, self.screen_h - self.pet_size))
        self.move(x, y)

    def face_direction(self, dx: float):
        if dx > 0:
            self.anim_mgr.set_facing(-1)
        elif dx < 0:
            self.anim_mgr.set_facing(1)

    def show_chat(self):
        geo = self.geometry()
        cx = geo.x() + geo.width() // 2
        cy = geo.y()
        self.chat_win.show_near(cx, cy)

    def feed(self):
        self.behavior.on_feed()

    def feed_file(self):
        self.behavior.on_feed_file()

    def add_affection(self, delta: int):
        self.behavior.add_affection(delta)

    def sing(self):
        self.behavior.on_sing()

    def stop_sing(self):
        self.music.stop_immediately()
        if self.behavior._state == "interact":
            self.behavior._state = "idle"
            self.anim_mgr.play_single(AnimGroup.START_B, loop=True)

    def pet(self):
        self.behavior.on_pet()

    def fly(self):
        self.behavior.on_fly()

    def nap(self):
        self.behavior.on_nap()

    def mischief(self):
        self.behavior.on_mischief()

    def startle(self):
        self.behavior.on_startle()

    def toggle_movement(self):
        enabled = not self.behavior.movement_enabled
        self.behavior.movement_enabled = enabled
        if not enabled:
            self.behavior.stop_movement()
        else:
            self.behavior._show_happy()

    def _show_pin_hint(self, text: str):
        try:
            self.show_chat()
            self.chat_win._append_msg("肥鸡", text)
        except Exception:
            pass

    def toggle_mischief(self, enabled):
        self.behavior.mischief_enabled = enabled

    def paintEvent(self, event):
        pass


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 应用级图标（任务栏/Alt+Tab）
    icon = _load_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = PetWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
