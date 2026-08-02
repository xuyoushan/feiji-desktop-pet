"""
行为引擎 - 模拟真实宠物行为
基于心情、饥饿、精力等状态驱动行为
"""

import json
import random
import ctypes
from ctypes import wintypes
from pathlib import Path
from PyQt5.QtCore import QTimer
from animation_new import AnimationManager, ANIM_SEQUENCES, AnimGroup, LOOP_ANIMS
from app_paths import STATE_FILE
DEFAULT_STATE = {"affection": 50, "mood": 80, "energy": 70, "hunger": 30}
DEFAULT_SIZE_MODE = "medium"

# 肥鸡会「吃掉」的文件扩展名
_EDIBLE_EXTS = {'.txt', '.md', '.doc', '.docx', '.pdf', '.pptx', '.jpg', '.png'}


def send_to_recycle_bin(filepath: str) -> bool:
    """将文件移动到 Windows 回收站（不永久删除）。"""
    try:
        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", wintypes.WORD),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p),
            ]

        FO_DELETE = 0x0003
        FOF_ALLOWUNDO = 0x0040
        FOF_NOCONFIRMATION = 0x0010
        FOF_SILENT = 0x0004

        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        op.pFrom = str(filepath) + '\0'
        op.pTo = None
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
        op.fAnyOperationsAborted = False
        op.hNameMappings = None
        op.lpszProgressTitle = None

        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return result == 0
    except Exception as e:
        print(f"[Mischief] send_to_recycle_bin failed: {e}")
        return False


def _find_edible_files() -> list:
    """搜索可被肥鸡「吃掉」的文件，仅扫描指定目录（非递归）。"""
    home = Path.home()

    search_dirs = [
        home / 'Desktop',
        home / 'Documents',
        home / 'Downloads',
        Path(r'D:\Edge Download'),
        Path(r'D:\Users\Friendly_Xu\Desktop\_'),
    ]

    files = []
    for d in search_dirs:
        if not d.exists():
            continue
        try:
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() in _EDIBLE_EXTS:
                    files.append(f)
        except (PermissionError, OSError):
            continue
    return files


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return {k: float(data.get(k, DEFAULT_STATE[k])) for k in DEFAULT_STATE}
    except Exception:
        pass
    return dict(DEFAULT_STATE)


def _load_size_mode() -> str:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            mode = data.get("size_mode", DEFAULT_SIZE_MODE)
            if mode in ("small", "medium", "large"):
                return mode
    except Exception:
        pass
    return DEFAULT_SIZE_MODE


