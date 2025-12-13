# kickr_gears
Python Script/Mac App to view KICKR bike gears and grade in a HUD window over Zwift

## Installation

1. After downloading or cloning, drag the app into the Applications folder
2. Go to [python.org](https://python.org) and download and install Python 3.14

### Setting Up Dependencies

1. Open Terminal (press `⌘ + Space`, type "terminal", and press Enter)
2. The terminal will open in your home directory (`/Users/<your user name>`) using zsh (macOS default shell)
3. Run the following commands one at a time (copy, paste, and press Enter):

```zsh
python3 -m venv kickr
source kickr/bin/activate
python -m pip install -r ~/kickr_gears/requirements.txt
deactivate
```

## Running the App

Double-click the app in Applications. It will request permissions for:
- Bluetooth access
- System accessibility

Grant both permissions. 

**Note:** On first launch, you may need to open System Settings → Privacy & Security to manually grant permissions to run the app if macOS blocks it.

The app will display "Searching for the KICKR bike" and continuously try to connect. Turn on your bike and adjust the gears/grade to trigger the connection.

### First Launch Setup
1. After the app launches, **click the window once** to hide the title bar (thanks tkinter!)
2. Right-click the dock icon and select "Options" → "Keep in Dock"
3. Drag the icon next to the Zwift icon for easy access

## Window Configuration

### The App Window
- Semi-transparent background matching Zwift's middle HUD display
- Always stays on top of other windows
- When disconnected: larger window to display messages
- When connected: shrinks to a smaller size

### Enabling Window Dragging
1. Left-click the app window
2. Right-click and select "Enable Window Dragging"
3. Click "Accept"
4. Now you can click and hold anywhere in the window to drag it
5. To lock the window position (preventing accidental movement while riding), open the dialog and uncheck "Enable Window Dragging"

### Dialog Features
- Adjustable window size/height via slider
- Dialog position is remembered when closed
- Window position is also remembered between sessions

### Positioning
Position the app window anywhere you want within Zwift's window.

## Zwift Display Settings

**Important:** Zwift must NOT be in full screen mode for the app to be visible.

To properly size Zwift without using full screen:
1. Click the Zwift app title bar
2. Hover over the green button (top-left corner)
3. Wait for the dropdown menu
4. Under "Fill and Arrange", select the bottom-left icon to fill the screen

## Closing the App

### If Dialog Was Opened
Once you've opened the dialog, you can only close the app by:
- Clicking "Quit App" in the dialog
- Clicking the red square in the top-left of the window
- Right-clicking the dock icon and selecting "Quit"

### If Dialog Was Never Opened
You can also close by clicking the window and pressing `ESC`, `X`, `Q`, or `E`

## Quick Start Checklist

1. ✅ Install Python 3.14 from python.org
2. ✅ Drag app to Applications folder
3. ✅ Run terminal commands to set up dependencies
4. ✅ Launch app and grant permissions
5. ✅ Click window once to hide title bar
6. ✅ Set Zwift to "Fill" mode (not full screen)
7. ✅ Enable window dragging and position the app
8. ✅ Click Zwift window to start riding!

## Credits

The information used to create this app was found in the repositories and information in this thread:
https://forums.zwift.com/t/gear-display-for-all-smart-bikes/507898/21

While I'm very familiar with Python, I used GitHub Copilot to do most of the work with the app.
