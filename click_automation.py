import tkinter as tk
import threading
import time
import pyautogui
import json
from pynput import keyboard, mouse
from PIL import ImageGrab # 用於擷取螢幕顏色

# 狀態管理
# click_positions 儲存:
#   (x, y) - 一般點擊 (無顏色檢查)
#   (x, y, r, g, b) - 帶顏色檢查的點擊 (會執行點擊動作)
#   ('drag', x1, y1, x2, y2, duration) - 拖曳操作
#   ('if_color', x, y, r, g, b) - IF 顏色判斷區塊的開始 (不執行點擊動作，只做判斷)
#   ('end_if') - IF 判斷區塊的結束
click_positions = []
is_running = False
is_recording_with_color = False
is_recording_drag = False
is_recording_if_color = False # 新增用於記錄if_color的狀態
is_monitoring_mouse = False # 新增滑鼠監控狀態

# 用於暫存拖曳的兩個點
drag_points_buffer = []

# GUI 初始化
root = tk.Tk()
root.title("Mac 滑鼠點擊器")
root.geometry("450x980") # 調整視窗大小以容納新按鈕和輸入框

# --- 點位列表顯示與滾動條 ---
list_frame = tk.Frame(root)
list_frame.pack(pady=10)

click_list = tk.Listbox(list_frame, width=50, height=15) # 增加高度
click_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=click_list.yview)
scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
click_list.config(yscrollcommand=scrollbar.set)
# --- 點位列表顯示與滾動條結束 ---

# 狀態文字
# status_label 將顯示執行中的詳細資訊，包括步驟編號
status_label = tk.Label(root, text="wait operation...", wraplength=400, justify=tk.LEFT)
status_label.pack(pady=5)

# 滑鼠位置與顏色顯示 Label
mouse_info_label = tk.Label(root, text="Mouse: (X: ?, Y: ?) RGB: (?, ?, ?)", font=("Arial", 10))
mouse_info_label.pack(pady=5)

# 點擊間隔（秒）
interval_label = tk.Label(root, text="Click Interval (s)")
interval_label.pack()
interval_entry = tk.Entry(root)
interval_entry.insert(0, "1.0")
interval_entry.pack()

# 拖曳速度（秒）
drag_speed_label = tk.Label(root, text="Drag Duration (s)")
drag_speed_label.pack()
drag_speed_entry = tk.Entry(root)
drag_speed_entry.insert(0, "0.5") # 預設拖曳時間
drag_speed_entry.pack()

# 重複次數 (-1 表示無限)
repeat_label = tk.Label(root, text="Repeat Count (-1 for infinite)")
repeat_label.pack()
repeat_entry = tk.Entry(root)
repeat_entry.insert(0, "1")
repeat_entry.pack()

# 抓取顏色前延遲與游標躲避點位設定
color_grab_delay_label = tk.Label(root, text="Color Grab Delay (s) before capture")
color_grab_delay_label.pack()
color_grab_delay_entry = tk.Entry(root)
color_grab_delay_entry.insert(0, "0.1") # 預設延遲 0.1 秒
color_grab_delay_entry.pack()

cursor_hide_pos_label = tk.Label(root, text="Cursor Hide Position (X,Y) during color grab")
cursor_hide_pos_label.pack()
cursor_hide_pos_entry = tk.Entry(root)
cursor_hide_pos_entry.insert(0, "1,1") # 預設移動到 (1,1) 以避免PyAutoGUI fail-safe
cursor_hide_pos_entry.pack()