def _save_state(affection: float, mood: float, energy: float, hunger: float,
                size_mode: str = None):
    try:
        # 读取现有文件保留 size_mode
        existing = {}
        if STATE_FILE.exists():
            try:
                existing = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.update({
            "affection": round(affection, 1),
            "mood":      round(mood, 1),
            "energy":    round(energy, 1),
            "hunger":    round(hunger, 1),
        })
        if size_mode is not None:
            existing["size_mode"] = size_mode
        STATE_FILE.write_text(
            json.dumps(existing, ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"[State] save failed: {e}")


class BehaviorEngine:
    def __init__(self, pet):
        self.pet = pet

        # 从持久化文件读取状态
        _s = _load_state()

        # 状态值（0-100）
        self.mood      = _s["mood"]
        self.hunger    = _s["hunger"]
        self.energy    = _s["energy"]
        self.affection = _s["affection"]   # 亲密度，持久化保存

        # 行为状态
        self._state = "idle"    # idle, walk, fly, sleep, interact, mischief
        self._state_timer = 0   # 当前状态持续时间
        self._idle_timer = 0

        # 行走相关
        self._walk_dir = 1
        self._last_walk_dir = 0        # 上次走路方向，0 表示无记录
        self._walk_dist_rem = 0.0   # 剩余行走距离（px）
        self._walk_turn_chance = 0.1
        self._walk_warmup = False   # 走路第一帧跳过移动，让动画先就位

        # 飞行相关
        self._fly_phase = "none"    # none, hop_a1, hop_a2, cruise, land_c1, land_c2
        self._fly_target_x = 0
        self._fly_dir = 1
        self._fly_landing_start_x = 0
        self._fly_hop_a1_moved = 0.0
        self._fly_phase_y_offset = 0.0  # 起跳/降落时的 y 偏移累积

        # 速度（px/sec）
        self.WALK_SPEED = 86.0
        self.FLY_SPEED  = self.WALK_SPEED * 3.5   # 巡飞速度 = 走路 3.5 倍
        self.FLY_LIFT_SPEED = self.FLY_SPEED * 0.5  # 起跳/降落阶段速度略慢

        # 捣乱模式
        self.mischief_enabled = True
        self._mischief_cooldown = 0

        # 低亲密度提醒冷却（秒）
        self._low_affection_cd = 0

        # 防护标志：仅手动点击捣乱按钮时才允许吃文件
        self._manual_mischief = False

        # 鼠标停留计时
        self._hover_timer = 0

        # 按钮交互锁：True=某个按钮动画正在播放，阻止其他按钮打断
        self._user_interacting = False

        # 移动开关
        self.movement_enabled = True

    # ═══════════════════════════════════════════════════════════
    # 主循环 tick（每秒调用）：只做状态决策，不移动位置
    # ═══════════════════════════════════════════════════════════

    def tick(self):
        self._update_stats()
        self._state_timer += 1
        self._mischief_cooldown = max(0, self._mischief_cooldown - 1)
        self._low_affection_cd = max(0, self._low_affection_cd - 1)

        # 亲密度低于 20 时自动提醒（5分钟冷却）
        if self.affection < 20 and self._low_affection_cd == 0 and self._state == "idle":
            self._low_affection_cd = 300
            self._show_low_affection_reminder()

        if self._state == "walk":
            pass  # 位置由 move_tick 处理
        elif self._state == "fly":
            pass  # 位置由 move_tick 处理
        elif self._state == "sleep":
            pass
        elif self._state == "interact":
            pass
        elif self._state == "mischief":
            pass
        elif self._state == "grabbed":
            pass
        else:  # idle
            self._check_hover_stay()
            self._do_idle()

    def move_tick(self, dt_ms: int):
        """每帧调用（40ms），只负责位置平滑移动"""
        if self._state == "walk":
            self._do_walk(dt_ms)
        elif self._state == "fly":
            self._do_fly(dt_ms)

    def _update_stats(self):
        """状态自然衰减"""
        self.mood      = max(0, self.mood - 0.5)
        self.hunger    = min(100, self.hunger + 0.3)
        self.energy    = max(0, self.energy - 0.2)
        self.affection = max(0, self.affection - 0.006)  # 约每2.8分钟-1

    # ═══════════════════════════════════════════════════════════
    # 待机行为决策
    # ═══════════════════════════════════════════════════════════

    def _check_hover_stay(self):
        """鼠标停留在宠物上时，每隔一段时间触发一个互动动画。"""
        from PyQt5.QtGui import QCursor
        cursor = QCursor.pos()
        geo = self.pet.geometry()
        if not geo.contains(cursor):
            self._hover_timer = 0
            return

        self._hover_timer += 1
        if self._hover_timer >= 2:
            self._hover_timer = 0
            action = random.choice(["look", "happy", "like", "snicker"])
            if action == "look":
                self._look_at_owner()
            elif action == "happy":
                self._show_happy()
            elif action == "like":
                self._show_like()
            elif action == "snicker":
                self._play_snicker()

    def _do_idle(self):
        self._idle_timer += 1

        # 检查是否需要睡觉（精力低）
        if self.energy < 20 and random.random() < 0.3:
            self._enter_sleep()
            return

        # 检查是否饿了（会主动找食物/看向主人）
        if self.hunger > 70 and random.random() < 0.2:
            self._look_at_owner()
            return

        # 心情差且允许捣乱
        if self.mood < 30 and self.mischief_enabled and self._mischief_cooldown == 0:
            if random.random() < 0.15:
                self._do_mischief()
                return

        # 随机行为决策（根据状态加权）
        if self._idle_timer >= random.randint(3, 8):
            self._idle_timer = 0
            self._pick_random_behavior()

    def _pick_random_behavior(self):
        """根据当前状态智能选择行为"""
        choices = []

        # 精力高：更倾向活跃行为
        if self.energy > 60:
            choices += [
                ("walk", 38),
                ("fly", 18),
                ("happy", 15 if self.mood > 60 else 5),
                ("look", 10),
            ]
        else:
            choices += [
                ("walk", 20),
                ("idle_anim", 20),
                ("nap", 15),
            ]

        # 心情好：更多开心表现
        if self.mood > 70:
            choices += [("happy", 20), ("sing", 10)]
        elif self.mood < 40:
            choices += [("snicker", 5)]

        # 饥饿：更多看向主人
        if self.hunger > 60:
            choices += [("look", 20)]

        # 亲密度高（>=70）：更活跃、更黏人
        if self.affection >= 70:
            choices += [
                ("happy", 15),
                ("like", 15),
                ("sing", 8),
                ("snicker", 8),
                ("look", 10),
                ("fly", 10),
            ]
        # 亲密度低（<40）：更容易发呆/小睡
        elif self.affection < 40:
            choices += [
                ("idle_anim", 25),
                ("nap", 20),
                ("look", 10),
            ]
        else:
            # 中等亲密度：正常
            choices += [("like", 10)]

        # 基础待机
        choices += [("idle_anim", 30)]

        # 加权随机
        actions, weights = zip(*choices)
        action = random.choices(actions, weights=weights, k=1)[0]

        self._execute_action(action)

    def _execute_action(self, action: str):
        if action in ("walk", "fly") and not self.movement_enabled:
            self._play_idle_anim()
            return
        if action == "walk":
            self._start_walk()
        elif action == "fly":
            self.add_affection(-1)  # 自动飞行消耗
            self._start_fly()
        elif action == "happy":
            self._show_happy()
        elif action == "look":
            self._look_at_owner()
        elif action == "idle_anim":
            self._play_idle_anim()
        elif action == "nap":
            self._take_nap()
        elif action == "sing":
            self._start_sing()
        elif action == "snicker":
            self._play_snicker()
        elif action == "like":
            self._show_like()

    # ═══════════════════════════════════════════════════════════
    # 具体行为实现
    # ═══════════════════════════════════════════════════════════

    def _start_walk(self):
        self._state = "walk"
        self._walk_dist_rem = float(random.randint(200, 500))
        # 上次有记录时，70% 概率往反方向走，避免一直往同一边堆
        if self._last_walk_dir != 0 and random.random() < 0.7:
            self._walk_dir = -self._last_walk_dir
        else:
            self._walk_dir = random.choice([-1, 1])
        self._walk_warmup = True
        self.pet.face_direction(self._walk_dir)
        self.pet.anim_mgr.play_single(AnimGroup.WALK_A, loop=True)

    def _do_walk(self, dt_ms: int):
        if self._walk_warmup:
            self._walk_warmup = False
            return  # 第一帧只让动画就位，不移动

        if self._walk_dist_rem <= 0:
            self._last_walk_dir = self._walk_dir   # 记录本次方向
            self._state = "idle"
            self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
            return

        step = self.WALK_SPEED * dt_ms / 1000.0
        pos = self.pet.get_pos()
        new_x = pos.x() + self._walk_dir * step

        # 碰边反向
        if new_x <= 0 or new_x >= self.pet.screen_w - self.pet.pet_size:
            self._walk_dir *= -1
            self.pet.face_direction(self._walk_dir)
            new_x = pos.x() + self._walk_dir * step

        self.pet.set_pos(int(new_x), pos.y())
        self._walk_dist_rem -= step

    # 降落阶段开始距目标的距离（px）
    LANDING_THRESHOLD = 60
    # hop_a1 最大前进距离（px），只负责起跳，不跑远
    HOP_A1_FORWARD_PX = 30

    def _fly_reached_or_passed(self, cur_x: int) -> bool:
        """判断当前位置是否已到达或越过 landing_start_x。"""
        if self._fly_dir > 0:
            return cur_x >= self._fly_landing_start_x
        else:
            return cur_x <= self._fly_landing_start_x

    def _fly_clamp_before_target(self, new_x: int) -> int:
        """把 new_x 夹在不越过 landing_start_x 的范围内。"""
        if self._fly_dir > 0:
            return min(new_x, self._fly_landing_start_x)
        else:
            return max(new_x, self._fly_landing_start_x)

    def _fly_y_at_x(self, cur_x: int) -> int:
        """根据当前 X 在飞行起点→目标连线上的比例，计算对应 Y。"""
        total_x = self._fly_target_x - self._fly_start_x
        if total_x == 0:
            return self._fly_start_y
        ratio = (cur_x - self._fly_start_x) / total_x
        y = int(self._fly_start_y + (self._fly_target_y - self._fly_start_y) * ratio)
        return max(0, min(y, self.pet.screen_h - self.pet.pet_size))

    def _start_fly(self):
        self._state = "fly"
        pos = self.pet.get_pos()
        fly_dir = random.choice([-1, 1])
        # 随机飞行距离：从当前距离到屏幕宽度不等
        max_dist = self.pet.screen_w - self.pet.pet_size
        fly_dist = random.randint(min(200, max_dist), max_dist)
        self._fly_target_x = max(0, min(pos.x() + fly_dir * fly_dist,
                                        self.pet.screen_w - self.pet.pet_size))
        self._fly_dir = 1 if self._fly_target_x > pos.x() else -1
        # 随机 Y 目标，实现斜向飞行
        self._fly_target_y = random.randint(0, self.pet.screen_h - self.pet.pet_size)
        self._fly_start_x = pos.x()
        self._fly_start_y = pos.y()
        # 降落起始点：目标前方 LANDING_THRESHOLD px（方向感知）
        self._fly_landing_start_x = self._fly_target_x - self._fly_dir * self.LANDING_THRESHOLD
        self._fly_phase_y_offset = 0.0
        # hop_a1 起跳位移上限
        self._fly_hop_a1_moved = 0.0
        self.pet.face_direction(self._fly_dir)

        # 进入起跳第一段
        self._fly_phase = "hop_a1"
        self.pet.anim_mgr.play_single(AnimGroup.HOP_A1, loop=False,
                                      on_finish=self._fly_enter_hop_a2)

    def _fly_enter_hop_a2(self):
        if self._state != "fly":
            return
        self._fly_phase = "hop_a2"
        self.pet.anim_mgr.play_single(AnimGroup.HOP_A2, loop=False,
                                      on_finish=self._fly_enter_cruise)

    def _fly_enter_cruise(self):
        if self._state != "fly":
            return
        self._fly_phase = "cruise"
        self.pet.anim_mgr.play_single(AnimGroup.HOP_B, loop=True)

    def _fly_enter_land_c1(self):
        if self._state != "fly":
            return
        self._fly_phase = "land_c1"
        self._fly_phase_y_offset = 0.0
        self.pet.anim_mgr.play_single(AnimGroup.HOP_C1, loop=False,
                                      on_finish=self._fly_enter_land_c2)

    def _fly_enter_land_c2(self):
        if self._state != "fly":
            return
        self._fly_phase = "land_c2"
        # 对齐到目标 x, y，消除累积误差
        self.pet.set_pos(self._fly_target_x, self._fly_target_y)
        self.pet.anim_mgr.play_single(AnimGroup.HOP_C2, loop=False,
                                      on_finish=self._fly_finish)

    def _fly_finish(self):
        self._fly_phase = "none"
        self._state = "idle"
        self._user_interacting = False
        self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)

    def _do_fly(self, dt_ms: int):
        if self._fly_phase == "none":
            return

        pos = self.pet.get_pos()

        if self._fly_phase == "hop_a1":
            # 起跳：轻微上升 + 小幅前进，总前进距离不超过 HOP_A1_FORWARD_PX
            remaining = self.HOP_A1_FORWARD_PX - self._fly_hop_a1_moved
            if remaining <= 0:
                return  # 已达上限，等动画回调切 hop_a2
            step_x = min(self.FLY_LIFT_SPEED * dt_ms / 1000.0, remaining)
            step_y = -15.0 * dt_ms / 1000.0
            move_x = self._fly_dir * step_x
            self._fly_hop_a1_moved += step_x
            new_x = max(0, min(int(pos.x() + move_x), self.pet.screen_w - self.pet.pet_size))
            new_y = max(0, int(pos.y() + step_y))
            self.pet.move(new_x, new_y)

        elif self._fly_phase == "hop_a2":
            # 加速阶段：用 _fly_dir，到达 landing_start_x 前夹住
            step = self.FLY_SPEED * dt_ms / 1000.0
            raw_x = pos.x() + self._fly_dir * step
            new_x = self._fly_clamp_before_target(int(raw_x))
            new_x = max(0, min(new_x, self.pet.screen_w - self.pet.pet_size))
            # Y 按比例移动
            new_y = self._fly_y_at_x(new_x)
            self.pet.set_pos(new_x, new_y)
            # 如果已到达 landing_start_x，提前切降落
            if self._fly_reached_or_passed(new_x):
                self._fly_enter_land_c1()

        elif self._fly_phase == "cruise":
            # 巡飞：用 _fly_dir，到达 landing_start_x 切降落
            if self._fly_reached_or_passed(pos.x()):
                self._fly_enter_land_c1()
                return
            step = self.FLY_SPEED * dt_ms / 1000.0
            raw_x = pos.x() + self._fly_dir * step
            new_x = self._fly_clamp_before_target(int(raw_x))
            new_x = max(0, min(new_x, self.pet.screen_w - self.pet.pet_size))
            # Y 按比例移动
            new_y = self._fly_y_at_x(new_x)
            self.pet.set_pos(new_x, new_y)
            if self._fly_reached_or_passed(new_x):
                self._fly_enter_land_c1()

        elif self._fly_phase == "land_c1":
            # 降落：继续用 _fly_dir 向目标推进，Y 按比例移动
            step_x = self.FLY_LIFT_SPEED * dt_ms / 1000.0
            dx_remain = abs(self._fly_target_x - pos.x())
            if dx_remain < step_x:
                move_x = self._fly_dir * dx_remain
            else:
                move_x = self._fly_dir * step_x
            new_x = max(0, min(int(pos.x() + move_x), self.pet.screen_w - self.pet.pet_size))
            new_y = self._fly_y_at_x(new_x)
            self.pet.move(new_x, new_y)

        elif self._fly_phase == "land_c2":
            pass  # 静止收尾，位置已在 _fly_enter_land_c2 对齐

    def _show_happy(self):
        """根据心情值选择开心程度"""
        self._state = "interact"
        if self.mood > 80:
            seq = "happy_jump"  # 超开心跳起来
        elif self.mood > 60:
            seq = random.choice(["happy_mid1", "happy_mid2"])
        else:
            seq = "happy_light"

        def _done():
            self._state = "idle"
            self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)

        self.pet.anim_mgr.play_sequence(seq, loop=False, on_finish=_done)

    def _look_at_owner(self):
        self._state = "interact"
        def _done():
            self._state = "idle"
            self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
        self.pet.anim_mgr.play_sequence("look", loop=False, on_finish=_done)

    def _play_idle_anim(self):
        self._state = "interact"
        def _done():
            self._state = "idle"
            self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
        self.pet.anim_mgr.play_sequence("idle_a_chain", loop=False, on_finish=_done)

    def _take_nap(self):
        self._state = "interact"
        def _done():
            self._state = "idle"
            self.energy = min(100, self.energy + 20)
            self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
        self.pet.anim_mgr.play_sequence("nap", loop=False, on_finish=_done)

    def _enter_sleep(self):
        """深度睡眠"""
        self._state = "sleep"
        self.pet.anim_mgr.play_sequence("sleep_start", loop=False,
                                        on_finish=lambda: self.pet.anim_mgr.play_single(AnimGroup.SLEEP_B2, loop=True))
        wake_time = random.randint(15000, 30000)
        QTimer.singleShot(wake_time, self._wake_up)

    def _wake_up(self):
        if self._state != "sleep":
            return
        self.energy = 100
        def _done():
            self._state = "idle"
            self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
        self.pet.anim_mgr.play_sequence("sleep_end", loop=False, on_finish=_done)

    def _start_sing(self):
        self._state = "interact"
        # 通知对话框显示取消按钮
        try:
            self.pet.chat_win.show_singing_controls(True)
        except Exception:
            pass

        def _sing_a2():
            # SING_A2 开始时音乐渐入
            self.pet.music.play_fade_in()
            self.pet.anim_mgr.play_single(AnimGroup.SING_A2, loop=False,
                                          on_finish=_start_loop)

        def _start_loop():
            self.pet.anim_mgr.play_single(AnimGroup.SING_B, loop=True)
            QTimer.singleShot(random.randint(3000, 6000), _end)

        def _end():
            # SING_C 开始时音乐渐出
            self.pet.music.fade_out()
            self.pet.anim_mgr.play_sequence("sing_end", loop=False, on_finish=_done)

        def _done():
            self._state = "idle"
            self._user_interacting = False
            self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
            try:
                self.pet.chat_win.show_singing_controls(False)
            except Exception:
                pass

        # SING_A1：享受音乐状态，无音乐
        self.pet.anim_mgr.play_single(AnimGroup.SING_A1, loop=False, on_finish=_sing_a2)

    def _play_snicker(self):
        self._state = "interact"
        def _done():
            self._state = "idle"
            self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
        self.pet.anim_mgr.play_sequence("snicker", loop=False, on_finish=_done)

    def _show_like(self):
        self._state = "interact"
        def _done():
            self._state = "idle"
            self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
        self.pet.anim_mgr.play_sequence("like", loop=False, on_finish=_done)

    # ═══════════════════════════════════════════════════════════
    # 捣乱行为
    # ═══════════════════════════════════════════════════════════

    def _do_mischief(self, eat_file: bool = False):
        """自动捣乱：只逃跑/生气，不吃文件。手动捣乱：eat_file=True 才吃文件。"""
        self._mischief_cooldown = 30
        self._manual_mischief = eat_file

        # 先决定捣乱动作（逃跑或生气）
        action = random.choice(["run_away", "angry"])
        if action == "run_away" and not self.movement_enabled:
            action = "angry"

        # 只有手动触发才找文件吃
        target = None
        if eat_file:
            edible = _find_edible_files()
            if edible:
                target = random.choice(edible)

        if action == "run_away":
            self._run_from_mouse(target)
        else:
            self._show_angry()
            if target:
                filename = target.name
                location = str(target.parent)
                QTimer.singleShot(2000, lambda: self._eat_file(filename, location))

    def _run_from_mouse(self, target=None):
        from PyQt5.QtGui import QCursor
        cursor = QCursor.pos()
        pos = self.pet.get_pos()
        dx = pos.x() - cursor.x()
        run_dir = 1 if dx >= 0 else -1
        run_dist = random.randint(150, 300)
        target_x = max(0, min(pos.x() + run_dir * run_dist,
                              self.pet.screen_w - self.pet.pet_size))

        # 更新朝向：逃跑方向
        self.pet.face_direction(run_dir)

        self._state = "mischief"
        self.pet.anim_mgr.play_single(AnimGroup.WALK_A, loop=True)

        steps = [0]
        total = abs(target_x - pos.x())

        def _step():
            if steps[0] >= total:
                self._state = "idle"
                self._user_interacting = False
                self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
                # 逃跑结束后吃文件（仅手动触发）
                if target and self._manual_mischief:
                    filename = target.name
                    location = str(target.parent)
                    self._manual_mischief = False
                    self._eat_file(filename, location)
                return
            cur = self.pet.get_pos()
            self.pet.set_pos(cur.x() + run_dir * 8, cur.y())
            steps[0] += 8
            QTimer.singleShot(40, _step)
        _step()

    def _show_angry(self):
        self._state = "interact"
        seq = "angry_big" if self.mood < 20 else "angry_small"
        def _done():
            self._state = "idle"
            self._user_interacting = False
            self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
        self.pet.anim_mgr.play_sequence(seq, loop=False, on_finish=_done)

    # ═══════════════════════════════════════════════════════════
    # 用户触发事件
    # ═══════════════════════════════════════════════════════════

    def _eat_file(self, filename: str, location: str):
        """删除文件到回收站 + 弹对话框说明（不播放吃东西动画）"""
        # 防护：仅手动触发时允许执行
        if not self._manual_mischief:
            print("[Mischief] Blocked auto _eat_file call")
            return
        self._manual_mischief = False

        from behavior_new import send_to_recycle_bin
        filepath = Path(location) / filename
        if not send_to_recycle_bin(str(filepath)):
            print(f"[Mischief] Failed to delete: {filepath}")
            return

        print(f"[Mischief] Ate file: {filename} from {location}")

        self.hunger = max(0, self.hunger - 20)
        self.mood = min(100, self.mood + 10)
        self.add_affection(-6)  # 吃了文件，损害信任

        # 短路径显示
        short_loc = location
        home = str(Path.home())
        if location.startswith(home):
            short_loc = "~" + location[len(home):]

        # 弹出对话框说明
        msg = f"啾！我吃掉了「{filename}」，从 {short_loc}。主人可以去回收站捞回来……"
        try:
            self.pet.show_chat()
            self.pet.chat_win._append_msg("肥鸡", msg)
            # 唱歌中不打断，只显示文字
            if self.pet.chat_win._voice_enabled and not self.pet.music.is_playing():
                self.pet.tts.speak(msg)
        except Exception:
            pass

    def on_grabbed(self):
        self._state = "grabbed"
        self.add_affection(-2)  # 被抓起来不高兴
        # 先播放被抓起瞬间，结束后切换到扑腾循环
        def _start_flap():
            # 如果已经松手，不再启动 Grab-C 循环
            if self._state != "grabbed":
                return
            if AnimGroup.GRAB_C in self.pet.anim_mgr._cache:
                self.pet.anim_mgr.play_single(AnimGroup.GRAB_C, loop=True)
            else:
                self.pet.anim_mgr.play_single(AnimGroup.GRAB_A, loop=True)
        self.pet.anim_mgr.play_sequence("grabbed", loop=False, on_finish=_start_flap)

    def on_released(self):
        self._state = "interact"
        def _done():
            self._state = "idle"
            self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
        self.pet.anim_mgr.play_sequence("released", loop=False, on_finish=_done)
        # 被抓会降低心情
        self.mood = max(0, self.mood - 10)

    def on_feed(self):
        if self._user_interacting:
            return
        self._user_interacting = True
        self.hunger    = max(0, self.hunger - 40)
        self.mood      = min(100, self.mood + 15)
        self.add_affection(3)  # 喂食 +3

        self._state = "interact"
        def _done():
            self._state = "idle"
            self._user_interacting = False
            if self.mood > 70:
                self._show_happy()
            else:
                self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
        self.pet.anim_mgr.play_sequence("eat", loop=False, on_finish=_done)

    def on_feed_file(self):
        """投喂文件：吃完后播放喜欢（爱心眼）动画"""
        if self._user_interacting or self._state in ("fly", "sleep"):
            return
        self._user_interacting = True
        self.hunger    = max(0, self.hunger - 40)
        self.mood      = min(100, self.mood + 15)
        self.add_affection(6)

        self._state = "interact"
        def _done():
            self._state = "idle"
            self._user_interacting = False
            self._show_like()
        self.pet.anim_mgr.play_sequence("eat", loop=False, on_finish=_done)

    def on_pet(self):
        if self._user_interacting:
            return
        self._user_interacting = True
        self.mood = min(100, self.mood + 20)
        self.add_affection(2)  # 摸摸 +2

        self._state = "interact"
        def _done():
            self._state = "idle"
            self._user_interacting = False
            if self.affection > 80 and random.random() < 0.5:
                self._show_like()
            else:
                self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
        self.pet.anim_mgr.play_sequence("pet", loop=False, on_finish=_done)

    def on_sing(self):
        if self._user_interacting:
            return
        self._user_interacting = True
        self.mood = min(100, self.mood + 10)
        self.add_affection(3)  # 唱歌 +3
        self._start_sing()

    def on_startle(self):
        """受惊（外部突然事件）"""
        if self._user_interacting:
            return
        self._user_interacting = True
        self._state = "interact"
        seq = "startle_big" if random.random() < 0.3 else "startle_small"
        def _done():
            self._state = "idle"
            self._user_interacting = False
            self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True)
        self.pet.anim_mgr.play_sequence(seq, loop=False, on_finish=_done)
        self.mood = max(0, self.mood - 5)
        self.add_affection(-1)  # 受惊，轻微降低

    def on_fly(self):
        """手动触发飞行"""
        if self._user_interacting:
            return
        if not self.movement_enabled:
            return
        self._user_interacting = True
        self.add_affection(-2)  # 手动飞行消耗
        self._start_fly()

    def on_nap(self):
        """手动触发小睡"""
        self._take_nap()

    def on_mischief(self):
        """手动触发捣乱（会吃文件）"""
        if self._user_interacting:
            return
        self._user_interacting = True
        self._do_mischief(eat_file=True)

    def stop_movement(self):
        """立即中止当前走路/飞行，回到 idle。"""
        if self._state in ("walk", "fly", "mischief"):
            self._fly_phase = "none"
            self._walk_dist_rem = 0.0
            self._state = "idle"
            self._user_interacting = False
            self.pet.anim_mgr.play_sequence("look", loop=False,
                on_finish=lambda: self.pet.anim_mgr.play_single(AnimGroup.START_B, loop=True))

    def add_affection(self, delta: float):
        """增加亲密度，自动 clamp 并保存。"""
        self.affection = max(0.0, min(100.0, self.affection + delta))
        _save_state(self.affection, self.mood, self.energy, self.hunger)
        try:
            self.pet.chat_win.update_affection_label(int(self.affection))
        except Exception:
            pass
        try:
            self.pet.hover_btns.update_affection(int(self.affection))
        except Exception:
            pass

    def _show_low_affection_reminder(self):
        """亲密度过低时自动弹出对话框提醒。"""
        try:
            self.pet.show_chat()
            self.pet.chat_win._append_msg("肥鸡", "啾……主人，你是不是好久没理我了……")
            if self.pet.chat_win._voice_enabled:
                self.pet.tts.speak("啾……主人，你是不是好久没理我了……")
        except Exception:
            pass
