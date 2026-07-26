''' Get KICKR gears and display in a mini window.'''

import tkinter as tk
import asyncio
import threading
from queue import Queue
import json
import os
from bleak import BleakClient, BleakScanner
import subprocess

# Debug flag
_DEBUG_ON = False

def debug_log(message):
    """Write debug message to log file if debugging is enabled."""
    if _DEBUG_ON:
        try:
            with open(os.path.expanduser("~/kickr_debug.log"), "a") as f:
                f.write(f"{message}\n")
        except:
            pass

# Try to import PyObjC for better macOS window management
try:
    from AppKit import NSApp, NSWindow
    from Cocoa import NSWindowStyleMaskTitled
except ImportError:
    NSApp = None
    NSWindow = None
    NSWindowStyleMaskTitled = None

# Thread-safe queue for callbacks to update GUI
gears_queue = Queue()
grade_queue = Queue()

# Global state for lock status and grade (persistent)
current_lock_status = None
current_grade = None
window_scale = 1.0  # Default scale factor
window_opacity = 0.8  # Default window opacity (0.0 = transparent, 1.0 = opaque)
TITLE_BAR_HEIGHT = 32  # macOS title bar height in pixels
DEFAULT_WINDOW_X = 100
DEFAULT_WINDOW_Y = 100
DEFAULT_WINDOW_SCALE = 1.0
DEFAULT_WINDOW_OPACITY = 0.8

WINDOW_BASE_WIDTH_DISCONNECTED = 320
WINDOW_BASE_WIDTH_CONNECTED = 240
WINDOW_BASE_HEIGHT = 130

HUD_FONT_SCALE = 0.9
HUD_FONT_GEARS = 35
HUD_FONT_GRADE = 25
HUD_FONT_DIALOG = 10

DIALOG_WIDTH = 300
DIALOG_HEIGHT = 240
SLIDER_MIN_SCALE = 0.45
SLIDER_MAX_SCALE = 1.5
SLIDER_RESOLUTION = 0.05
OPACITY_MIN = 10
OPACITY_MAX = 100
OPACITY_RESOLUTION = 1

BUTTON_WIDTH = 10
BUTTON_HEIGHT = 1
QUIT_BOX_SIZE = 10
QUIT_BOX_COLOR = 'red'

DIALOG_PAD_Y = 10
DIALOG_SLIDER_PAD_Y = 10
DIALOG_SLIDER_PAD_X = 20
LABEL_PAD_X = 10
LABEL_PAD_Y = 0
FOCUS_REFRESH_DELAY_MS = 100

PROCESS_QUEUE_INTERVAL_MS = 100

def hud_font(base_size, bold=True):
    """Return a pixel-sized font tuple for stable rendering across Tk versions."""
    pixel_size = max(1, int(round(base_size * window_scale * HUD_FONT_SCALE)))
    if bold:
        return ("Helvetica", -pixel_size, "bold")
    return ("Helvetica", -pixel_size)

# Config file path in script directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "kickr_gears_config.json")

def load_config():
    """Load window position and scale from config file."""
    global window_scale, window_opacity
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                x = config.get('window_x', DEFAULT_WINDOW_X)
                y = config.get('window_y', DEFAULT_WINDOW_Y)
                window_scale = config.get('scale', DEFAULT_WINDOW_SCALE)
                window_opacity = config.get('opacity', DEFAULT_WINDOW_OPACITY)
                # Adjust Y position for title bar height (starts visible)
                return x, y - TITLE_BAR_HEIGHT, window_scale
        except Exception as e:
            print(f"Error loading config: {e}")
            return DEFAULT_WINDOW_X, DEFAULT_WINDOW_Y, DEFAULT_WINDOW_SCALE