# 更新點位列表顯示
def update_click_list():
    click_list.delete(0, tk.END)
    indent_level = 0
    for i, pos_data in enumerate(click_positions):
        prefix = "  " * indent_level # 用於視覺化縮進
        if len(pos_data) == 2:
            click_list.insert(tk.END, f"{i+1}: {prefix}Click ({pos_data[0]}, {pos_data[1]}) (No Color Check)")
        elif len(pos_data) == 5 and pos_data[0] != 'if_color': # 帶顏色檢查的點擊
            click_list.insert(tk.END, f"{i+1}: {prefix}Click ({pos_data[0]}, {pos_data[1]}) Color: ({pos_data[2]}, {pos_data[3]}, {pos_data[4]})")
        elif pos_data[0] == 'drag':
            click_list.insert(tk.END, f"{i+1}: {prefix}Drag ({pos_data[1]}, {pos_data[2]}) to ({pos_data[3]}, {pos_data[4]}) in {pos_data[5]}s")
        elif pos_data[0] == 'if_color':
            click_list.insert(tk.END, f"{i+1}: {prefix}IF Color ({pos_data[1]}, {pos_data[2]}) == ({pos_data[3]}, {pos_data[4]}, {pos_data[5]})")
            indent_level += 1
        elif pos_data[0] == 'end_if':
            indent_level = max(0, indent_level - 1) # 確保不會出現負數縮進
            click_list.insert(tk.END, f"{i+1}: {prefix}END IF")

# 獲取指定座標的像素顏色
def get_pixel_color(x, y):
    try:
        # 將抓取顏色的點位向左偏移 10 像素
        # 注意：這個偏移只影響實際抓取像素時的座標，不影響記錄或點擊的邏輯座標
        adjusted_x = x - 10
        # 確保偏移後座標不會小於0或超出螢幕寬度 (簡單檢查)
        if adjusted_x < 0:
            adjusted_x = 0
        # 這裡可以加上螢幕寬度限制，但通常不會發生
        # screen_width, _ = pyautogui.size()
        # if adjusted_x >= screen_width:
        #     adjusted_x = screen_width - 1

        screenshot = ImageGrab.grab()
        return screenshot.getpixel((adjusted_x, y))
    except Exception as e:
        # 在多螢幕或某些特殊情況下，getpixel可能出錯，返回黑色或白色避免崩潰
        print(f"Error getting pixel color at ({x}, {y}) (adjusted to {adjusted_x}, {y}): {e}")
        return 0, 0, 0, 255 # 返回黑色，帶alpha

# 持續更新滑鼠位置和顏色資訊的函式
def update_mouse_info():
    global is_monitoring_mouse
    if not is_monitoring_mouse:
        is_monitoring_mouse = True
        
    while is_monitoring_mouse:
        try:
            x, y = pyautogui.position() # 獲取滑鼠當前位置 (這是實際的滑鼠位置)
            # 這裡的 get_pixel_color 會使用內部調整後的 x 座標來抓取顏色
            r, g, b, _ = get_pixel_color(x, y) # 獲取該位置的顏色

            # 使用 root.after 確保在主執行緒中更新 GUI
            root.after(0, lambda: mouse_info_label.config(text=f"Mouse: (X: {x}, Y: {y}) RGB: ({r}, {g}, {b})"))
        except Exception as e:
            root.after(0, lambda: mouse_info_label.config(text=f"Mouse Info Error: {e}"))
            print(f"Mouse info update error: {e}")
        time.sleep(0.05) # 每 50 毫秒更新一次
        
    root.after(0, lambda: mouse_info_label.config(text="Mouse: (X: ?, Y: ?) RGB: (?, ?, ?)")) # 停止時清空
    
# 啟動滑鼠資訊監控執行緒
mouse_monitor_thread = threading.Thread(target=update_mouse_info, daemon=True)
mouse_monitor_thread.start()


# 非阻塞式滑鼠記錄 (只記錄位置)
def record_next_click_only_position():
    global is_recording_with_color, is_recording_drag, is_recording_if_color
    if is_recording_with_color or is_recording_drag or is_recording_if_color:
        status_label.config(text="請先完成其他記錄操作")
        return
    status_label.config(text="請點擊任意位置以記錄座標...")
    listener = mouse.Listener(on_click=on_click_record_position_only)
    listener.start()

