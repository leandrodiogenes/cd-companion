## v0.11.06

### Fixed
- Hide Left Panel and Hide Right Panel sometimes failed to close the sidebars on startup. The close action could run before the map page finished initializing and was ignored. The overlay now retries until each panel is actually hidden.

## v0.11.05

### Fixed
- Nearby Locations could stop loading category data after a map website update. The overlay now adapts to those updates automatically, so the feature keeps working without an app update.

## v0.11.04

### Fixed
- After a Crimson Desert update, the companion could crash the game while loading a save. Offsets cached from a previous game version were reused without validation, which could make the companion patch the wrong memory address. Cached offsets are now validated on attach, and the whole cache is rebuilt automatically when a game update is detected. Deleting the cached offsets file by hand is no longer required.

## v0.11.03

### Fixed
- Restored "Rotate map with camera" after the Crimson Desert 1.09.00 update. Following the patch, the camera heading was no longer detected, which disabled camera based map rotation. It works again.
- Sidebar auto-hide no longer sends an extra click when the sidebar is already closed.

## v0.11.02

### Fixed
- Switching to circular mode via controller combo no longer triggers the combo buttons in-game. Focus now returns to the game after a short delay, giving time for the buttons to be released.
- Overlay follow mode now correctly tracks the player position when moving in a straight line with "Rotate map with camera" enabled. Previously, the map only re-centered when the camera heading changed; it now re-centers on every position update.

## v0.11.01

### Added
- Toggle window shape hotkey: switch between square and circular window using a configurable keyboard shortcut or controller button combo. Each mode remembers its own position and size. Switching to square focuses the overlay; switching to circular returns focus to the game. Configure in Settings > Window > Toggle window shape.

### Changed
- Waypoints panel: larger touch targets across the board. Each waypoint row is now at least 44px tall, the Teleport and Delete buttons are bigger, the filter input and Save button have more padding, and the panel is slightly wider.
- Resize ring in circular mode is now golden instead of blue.

### Fixed
- Floating buttons (Waypoints, Teleport to Center, Follow) now correctly reposition when toggling between square and circular window via hotkey or controller combo.
- Center teleport Y slider now starts at 0 and moves in steps of 5.

## v0.11.00

### Added
- High DPI scaling: the settings UI now scales correctly on displays set to 125%, 150%, or any other Windows display scaling. Can be toggled in Settings > Performance > High DPI scaling (enabled by default, requires restart).

## v0.10.05

### Fixed
- Hook cache validation: stale `hook_e` cache entries (left over from a previous game patch) are now detected and cleared automatically on attach. Previously, a stale entry could cause the companion to install its hook at the wrong address after a game update, crashing the game when teleporting.

## v0.10.04

### Fixed
- Teleport hotkeys can now be changed from Settings > Teleport, including Go to Marker and Abort.

## v0.10.03

### Added
- German language support, contributed by HaZt-Panda.
- Chinese (Simplified) and Chinese (Traditional) language support, contributed by yv4ev3r.

## v0.10.02

### Fixed
- Configurable keyboard hotkeys now support single keys, one-modifier shortcuts, and multi-modifier shortcuts. F13-F24 and numpad keys can also be assigned.

## v0.10.01

### Added
- Korean language (ko) contributed by Doleun.

## v0.10.00

### Added
- Language support: the overlay UI is now fully translated. Switch between English and Português (Brasil) in Settings > Window > Language.
- Community translations can be added by placing a JSON file in a `locales/` folder next to the exe — no rebuild required. See `overlay/locales/README.md` for the format.

## v0.09.02

### Fixed
- System tray icon not appearing in the compiled exe. The icon file is now correctly bundled and resolved at runtime.

## v0.09.01

### Fixed
- Launching a second instance of CD Companion now shows a warning and exits immediately, instead of opening a duplicate window.

## v0.09.00

### Added
- Show/hide overlay hotkey is now configurable in Settings > Window: choose any keyboard shortcut (default Ctrl+Shift+M) and optionally assign a controller combo (default: none).
- System tray icon: both full and server-only modes now show an icon in the system tray. Right-click to quit; in full mode, left-click or the "Show/Hide Overlay" menu item toggles the overlay window.

