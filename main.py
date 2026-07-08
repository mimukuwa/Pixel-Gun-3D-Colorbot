import math
import time
import ctypes
import threading
import numpy as np
import mss
import keyboard
import tkinter as tk
import win32gui

import win32gui

def is_game_active():
    hwnd = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(hwnd)
    return "Pixel Gun 3D" in title

#~~~~~~~~~~~~~~~~~CONFIG~~~~~~~~~~~~~~~~#
AIMBOT_KEY         = 'x'     
TOGGLE_MODE        = True       

SENSITIVITY        = 20           
HEX_CODES_RAW      = 'e01808'    #don't touch the hex code 
DOT_ENABLED        = False
DOT_OPACITY        = 255        

SMOOTHNESS         = 3.5       

FOV_W              = 80
FOV_H              = 80
FOV_VISIBLE        = True
FOV_OPACITY        = 150

Y_OFFSET           = 24          
X_OFFSET           = 0           


user32 = ctypes.windll.user32
SCREEN_W = user32.GetSystemMetrics(0)
SCREEN_H = user32.GetSystemMetrics(1)
CENTER_X = SCREEN_W // 2
CENTER_Y = SCREEN_H // 2

def parse_colors(raw):
    """HEX (#RRGGBB ou RRGGBB, séparé ',') → BGR tuples OpenCV"""
    colors = []
    for h in raw.replace("#","").split(","):
        val = int(h.strip(), 16)
        r = (val >> 16) & 0xFF
        g = (val >> 8)  & 0xFF
        b =  val        & 0xFF
        colors.append((b, g, r)) 
    return colors

TARGET_COLORS = parse_colors(HEX_CODES_RAW)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def in_fov(x, y):
    dx = abs(x - CENTER_X)
    dy = abs(y - CENTER_Y)
    return dx <= FOV_W / 2 and dy <= FOV_H / 2

def find_target(frame_bgr, region_x, region_y):
    """Retourne l'écran (x,y) du cluster rouge le plus proche du centre"""
    best_dist = float("inf")
    best_x = None
    best_y = None
    for target_bgr in TARGET_COLORS:
        diff = np.abs(frame_bgr.astype(np.int16) - np.array(target_bgr))
        mask = np.sum(diff, axis=2) <= SENSITIVITY * 3
        ys, xs = np.where(mask)
        if len(xs) < 20:
            continue
        cx = int(np.mean(xs))
        cy = int(np.mean(ys) * 0.6 + np.min(ys) * 0.4) 
        px = region_x + cx
        py = region_y + cy
        
        if abs(px - CENTER_X) > FOV_W:
            continue
        d = math.sqrt((px - CENTER_X) ** 2 + (py - CENTER_Y) ** 2)
        if d < best_dist:
            best_dist = d
            best_x = px
            best_y = py
    if best_x is not None:
        return best_x + X_OFFSET, best_y
    return None, None

class FOVOverlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", "black")
        self.root.attributes("-alpha", FOV_OPACITY/255)
        self.root.configure(bg="black")
        self.canvas = tk.Canvas(self.root, width=FOV_W, height=FOV_H, bg="Black", highlightthickness=0)
        self.canvas.pack()
        self.canvas.create_oval(0, 0, FOV_W, FOV_H, outline="White", width=2)
        x = CENTER_X - FOV_W // 2
        y = CENTER_Y - FOV_H // 2
        self.root.geometry(f"{FOV_W}x{FOV_H}+{x}+{y}")
        self.visible = True
        self.root.bind("<F2>", self.toggle)
    def toggle(self, _=None):
        self.visible = not self.visible
        if self.visible:
            self.root.deiconify()
        else:
            self.root.withdraw()
    def run(self):
        self.root.mainloop()

class DotOverlay:

    
    def __init__(self):
        self.win = tk.Toplevel()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", "black")
        self.win.attributes("-alpha", DOT_OPACITY / 255)
        self.win.configure(bg="black")
        self.canvas = tk.Canvas(self.win, width=12, height=12, bg="black", highlightthickness=0)
        self.canvas.pack()
        self.canvas.create_oval(1, 1, 11, 11, fill="White", outline="")
        self.win.withdraw()
    def update(self, x, y, visible):
        if visible and x is not None and y is not None:
            self.win.geometry(f"12x12+{int(x-6)}+{int(y-6)}")
            self.win.deiconify()
        else:
            self.win.withdraw()

class RightClickListener:
    _VK_RBUTTON = 0x02
    def __init__(self, callback_on_press, callback_on_release):
        self.callback_on_press   = callback_on_press
        self.callback_on_release = callback_on_release
        self.running = True
        self.pressed = False
        self.thread  = threading.Thread(target=self._listen_loop, daemon=True)
        self.thread.start()
    def _listen_loop(self):
        get_state = ctypes.windll.user32.GetAsyncKeyState
        while self.running:
            is_pressed = bool(get_state(self._VK_RBUTTON) & 0x8000)
            if is_pressed and not self.pressed:
                self.pressed = True
                if self.callback_on_press:
                    self.callback_on_press()
            elif not is_pressed and self.pressed:
                self.pressed = False
                if self.callback_on_release:
                    self.callback_on_release()
            time.sleep(0.001)
    def stop(self):
        self.running = False
        self.thread.join(timeout=1.0)