def on_click_record_position_only(x, y, button, pressed):
    if pressed:
        click_positions.append((x, y))
        update_click_list()
        status_label.config(text=f"已記錄座標: ({x}, {y})")
        return False # 停止監聽

# 非阻塞式滑鼠記錄 (記錄位置與顏色)
def record_next_click_with_color():
    global is_recording_with_color, is_recording_drag, is_recording_if_color
    if is_recording_with_color or is_recording_drag or is_recording_if_color:
        status_label.config(text="請先完成其他記錄操作")
        return
    is_recording_with_color = True
    status_label.config(text="請點擊任意位置以記錄座標與顏色...")
    listener = mouse.Listener(on_click=on_click_record_with_color)
    listener.start()

def on_click_record_with_color(x, y, button, pressed):
    global is_recording_with_color
    if pressed and is_recording_with_color:
        original_mouse_pos = pyautogui.position() # 記錄當前滑鼠位置

        # 獲取設定的延遲和游標躲避點位
        try:
            delay = float(color_grab_delay_entry.get())
            hide_x, hide_y = map(int, cursor_hide_pos_entry.get().split(','))
        except ValueError:
            status_label.config(text="延遲時間或游標躲避點位格式錯誤，已取消記錄")
            is_recording_with_color = False
            return False

        time.sleep(delay) # 等待指定延遲時間
        pyautogui.moveTo(hide_x, hide_y) # 移動滑鼠游標到躲避點位
        time.sleep(0.05) # 確保游標移動完成

        r, g, b, _ = get_pixel_color(x, y) # 在游標躲避後擷取顏色

        pyautogui.moveTo(original_mouse_pos.x, original_mouse_pos.y) # 將滑鼠游標移回原位

        click_positions.append((x, y, r, g, b))
        update_click_list()
        status_label.config(text=f"已記錄: ({x}, {y}), 顏色: ({r}, {g}, {b})")
        is_recording_with_color = False
        return False # 停止監聽

# 記錄拖曳點位
def record_drag_points():
    global is_recording_drag, drag_points_buffer, is_recording_with_color, is_recording_if_color
    if is_recording_drag or is_recording_with_color or is_recording_if_color:
        status_label.config(text="請先完成其他記錄操作")
        return
    is_recording_drag = True
    drag_points_buffer.clear()
    status_label.config(text="請點擊拖曳起點...")
    listener = mouse.Listener(on_click=on_click_record_drag_start)
    listener.start()

def on_click_record_drag_start(x, y, button, pressed):
    global drag_points_buffer
    if pressed:
        drag_points_buffer.append((x, y))
        status_label.config(text="已記錄起點，請點擊拖曳終點...")
        # 啟動新的監聽器來捕捉第二個點
        new_listener = mouse.Listener(on_click=on_click_record_drag_end)
        new_listener.start()
        return False # 停止當前監聽器

def on_click_record_drag_end(x, y, button, pressed):
    global is_recording_drag, drag_points_buffer
    if pressed:
        drag_points_buffer.append((x, y))
        try:
            drag_duration = float(drag_speed_entry.get())
            if drag_duration <= 0:
                raise ValueError("拖曳時間必須大於0")
        except ValueError:
            status_label.config(text="拖曳時間請輸入大於0的數字，已取消記錄拖曳點位")
            is_recording_drag = False
            drag_points_buffer.clear()
            return False

        if len(drag_points_buffer) == 2:
            start_x, start_y = drag_points_buffer[0]
            end_x, end_y = drag_points_buffer[1]
            click_positions.append(('drag', start_x, start_y, end_x, end_y, drag_duration))
            update_click_list()
            status_label.config(text=f"已記錄拖曳: ({start_x}, {start_y}) 到 ({end_x}, {end_y})")
        else:
            status_label.config(text="記錄拖曳點位發生錯誤，請重試")
        is_recording_drag = False
        drag_points_buffer.clear()
        return False # 停止監聽

