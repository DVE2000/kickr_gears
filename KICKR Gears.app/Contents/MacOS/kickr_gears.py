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

# Config file path in script directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "kickr_gears_config.json")

def load_config():
    """Load window position and scale from config file."""
    global window_scale
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                x = config.get('window_x', 100)
                y = config.get('window_y', 100)
                window_scale = config.get('scale', 1.0)
                # Adjust Y position for title bar height (starts visible)
                title_bar_height = 32
                return x, y - title_bar_height, window_scale
        except Exception as e:
            print(f"Error loading config: {e}")
    return 100, 100, 1.0

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
    global window_scale
    try:
        config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
        config['window_x'] = x
        config['window_y'] = y
        config['scale'] = window_scale
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
    
    # Base dimensions
    base_width_disconnected = 320
    base_width_connected = 265
    base_height = 150
    
    # Apply scale
    scaled_width = int(base_width_disconnected * window_scale)
    scaled_height = int(base_height * window_scale)
    
    root.geometry(f"{scaled_width}x{scaled_height}+{saved_x}+{saved_y}")
    
    root.resizable(False, False) # Make it non-resizable
    root.attributes('-topmost', True) # Keep it on top (optional)
    root.attributes('-alpha', 0.8)  # Set window opacity (0.0 = transparent, 1.0 = opaque)
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
        save_window_position(root.winfo_x(), root.winfo_y())

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
        
        debug_log(f"Creating dialog {id(dialog)} for root {id(root)}")
        
        dialog.title("Window Scale")
        
        # Load saved dialog position
        dialog_x, dialog_y = load_dialog_position()
        if dialog_x is not None and dialog_y is not None:
            dialog.geometry(f"300x170+{dialog_x}+{dialog_y}")
        else:
            dialog.geometry("300x170")
        
        dialog.resizable(False, False)  # Prevent resizing
        dialog.attributes('-topmost', True)
        
        # Scale slider
        scale_slider = tk.Scale(dialog, from_=0.45, to=1.5, resolution=0.05, 
                               orient=tk.HORIZONTAL, showvalue=True)
        scale_slider.set(window_scale)
        scale_slider.pack(pady=10, padx=20, fill=tk.X)
        
        # Dragging checkbox
        dragging_var = tk.BooleanVar(value=dragging_enabled)
        dragging_checkbox = tk.Checkbutton(dialog, text="Enable Window Dragging",
                                          variable=dragging_var,
                                          font=("Helvetica", 10))
        dragging_checkbox.pack(pady=5)
        
        debug_log(f"Creating dialog {id(dialog)} for root {id(root)}")
        
        # Apply button
        def apply_scale():
            global window_scale, current_dialog, dragging_enabled
            
            # Get new scale and dragging preference
            new_scale = scale_slider.get()
            dragging_enabled = dragging_var.get()
            
            # Save dialog position
            dialog_x = dialog.winfo_x()
            dialog_y = dialog.winfo_y()
            save_dialog_position(dialog_x, dialog_y)
            
            # Close dialog
            dialog.destroy()
            current_dialog = None
            
            # If scale changed, update window in place
            if abs(new_scale - window_scale) > 0.001:
                window_scale = new_scale
                
                # Save current position
                current_x = root.winfo_x()
                current_y = root.winfo_y()
                
                # Update all widget sizes WITHOUT toggling overrideredirect
                gear_font_size = int(35 * window_scale)
                gear_label_front.config(font=("Helvetica", gear_font_size, "bold"))
                gear_label_back.config(font=("Helvetica", gear_font_size, "bold"))
                
                grade_font_size = int(25 * window_scale)
                grade_label.config(font=("Helvetica", grade_font_size, "bold"))
                
                # Update window size (use wider width at 60% scale or less for 2-digit gears)
                if "Front Gear:" in gear_label_front.cget("text"):
                    base_width = 280 if window_scale <= 0.60 else 265
                    scaled_width = int(base_width * window_scale)
                else:
                    base_width = 335 if window_scale <= 0.60 else 320
                    scaled_width = int(base_width * window_scale)
                scaled_height = int(150 * window_scale)
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
        button_frame.pack(pady=10, fill=tk.X)
        
        apply_button = tk.Button(button_frame, text="Apply", command=apply_scale,
                                font=("Helvetica", 10, "bold"), width=10, height=1)
        apply_button.grid(row=0, column=0, padx=5)
        
        cancel_button = tk.Button(button_frame, text="Cancel", command=cancel_scale,
                                 font=("Helvetica", 10, "bold"), width=10, height=1)
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
                               font=("Helvetica", 10, "bold"), width=10, height=1)
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
    gear_font_size = int(35 * window_scale)
    gear_label_front = tk.Label(root, text="Connecting to", font=("Helvetica", gear_font_size, "bold"), 
                     fg="white", bg='#486578', justify=tk.LEFT, anchor="w")
    gear_label_front.pack(pady=0, fill=tk.X, padx=10)
    
    gear_label_back = tk.Label(root, text="KICKR", font=("Helvetica", gear_font_size, "bold"), 
                     fg="white", bg='#486578', justify=tk.LEFT, anchor="w")
    gear_label_back.pack(pady=0, fill=tk.X, padx=10)
    
    # Add grade label at the bottom, left-aligned
    grade_font_size = int(25 * window_scale)
    grade_label = tk.Label(root, text="Grade: --", font=("Helvetica", grade_font_size, "bold"), 
                           fg="white", bg='#486578', justify=tk.LEFT, anchor="w")
    grade_label.pack(pady=0, fill=tk.X, padx=10)
    
    # Add 2x2 pixel black quit box in top left corner
    quit_box = tk.Label(root, bg='red', width=7, height=1)
    quit_box.place(x=0, y=0, width=7, height=7)
    quit_box.bind("<Button-1>", lambda e: on_closing())
    
    # Bind mouse events for dragging
    def on_press_with_focus(event):
        """Record initial position on mouse press and focus window."""
        on_press(event)
        # Re-focus after toggling title bar
        root.after(100, root.focus_set)
    
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
                    base_width = 280 if window_scale <= 0.60 else 265
                    scaled_width_connected = int(base_width * window_scale)
                    scaled_height = int(150 * window_scale)
                    root.geometry(f"{scaled_width_connected}x{scaled_height}+{root.winfo_x()}+{root.winfo_y()}")
                else:
                    # Increase window width when disconnected (scaled, wider at 60% or less)
                    base_width = 335 if window_scale <= 0.60 else 320
                    scaled_width_disconnected = int(base_width * window_scale)
                    scaled_height = int(150 * window_scale)
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
            after_id = root.after(100, process_queue)
            after_ids.append(after_id)
        except:
            pass  # Window destroyed, stop scheduling

    # Start processing queue
    after_id = root.after(100, process_queue)
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