def load_dialog_position():
    """Load dialog position from config file."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                dialog_x = config.get('dialog_x', None)
                dialog_y = config.get('dialog_y', None)
                return dialog_x, dialog_y
        except Exception as e:
            print(f"Error loading dialog config: {e}")
    return None, None

def save_dialog_position(x, y):
    """Save dialog position to config file."""
    try:
        config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
        config['dialog_x'] = x
        config['dialog_y'] = y
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Error saving dialog config: {e}")

def load_window_position():
    """Load window position from config file, adjusting for title bar height."""
    x, y, _ = load_config()
    return x, y

def save_window_position(x, y):
    """Save window position to config file, adjusting for title bar height."""
    global window_scale, window_opacity
    try:
        config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
        config['window_x'] = x
        config['window_y'] = y
        config['scale'] = window_scale
        config['opacity'] = window_opacity
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}")

def calc_grade(data: bytearray):
    """Decode grade and lock status from KICKR characteristic.
    Format: [lock_unlock_byte_0, lock_unlock_byte_1, grade_low, grade_high]
    Returns: grade value with persistent lock status
    """
    global current_lock_status, current_grade
    
    if len(data) >= 3 and data[0] == 0xfd and data[1] == 0x33:
        # Lock/unlock status - update and persist it
        current_lock_status = data[2] == 0x01
    
    if len(data) >= 4 and data[0] == 0xfd and data[1] == 0x34:
        # Grade data
        if data[3] < 0x80:
            # Positive grade
            grade_value = (data[3] << 8 | data[2]) / 100.0
            current_grade = f"+{grade_value:.1f}%"
        else:
            # Negative grade
            tmp16 = 0xffff - (data[3] << 8 | data[2])
            grade_value = tmp16 / 100.0
            current_grade = f"-{grade_value:.1f}%"
    
    # Build output with persistent grade and lock status
    if current_grade is not None:
        if current_lock_status is not None:
            lock_text = "L" if current_lock_status else "U"
            return f"{current_grade} ({lock_text})"
        else:
            return current_grade
    
    # If we only have lock status without grade
    if current_lock_status is not None:
        return "L" if current_lock_status else "U"
    
    return None

# Global reference to current dialog (only one allowed at a time)
current_dialog = None
dragging_enabled = False  # Window dragging disabled by default

def create_mini_window():
    global window_scale
    
    debug_log(f"===== Creating new window =====")
    after_ids = []  # Track scheduled callbacks for cleanup
    root = tk.Tk() # Main window
    root.title("KICKR Gears")
    
    # Load saved window position and scale
    saved_x, saved_y, window_scale = load_config()
    
    # Apply scale
    scaled_width = int(WINDOW_BASE_WIDTH_DISCONNECTED * window_scale)
    scaled_height = int(WINDOW_BASE_HEIGHT * window_scale)
    
    root.geometry(f"{scaled_width}x{scaled_height}+{saved_x}+{saved_y}")
    
    root.resizable(False, False) # Make it non-resizable
    root.attributes('-topmost', True) # Keep it on top (optional)
    root.attributes('-alpha', window_opacity)  # Set window opacity (0.0 = transparent, 1.0 = opaque)
    # Start with title bar visible so window can receive focus
    
    # Set dull sky blue background (matching Zwift HUD)
    root.configure(bg='#486578')
    
    # Get window ID for macOS focus management
    window_id = root.winfo_id()
    
    # Make the window focusable using PyObjC on macOS
    if NSWindow is not None:
        try:
            from AppKit import NSApplication
            ns_windows = NSApplication.sharedApplication().windows()
            for ns_window in ns_windows:
                if ns_window.windowNumber() == window_id:
                    # Make window can become key window
                    ns_window.setCanBecomeKeyWindow_(True)
                    ns_window.setCanBecomeMainWindow_(True)
                    break
        except Exception as e:
            print(f"Failed to make window focusable: {e}")

    # Variables for window dragging and title bar state
    drag_data = {"x": 0, "y": 0}
    title_bar_visible = [True]  # Start with title bar visible
    

    def on_press(event):
        """Toggle title bar on left click, record position for drag."""
        # Toggle title bar visibility
        if title_bar_visible[0]:
            title_bar_visible[0] = not title_bar_visible[0]
            root.overrideredirect(not title_bar_visible[0])
        root.focus_set()  # Try to focus when clicking
        
        # Record position for potential drag
        drag_data["x"] = event.x_root - root.winfo_x()
        drag_data["y"] = event.y_root - root.winfo_y()

    def on_drag(event):
        """Move window on mouse drag."""
        if not dragging_enabled:
            return
        x = event.x_root - drag_data["x"]
        y = event.y_root - drag_data["y"]
        root.geometry(f"+{x}+{y}")
    
    def on_button_release(event):
        """Save window position when drag is complete (button release)."""
        # Adjust for title bar height if title bar is visible
        x = root.winfo_x()
        y = root.winfo_y()
        if title_bar_visible[0]:
            y += TITLE_BAR_HEIGHT
        save_window_position(x, y)

    def on_closing():
        """Close window and cancel callbacks."""
        # Cancel all pending after callbacks
        for after_id in after_ids:
            try:
                root.after_cancel(after_id)
            except:
                pass
        root.destroy()
    
    def show_scale_dialog(event):
        """Show scale adjustment dialog on right-click."""
        global window_scale, current_dialog
        
        if title_bar_visible[0]:
            title_bar_visible[0] = not title_bar_visible[0]
            root.overrideredirect(not title_bar_visible[0])
            
        # Close any existing dialog first - only allow one dialog at a time
        if current_dialog is not None:
            try:
                current_dialog.destroy()
            except:
                pass
            current_dialog = None
        
        dialog = tk.Toplevel(root)
        current_dialog = dialog  # Track this dialog
        
        # Double the quit box size on first dialog open only
        if not quit_box_doubled[0]:
            doubled_size = quit_box_size[0] * 2
            quit_box.place(x=0, y=0, width=doubled_size, height=doubled_size)
            quit_box_doubled[0] = True
        
        debug_log(f"Creating dialog {id(dialog)} for root {id(root)}")
        
        dialog.title("Window Scale")
        
        # Load saved dialog position
        dialog_x, dialog_y = load_dialog_position()
        if dialog_x is not None and dialog_y is not None:
            dialog.geometry(f"{DIALOG_WIDTH}x{DIALOG_HEIGHT}+{dialog_x}+{dialog_y}")
        else:
                dialog.geometry(f"{DIALOG_WIDTH}x{DIALOG_HEIGHT}")
        
        dialog.resizable(False, False)  # Prevent resizing
        dialog.attributes('-topmost', True)
        
        # Scale slider
        scale_slider = tk.Scale(dialog, from_=SLIDER_MIN_SCALE, to=SLIDER_MAX_SCALE, resolution=SLIDER_RESOLUTION, 
                               orient=tk.HORIZONTAL, showvalue=True)
        scale_slider.set(window_scale)
        scale_slider.pack(pady=DIALOG_SLIDER_PAD_Y, padx=DIALOG_SLIDER_PAD_X, fill=tk.X)

        # Opacity slider
        opacity_label = tk.Label(dialog, text="Opacity (%)", font=hud_font(HUD_FONT_DIALOG), bg=dialog.cget('bg'))
        opacity_label.pack(pady=(5, 0), padx=20, anchor='w')
        opacity_slider = tk.Scale(dialog, from_=OPACITY_MIN, to=OPACITY_MAX, resolution=OPACITY_RESOLUTION,
                                 orient=tk.HORIZONTAL, showvalue=True)
        opacity_slider.set(int(window_opacity * 100))
        opacity_slider.pack(pady=(0, 10), padx=20, fill=tk.X)
        
        # Dragging checkbox
        dragging_var = tk.BooleanVar(value=dragging_enabled)
        dragging_checkbox = tk.Checkbutton(dialog, text="Enable Window Dragging",
                                          variable=dragging_var,
                                          font=hud_font(HUD_FONT_DIALOG, bold=False))
        dragging_checkbox.pack(pady=5)
        
        debug_log(f"Creating dialog {id(dialog)} for root {id(root)}")
        
        # Apply button
        def apply_scale():
            global window_scale, current_dialog, dragging_enabled, window_opacity
            
            # Get new scale, opacity and dragging preference
            new_scale = scale_slider.get()
            new_opacity = opacity_slider.get() / 100.0
            dragging_enabled = dragging_var.get()
            
            # Save dialog position
            dialog_x = dialog.winfo_x()
            dialog_y = dialog.winfo_y()
            save_dialog_position(dialog_x, dialog_y)
            
            # Close dialog
            dialog.destroy()
            current_dialog = None
            
            # If opacity changed, apply it immediately
            if abs(new_opacity - window_opacity) > 0.001:
                window_opacity = new_opacity
                try:
                    root.attributes('-alpha', window_opacity)
                except Exception:
                    pass
            
            # If scale changed, update window in place
            if abs(new_scale - window_scale) > 0.001:
                window_scale = new_scale
                
                # Save current position
                current_x = root.winfo_x()
                current_y = root.winfo_y()
                
                # Update all widget sizes WITHOUT toggling overrideredirect
                gear_label_front.config(font=hud_font(35))
                gear_label_back.config(font=hud_font(35))

                grade_label.config(font=hud_font(25))
                
                # Update window size (use wider width at 60% scale or less for 2-digit gears)
                if "Front Gear:" in gear_label_front.cget("text"):
                    base_width = WINDOW_BASE_WIDTH_CONNECTED
                    scaled_width = int(base_width * window_scale)
                else:
                    base_width = WINDOW_BASE_WIDTH_DISCONNECTED
                    scaled_width = int(base_width * window_scale)
                scaled_height = int(WINDOW_BASE_HEIGHT * window_scale)
                root.geometry(f"{scaled_width}x{scaled_height}+{current_x}+{current_y}")
                
                # Force update to apply changes
                root.update_idletasks()
                
                # Refocus window to ensure keyboard shortcuts work
                root.focus_force()
                root.lift()
                root.attributes('-topmost', True)
                
                debug_log(f"APPLY: Updated window scale to {window_scale}")
        
        def cancel_scale():
            global current_dialog
            
            # Save dialog position
            dialog_x = dialog.winfo_x()
            dialog_y = dialog.winfo_y()
            save_dialog_position(dialog_x, dialog_y)
            
            # Close dialog
            dialog.destroy()
            current_dialog = None
            
            debug_log(f"CANCEL: Dialog closed")
        
        # Button frame for Apply and Cancel
        button_frame = tk.Frame(dialog)
        button_frame.pack(pady=DIALOG_PAD_Y, fill=tk.X)
        
        apply_button = tk.Button(button_frame, text="Apply", command=apply_scale,
                    font=hud_font(HUD_FONT_DIALOG), width=BUTTON_WIDTH, height=BUTTON_HEIGHT)
        apply_button.grid(row=0, column=0, padx=5)
        
        cancel_button = tk.Button(button_frame, text="Cancel", command=cancel_scale,
                     font=hud_font(HUD_FONT_DIALOG), width=BUTTON_WIDTH, height=BUTTON_HEIGHT)
        cancel_button.grid(row=0, column=1, padx=5)
        
        # Center the buttons in the frame
        button_frame.grid_columnconfigure(0, weight=1)
        button_frame.grid_columnconfigure(1, weight=1)
        
        # Quit button (centered below Apply and Cancel)
        def quit_app():
            global current_dialog
            dialog.destroy()
            current_dialog = None
            on_closing()
        
        quit_button = tk.Button(button_frame, text="Quit App", command=quit_app,
                       font=hud_font(HUD_FONT_DIALOG), width=BUTTON_WIDTH, height=BUTTON_HEIGHT)
        quit_button.grid(row=1, column=0, columnspan=2, pady=(5, 0))

        # Also handle window close button (X)
        dialog.protocol("WM_DELETE_WINDOW", cancel_scale)
        
        # CRITICAL: Force complete rendering with multiple passes
        # This is needed especially when running as a .app bundle
        for _ in range(3):
            dialog.update_idletasks()
            dialog.update()
        
        # Ensure window is visible and on top
        dialog.deiconify()
        dialog.lift()
        dialog.focus_force()
        
        # One final update after making visible
        dialog.update_idletasks()

    # Add content with Zwift-style font (Helvetica/system-ui, white text on blue background)
    gear_label_front = tk.Label(root, text="Connecting to", font=hud_font(HUD_FONT_GEARS), 
                     fg="white", bg='#486578', justify=tk.LEFT, anchor="w")
    gear_label_front.pack(pady=LABEL_PAD_Y, fill=tk.X, padx=LABEL_PAD_X)
    
    gear_label_back = tk.Label(root, text="KICKR", font=hud_font(HUD_FONT_GEARS), 
                     fg="white", bg='#486578', justify=tk.LEFT, anchor="w")
    gear_label_back.pack(pady=LABEL_PAD_Y, fill=tk.X, padx=LABEL_PAD_X)
    
    # Add grade label at the bottom, left-aligned
    grade_label = tk.Label(root, text="Grade: --", font=hud_font(HUD_FONT_GRADE), 
                           fg="white", bg='#486578', justify=tk.LEFT, anchor="w")
    grade_label.pack(pady=LABEL_PAD_Y, fill=tk.X, padx=LABEL_PAD_X)
    
    # Add 2x2 pixel black quit box in top left corner
    quit_box = tk.Label(root, bg=QUIT_BOX_COLOR, width=QUIT_BOX_SIZE, height=1)
    quit_box.place(x=0, y=0, width=QUIT_BOX_SIZE, height=QUIT_BOX_SIZE)
    quit_box_size = [QUIT_BOX_SIZE]  # Track current size [normal_size]
    quit_box_doubled = [False]  # Track if quit box has been doubled
    quit_box.bind("<Button-1>", lambda e: on_closing())
    
    # Bind mouse events for dragging
    def on_press_with_focus(event):
        """Record initial position on mouse press and focus window."""
        on_press(event)
        # Re-focus after toggling title bar
        root.after(FOCUS_REFRESH_DELAY_MS, root.focus_set)
    
    root.bind("<Button-1>", on_press_with_focus)
    root.bind("<B1-Motion>", on_drag)
    root.bind("<ButtonRelease-1>", on_button_release)  # Save position when drag completes
    root.bind("<Button-2>", show_scale_dialog)  # Right-click to show scale dialog (Button-2 on macOS)
    root.bind("<Button-3>", show_scale_dialog)  # Also try Button-3 for compatibility
    
    # Bind keyboard events to exit the app (bind to label for better focus capture)
    def on_key_press(event):
        """Exit app on Escape, X, E, or Q key press."""
        if event.keysym in ('Escape', 'x', 'X', 'e', 'E', 'q', 'Q'):
            on_closing()
    
    root.bind("<KeyPress>", on_key_press)
    gear_label_front.bind("<KeyPress>", on_key_press)
    gear_label_back.bind("<KeyPress>", on_key_press)
    grade_label.bind("<KeyPress>", on_key_press)
    
    # Set initial focus on the window
    root.focus_set()
    
    # Set close handler for title bar close button
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    # Function to process queue updates from async callbacks
    def process_queue():
        # Check if window still exists before processing
        try:
            if not root.winfo_exists():
                return  # Window destroyed, stop processing
        except:
            return  # Window destroyed, stop processing
        
        try:
            while True:
                gear_data = gears_queue.get_nowait()
                if gear_data[0].startswith("Front Gear:"):
                    # Set window width for gear display (scaled, wider at 60% or less)
                    base_width = WINDOW_BASE_WIDTH_CONNECTED
                    scaled_width_connected = int(base_width * window_scale)
                    scaled_height = int(WINDOW_BASE_HEIGHT * window_scale)
                    root.geometry(f"{scaled_width_connected}x{scaled_height}+{root.winfo_x()}+{root.winfo_y()}")
                else:
                    # Increase window width when disconnected (scaled, wider at 60% or less)
                    base_width = WINDOW_BASE_WIDTH_DISCONNECTED
                    scaled_width_disconnected = int(base_width * window_scale)
                    scaled_height = int(WINDOW_BASE_HEIGHT * window_scale)
                    root.geometry(f"{scaled_width_disconnected}x{scaled_height}+{root.winfo_x()}+{root.winfo_y()}")

                gear_label_front.config(text=gear_data[0])
                gear_label_back.config(text=gear_data[1])
        except:
            pass
        
        # Process grade updates
        try:
            while True:
                grade_data = grade_queue.get_nowait()
                grade_label.config(text=f"Grade: {grade_data}")
        except:
            pass
        
        # Force window to stay visible over fullscreen apps using AppleScript
        try:
            applescript = """
            tell application "System Events"
                set frontmost of (every window whose name is "KICKR Gears") to true
            end tell
            """
            subprocess.run(['osascript', '-e', applescript], capture_output=True)
        except:
            pass
        
        # Also use tkinter methods
        try:
            root.lift()
            root.attributes('-topmost', True)
        except:
            return  # Window destroyed, stop processing
        
        # Schedule next check only if window still exists
        try:
            after_id = root.after(PROCESS_QUEUE_INTERVAL_MS, process_queue)
            after_ids.append(after_id)
        except:
            pass  # Window destroyed, stop scheduling

    # Start processing queue
    after_id = root.after(PROCESS_QUEUE_INTERVAL_MS, process_queue)
    after_ids.append(after_id)
    
    debug_log(f"Starting mainloop for root {id(root)}")
         
    try:
        root.mainloop() # Start the event loop
    except SystemExit as e:
        debug_log(f"Mainloop SystemExit for root {id(root)}: {e}")
    except Exception as e:
        debug_log(f"Mainloop exception for root {id(root)}: {e}")
    
    # Mainloop has exited
    debug_log(f"Mainloop exited for root {id(root)}")
    
    # Clean up if window still exists
    try:
        if root.winfo_exists():
            debug_log(f"Destroying root {id(root)} after mainloop exit")
            root.destroy()
        else:
            debug_log(f"Root {id(root)} already destroyed")
    except Exception as e:
        debug_log(f"Error during cleanup of root {id(root)}: {e}")


def decode_gears(data: bytearray):
    """
    Attempt to decode gear data from Wahoo KICKR Bike.
    Typically: [front_gear, rear_gear] as two bytes.
    """
    if len(data) >= 2:
        front = 1 + data[2]
        rear = 1 + data[3]
        return (f"Front Gear: {front}", f"Rear Gear : {rear}")
    else:
        return ("Bad Gear", "Data")

async def gears_notification_handler(sender, data):
    """Handle incoming notifications and decode gear info."""
    decoded = decode_gears(data)
    # Push update to thread-safe queue for tkinter to consume
    gears_queue.put(decoded)

async def grade_notification_handler(sender, data):
    """Handle incoming grade and lock status notifications."""
    grade_info = calc_grade(data)
    if grade_info:
        grade_queue.put(grade_info)

async def main(shutdown_event):
    while not shutdown_event.is_set():
        try:
            gears_queue.put(("Scanning for","KICKR..."))
            devices = await BleakScanner.discover()
            kicker = None
            for d in devices:
                if d.name and "KICKR" in d.name:  # Adjust if your device name differs
                    kicker = d
                    break

            if not kicker:
                gears_queue.put(("KICKR not found.","Retrying..."))
                try:
                    await asyncio.wait_for(asyncio.shield(asyncio.sleep(1)), timeout=1.1)
                except asyncio.TimeoutError:
                    pass
                if shutdown_event.is_set():
                    break
                continue

            async with BleakClient(kicker.address) as client:
                if not client.is_connected:
                    gears_queue.put(("Connection failed.","Retrying..."))
                    try:
                        await asyncio.wait_for(asyncio.shield(asyncio.sleep(1)), timeout=1.1)
                    except asyncio.TimeoutError:
                        pass
                    if shutdown_event.is_set():
                        break
                    continue

                gears_queue.put((f"Connected to:", kicker.name))
                await asyncio.sleep(3)  # allow services to populate
                services = client.services  # safer than await

                test_uuid = 'a026e03a-0a7d-4ab3-97fa-f1500f9feb8b' 
                grade_uuid = 'a026e037-0a7d-4ab3-97fa-f1500f9feb8b'  # Grade characteristic UUID
                
                if test_uuid:
                    await client.start_notify(test_uuid, gears_notification_handler)
                    # Also subscribe to grade notifications
                    try:
                        await client.start_notify(grade_uuid, grade_notification_handler)
                    except Exception as e:
                        print(f"Could not subscribe to grade characteristic: {e}")
                    
                    # Keep connection alive while tkinter window runs
                    try:
                        while client.is_connected and not shutdown_event.is_set():
                            await asyncio.sleep(0.1)
                        # Connection was lost or shutdown requested
                        if not shutdown_event.is_set():
                            gears_queue.put(("Connection lost.","Reconnecting..."))
                        else:
                            # Gracefully stop notifications on shutdown
                            try:
                                await client.stop_notify(test_uuid)
                            except:
                                pass
                            try:
                                await client.stop_notify(grade_uuid)
                            except:
                                pass
                    except Exception as e:
                        if not shutdown_event.is_set():
                            gears_queue.put((f"Connection error {e}", "Reconnecting..."))
        except asyncio.CancelledError:
            debug_log("Async main cancelled, exiting cleanly")
            break
        except Exception as e:
            if not shutdown_event.is_set():
                gears_queue.put((f"Error: {e}", "Retrying..."))
                try:
                    await asyncio.wait_for(asyncio.shield(asyncio.sleep(1)), timeout=1.1)
                except asyncio.TimeoutError:
                    pass

if __name__ == "__main__":
    # Create shutdown event to signal threads to exit gracefully
    shutdown_event = threading.Event()
    
    # Start async BLE connection in a separate thread
    def run_async_main():
        try:
            asyncio.run(main(shutdown_event))
        except Exception as e:
            debug_log(f"Error in BLE thread: {e}")
        finally:
            debug_log("BLE thread exiting")
    
    ble_thread = threading.Thread(target=run_async_main, daemon=False)  # Not a daemon - we want to control exit
    ble_thread.start()
    
    # Start tkinter GUI (blocks until window closes)
    debug_log(f"Main loop: Calling create_mini_window()")
    try:
        create_mini_window()
    except Exception as e:
        debug_log(f"Exception in create_mini_window: {e}")
    
    debug_log(f"Main loop: Window closed, initiating shutdown")
    
    # Signal the BLE thread to shut down gracefully
    shutdown_event.set()
    
    # Wait for BLE thread to exit (with timeout)
    ble_thread.join(timeout=3.0)
    
    if ble_thread.is_alive():
        debug_log("BLE thread did not exit in time, force exiting")
    else:
        debug_log("BLE thread exited cleanly")
    
    debug_log(f"Main loop: Exiting application")