## v0.08.00

### Added
- Server-only mode: runs only the WebSocket server, hotkeys, and controller polling without opening the map window — ideal for users who rely on the Chrome extension or Android app as their map display.
- Mode selector dialog on startup to choose between full overlay and server-only mode.
- Focus toggle: configurable keyboard hotkey and controller combo to switch focus between Crimson Desert and the overlay map (disabled by default, set up in Settings > Window).
- Circular window mode now shows a subtle highlight ring around the edge when the cursor hovers over the resize area, making the draggable border easier to find.

### Fixed
- Circular window size is now preserved when toggling between circular and square mode.
- Help button removed from the Settings dialog title bar.
- Application now terminates correctly when the overlay window is closed.

## v0.07.00

### Added
- Waypoints panel: open/close via keyboard hotkey (default Shift+Y) and configurable controller combo (default DPad Down+A).
- Waypoints panel: controller navigation with D-pad Up/Down, A to teleport, Y to delete, B to close.
- Nearby popup: controller combo now configurable in Settings (default LB+Down).
- Nearby popup: "Stay in list" toggle that keeps focus in the current found/unfound list after marking a location, instead of following the item to the other list.
- Settings dialog reorganized into tabbed layout (Map, Window, Teleport, Nearby, Waypoints, Direction, Performance).
- All hotkey and controller combo inputs now have a Clear button to disable the binding.
- Configurable browser zoom level (70–150%) in Settings > Window, applied on page load and on save.

### Fixed
- Closing the waypoints popup with controller B no longer triggers the B action in Crimson Desert (delayed focus return to game window).
- Keyboard navigation improvements in the waypoints popup.

## v0.06.02

### Fixed
- Closing the nearby popup while the overlay is hidden no longer exits the entire application.

## v0.06.01

### Added
- Freedom Flyer compatibility: teleport now works when using both mods together.
- Optional shared entity base: player detection can use Freedom Flyer's shared memory, reducing hook conflicts. Toggle in Settings → Teleport.

## v0.06.00

### Added
- Nearby popup now pans the map to the selected location while navigating, highlights it with a red Mapbox layer, and returns to the player position when the popup closes.
- Nearby popup can now respect the category visibility currently selected on the map, with a Settings toggle to show all categories instead.
- Overlay Settings now includes a map icon size slider for adjusting location marker scale.
- Nearby popup items can be filtered by found/unfound status via gamepad Back button, with found items sorted to the bottom. D-pad left/right navigates pages.
- Title bar now supports double-click to maximize/restore the overlay window and includes a maximize button (square mode only).

### Fixed
- Improved overlay map smoothness when following the player — camera rotation and position updates now match the Chrome extension responsiveness.
- Nearby popup controller navigation now only works while the popup window is the active foreground window, preventing D-pad/A/B from changing the popup while playing with the game focused.
- Nearby scan radius slider now supports a smaller minimum radius.
- Nearby scan radius is displayed as a simple 1-8 value in Settings while still saving the internal 0.001-0.008 map radius.

### Removed
- Calibration UI (button and click-to-add) disabled from the overlay panel.

## v0.05.00

### Added
- Nearby popup: category icon and name shown per location, sourced from MapGenie data.
- Nearby popup: found/unfound badge overlaid on category icon (bottom-right corner).
- Nearby popup: locations sorted by distance, with distance value displayed per item.
- Nearby popup: details panel showing MapGenie image, title, category, description, and found state for the selected location.
- Description links to other MapGenie locations now pan all connected clients to that location instead of opening a browser page.
- Scan radius circle on the MapGenie map showing the nearby scan area (visible when nearby controls are enabled).
- Nearby scan radius configurable in Settings (0.003–0.008, default 0.005).
- Nearby popup hotkey configurable in Settings (saved to cd_hotkeys.json, restart required).

### Fixed
- Nearby popup no longer opens when pressing the assigned hotkey while editing it in Settings.

### Improved
- Nearby popup list no longer resets scroll on each refresh; skips render entirely when nothing changed.
- Nearby popup refresh is lighter while open: location details are cached, the details panel only re-renders when selection/found state changes, and the radius circle skips unchanged updates.