# 記錄 If Block 的顏色判斷點
def record_if_color_point():
    global is_recording_if_color, is_recording_with_color, is_recording_drag
    if is_recording_if_color or is_recording_with_color or is_recording_drag:
        status_label.config(text="請先完成其他記錄操作")
        return
    is_recording_if_color = True
    status_label.config(text="請點擊任意位置以記錄 IF 判斷點的座標與顏色...")
    listener = mouse.Listener(on_click=on_click_record_if_color)
    listener.start()

def on_click_record_if_color(x, y, button, pressed):
    global is_recording_if_color
    if pressed and is_recording_if_color:
        original_mouse_pos = pyautogui.position() # 記錄當前滑鼠位置

        # 獲取設定的延遲和游標躲避點位
        try:
            delay = float(color_grab_delay_entry.get())
            hide_x, hide_y = map(int, cursor_hide_pos_entry.get().split(','))
        except ValueError:
            status_label.config(text="延遲時間或游標躲避點位格式錯誤，已取消記錄")
            is_recording_if_color = False
            return False

        time.sleep(delay) # 等待指定延遲時間
        pyautogui.moveTo(hide_x, hide_y) # 移動滑鼠游標到躲避點位
        time.sleep(0.05) # 確保游標移動完成

        r, g, b, _ = get_pixel_color(x, y) # 在游標躲避後擷取顏色

        pyautogui.moveTo(original_mouse_pos.x, original_mouse_pos.y) # 將滑鼠游標移回原位

        click_positions.append(('if_color', x, y, r, g, b))
        update_click_list()
        status_label.config(text=f"已記錄 IF 判斷點: ({x}, {y}), 顏色: ({r}, {g}, {b})")
        is_recording_if_color = False
        return False # 停止監聽

# 結束 If Block
def end_if_block():
    click_positions.append(('end_if',)) # 使用元組，即使只有一個元素，方便JSON序列化
    update_click_list()
    status_label.config(text="已新增 END IF 區塊")