class AimBot:
    def __init__(self):
        self.active = False
        self.lock = threading.Lock()
        self.look_x = None
        self.look_y = None
        self.last_target_x = None
        self.last_target_y = None
        self._running = True
        self._suspended = False
        self._fov_overlay = None
        self.frame_count = 0
        self._setup_input()
        if not TOGGLE_MODE:
            self.right_click_listener = RightClickListener(self._activate, self._deactivate)
        else:
            self.right_click_listener = None

    def _setup_input(self):
        key = AIMBOT_KEY.lower().replace("rbutton", "right")
        if TOGGLE_MODE:
            keyboard.on_press_key(key, self._toggle)
        keyboard.add_hotkey("f2", self._fov_toggle)
        keyboard.add_hotkey("right ctrl", self._suspend)
    def _toggle(self, _=None):
        with self.lock:
            self.active = not self.active
    def _activate(self):
        with self.lock:
            self.active = True
    def _deactivate(self):
        with self.lock:
            self.active = False
            self.look_x = None
            self.look_y = None
    def _fov_toggle(self):
        if self._fov_overlay:
            self._fov_overlay.root.after(0, self._fov_overlay.toggle)
    def _suspend(self):
        self._suspended = not self._suspended

    def run(self):
        with mss.mss() as sct:
            while self._running:
                if not is_game_active():
                    time.sleep(0.01)
                    continue
                self.frame_count += 1
                if self._suspended:
                    time.sleep(0.01)
                    continue
                with self.lock:
                    is_active = self.active
                if not is_active:
                    self.look_x = None
                    self.look_y = None
                    time.sleep(0.01)
                    continue

                left   = CENTER_X - (FOV_W / 2)
                top    = CENTER_Y - (FOV_H / 2)
                rx = int(left)
                ry = int(top)
                region = {"left": rx, "top": ry, "width": int(FOV_W), "height": int(FOV_H)}
                try:
                    frame = np.array(sct.grab(region))[:, :, :3]
                except Exception as e:
                    print(f"[ERROR] Capture écran: {e}")
                    time.sleep(0.01)
                    continue
                pos_x, pos_y = find_target(frame, rx, ry)
                if pos_x is None or pos_y is None:
                    with self.lock:
                        self.look_x = None
                        self.look_y = None
                    time.sleep(0.01)
                    continue

                target_x = pos_x
                target_y = pos_y + Y_OFFSET

               
                self.last_target_x = target_x
                self.last_target_y = target_y

                
                target_y = max(0, target_y)

              
                dx = target_x - CENTER_X
                dy = target_y - CENTER_Y

                distance = math.sqrt(dx*dx + dy*dy)

                smooth = max(float(SMOOTHNESS), 1.0)

               
                if distance < 80:
                    assist_strength = 0.6  
                elif distance < 200:
                    assist_strength = 0.35  
                else:
                    assist_strength = 0.15  

                move_x = int((dx / smooth) * assist_strength)
                move_y = int((dy / smooth) * assist_strength)
             
                if abs(move_x) < 1:
                    move_x = 0
                if abs(move_y) < 1:
                    move_y = 0
                if move_x != 0 or move_y != 0:
                    ctypes.windll.user32.mouse_event(0x0001, move_x, move_y, 0, 0)
                with self.lock:
                    self.look_x = target_x
                    self.look_y = target_y
              
                if self.frame_count % 50 == 0:
                    print(
                        f"[{self.frame_count}] Target: ({pos_x:.0f}, {pos_y:.0f}) | Move: ({move_x},{move_y})")
                time.sleep(0.01)

    def stop(self):
        self._running = False
        if hasattr(self, "right_click_listener") and self.right_click_listener:
            self.right_click_listener.stop()

def main():
    print("\n" + "="*70)
    print("[AimAssist Python] - Aimbot loaded successfully  - Config Loaded !")
    print("="*70)
    bot = AimBot()
    aim_thread = threading.Thread(target=bot.run, daemon=True)
    aim_thread.start()
    if FOV_VISIBLE:
        overlay = FOVOverlay()
        bot._fov_overlay = overlay
        if DOT_ENABLED:
            dot = DotOverlay()
            def dot_update_loop():
                while True:
                    with bot.lock:
                        lx, ly = bot.look_x, bot.look_y
                        active = bot.active
                    overlay.root.after(0, dot.update, lx, ly, active and lx is not None)
                    time.sleep(0.001)
            dot_thread = threading.Thread(target=dot_update_loop, daemon=True)
            dot_thread.start()
        try:
            overlay.run()
        except KeyboardInterrupt:
            pass
    else:
        try:
            keyboard.wait("esc")
        except KeyboardInterrupt:
            pass
    bot.stop()
    print("\n[AimAssist] fuck jews.\n")

if __name__ == "__main__":
    main()