async def main():
    while True:
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
                await asyncio.sleep(1)
                continue

            async with BleakClient(kicker.address) as client:
                if not client.is_connected:
                    gears_queue.put(("Connection failed.","Retrying..."))
                    await asyncio.sleep(1)
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
                        while client.is_connected:
                            await asyncio.sleep(0.1)
                        # Connection was lost
                        gears_queue.put(("Connection lost.","Reconnecting..."))
                    except KeyboardInterrupt:
                        await client.stop_notify(test_uuid)
                        try:
                            await client.stop_notify(grade_uuid)
                        except:
                            pass
                        raise
                    except Exception as e:
                        gears_queue.put((f"Connection error {e}", "Reconnecting..."))
        except KeyboardInterrupt:
            break
        except Exception as e:
            gears_queue.put((f"Error: {e}", "Retrying..."))
            await asyncio.sleep(1)

if __name__ == "__main__":
    # Start async BLE connection in a separate thread
    def run_async_main():
        asyncio.run(main())
    
    ble_thread = threading.Thread(target=run_async_main, daemon=True)
    ble_thread.start()
    
    # Start tkinter GUI (blocks until window closes)
    debug_log(f"Main loop: Calling create_mini_window()")
    create_mini_window()
    debug_log(f"Main loop: Window closed, exiting")