# 執行點擊邏輯
def click_loop(interval, repeat):
    global is_running
    status_label.config(text="執行中...")
    counter = 0
    while is_running and (repeat == -1 or counter < repeat):
        # 跟踪跳過等級。0表示不跳過，>0表示正在跳過某個IF區塊
        skip_block_level = 0
        
        i = 0
        while i < len(click_positions):
            if not is_running:
                break

            pos_data = click_positions[i]
            
            # --- 更新執行資訊 Label ---
            current_action_desc = ""
            if len(pos_data) == 2:
                current_action_desc = f"Click ({pos_data[0]}, {pos_data[1]})"
            elif len(pos_data) == 5 and pos_data[0] != 'if_color':
                current_action_desc = f"Click ({pos_data[0]}, {pos_data[1]}) Color: ({pos_data[2]}, {pos_data[3]}, {pos_data[4]})"
            elif pos_data[0] == 'drag':
                current_action_desc = f"Drag ({pos_data[1]}, {pos_data[2]}) to ({pos_data[3]}, {pos_data[4]})"
            elif pos_data[0] == 'if_color':
                current_action_desc = f"IF Color ({pos_data[1]}, {pos_data[2]}) == ({pos_data[3]}, {pos_data[4]}, {pos_data[5]})"
            elif pos_data[0] == 'end_if':
                current_action_desc = "END IF"
            
            root.after(0, lambda idx=i+1, desc=current_action_desc: status_label.config(text=f"執行中 (步驟 {idx}/{len(click_positions)}): {desc}"))
            # --- 執行資訊 Label 更新結束 ---


            # --- IF 顏色判斷邏輯 ---
            if pos_data[0] == 'if_color':
                x, y, expected_r, expected_g, expected_b = pos_data[1:]

                original_mouse_pos = pyautogui.position() # 記錄當前滑鼠位置
                try:
                    delay = float(color_grab_delay_entry.get())
                    hide_x, hide_y = map(int, cursor_hide_pos_entry.get().split(','))
                except ValueError:
                    delay = 0
                    hide_x, hide_y = original_mouse_pos.x, original_mouse_pos.y

                time.sleep(delay) # 等待指定延遲時間
                pyautogui.moveTo(hide_x, hide_y) # 移動滑鼠游標到躲避點位
                time.sleep(0.05) # 確保游標移動完成

                current_r, current_g, current_b, _ = get_pixel_color(x, y) # 在游標躲避後擷取顏色

                pyautogui.moveTo(original_mouse_pos.x, original_mouse_pos.y) # 將滑鼠游標移回原位

                # --- Debug Print ---
                print(f"--- IF Check at ({x}, {y}) ---")
                print(f"Expected RGB: ({expected_r}, {expected_g}, {expected_b})")
                print(f"Current  RGB: ({current_r}, {current_g}, {current_b})")
                # --- Debug Print End ---

                if (expected_r, expected_g, expected_b) == (current_r, current_g, current_b):
                    root.after(0, lambda: status_label.config(text=f"執行中 (步驟 {i+1}/{len(click_positions)}): IF 條件 ({x}, {y}) 顏色符合, 進入區塊"))
                else:
                    root.after(0, lambda: status_label.config(text=f"執行中 (步驟 {i+1}/{len(click_positions)}): IF 條件 ({x}, {y}) 顏色不符, 跳過區塊"))
                    skip_block_level += 1 # 進入跳過模式，增加跳過層級
            # --- END IF 邏輯 ---
            elif pos_data[0] == 'end_if':
                if skip_block_level > 0:
                    skip_block_level -= 1 # 退出一個跳過層級
            # --- 跳過判斷 ---
            elif skip_block_level > 0:
                pass # 如果處於跳過模式，則跳過當前操作
            # --- 正常操作執行邏輯 ---
            elif pos_data[0] == 'drag': # 處理拖曳操作
                _, start_x, start_y, end_x, end_y, duration = pos_data
                pyautogui.moveTo(start_x, start_y) # 先移動到起始點
                pyautogui.dragTo(end_x, end_y, duration=duration, button='left')
                root.after(0, lambda: status_label.config(text=f"執行中 (步驟 {i+1}/{len(click_positions)}): 執行拖曳: ({start_x}, {start_y}) 到 ({end_x}, {end_y})"))
            elif len(pos_data) == 5: # 處理帶有顏色檢查的點擊
                x, y, recorded_r, recorded_g, recorded_b = pos_data
                
                original_mouse_pos = pyautogui.position() # 記錄當前滑鼠位置
                try:
                    delay = float(color_grab_delay_entry.get())
                    hide_x, hide_y = map(int, cursor_hide_pos_entry.get().split(','))
                except ValueError:
                    delay = 0
                    hide_x, hide_y = original_mouse_pos.x, original_mouse_pos.y

                time.sleep(delay) # 等待指定延遲時間
                pyautogui.moveTo(hide_x, hide_y) # 移動滑鼠游標到躲避點位
                time.sleep(0.05) # 確保游標移動完成

                current_r, current_g, current_b, _ = get_pixel_color(x, y) # 在游標躲避後擷取顏色

                pyautogui.moveTo(original_mouse_pos.x, original_mouse_pos.y) # 將滑鼠游標移回原位

                if (recorded_r, recorded_g, recorded_b) != (current_r, current_g, current_b):
                    root.after(0, lambda: status_label.config(text=f"執行中 (步驟 {i+1}/{len(click_positions)}): 位置 ({x}, {y}) 顏色不符, 跳過點擊"))
                    time.sleep(interval) # 即使跳過也要等待間隔，避免過快
                    i += 1 # 手動推進索引
                    continue # 跳過本次點擊
                pyautogui.click(x=x, y=y)
                root.after(0, lambda: status_label.config(text=f"執行中 (步驟 {i+1}/{len(click_positions)}): 執行點擊: ({x}, {y})"))
            elif len(pos_data) == 2: # 處理只有座標的點擊
                x, y = pos_data
                pyautogui.click(x=x, y=y)
                root.after(0, lambda: status_label.config(text=f"執行中 (步驟 {i+1}/{len(click_positions)}): 執行點擊: ({x}, {y})"))

            time.sleep(interval)
            i += 1 # 前進到下一個指令

        counter += 1
    is_running = False
    root.after(0, lambda: status_label.config(text="finished" if counter > 0 else "stopped"))


def start_clicking():
    global is_running
    if is_running or not click_positions:
        return
    try:
        interval = float(interval_entry.get())
        repeat = int(repeat_entry.get())
    except ValueError:
        status_label.config(text="請輸入正確數字")
        return
    is_running = True
    threading.Thread(target=click_loop, args=(interval, repeat), daemon=True).start()

def stop_clicking():
    global is_running
    is_running = False
    status_label.config(text="stopped")

# 刪除選中的點位
def delete_selected():
    selected = click_list.curselection()
    if not selected:
        return
    index = selected[0]
    click_positions.pop(index)
    update_click_list()
    status_label.config(text=f"已刪除點位 {index + 1}")

# 儲存與讀取
def save_positions():
    with open("click_positions.json", "w") as f:
        json.dump(click_positions, f)
    status_label.config(text="已儲存至 click_positions.json")

def load_positions():
    global click_positions
    try:
        with open("click_positions.json", "r") as f:
            click_positions = json.load(f)
        update_click_list()
        status_label.config(text="已載入 click_positions.json")
    except FileNotFoundError:
        status_label.config(text="找不到 click_positions.json")
    except Exception as e:
        status_label.config(text=f"載入失敗: {e}")

# 鍵盤快捷鍵處理
pressed_keys = set()
def on_press(key):
    try:
        pressed_keys.add(key)
        if {keyboard.Key.ctrl_l, keyboard.Key.alt_l, keyboard.KeyCode.from_char('s')} <= pressed_keys:
            start_clicking()
        elif {keyboard.Key.ctrl_l, keyboard.Key.alt_l, keyboard.KeyCode.from_char('q')} <= pressed_keys:
            stop_clicking()
    except:
        pass

def on_release(key):
    pressed_keys.discard(key)

keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
keyboard_listener.start()

# 建立按鈕
record_btn_pos_only = tk.Button(root, text="Record Next Position (Only Coords)", command=record_next_click_only_position)
record_btn_pos_only.pack(pady=5)

record_btn_with_color = tk.Button(root, text="Record Next Position (With Color Check)", command=record_next_click_with_color)
record_btn_with_color.pack(pady=5)

record_drag_btn = tk.Button(root, text="Record Drag (2 Clicks)", command=record_drag_points)
record_drag_btn.pack(pady=5)

# --- IF Block 相關按鈕 ---
if_color_btn = tk.Button(root, text="Record IF Color Condition", command=record_if_color_point)
if_color_btn.pack(pady=5)

end_if_btn = tk.Button(root, text="END IF Block", command=end_if_block)
end_if_btn.pack(pady=5)
# --- IF Block 相關按鈕結束 ---


start_btn = tk.Button(root, text="Start Clicking", command=start_clicking)
start_btn.pack(pady=5)

stop_btn = tk.Button(root, text="Stop", command=stop_clicking)
stop_btn.pack(pady=5)

delete_btn = tk.Button(root, text="Delete Selected Position", command=delete_selected)
delete_btn.pack(pady=5)

save_btn = tk.Button(root, text="Save Positions", command=save_positions)
save_btn.pack(pady=5)

load_btn = tk.Button(root, text="Load Positions", command=load_positions)
load_btn.pack(pady=5)

clear_btn = tk.Button(root, text="Clear All Positions", command=lambda: (click_positions.clear(), update_click_list(), status_label.config(text="cleared")))
clear_btn.pack(pady=5)

# 啟動 GUI 主迴圈
root.mainloop()

# 確保在 GUI 關閉時停止監控執行緒
is_monitoring_mouse = False