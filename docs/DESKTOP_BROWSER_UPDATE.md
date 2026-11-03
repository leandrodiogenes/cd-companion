# Desktop browser update

The current MapGenie desktop map uses JavaScript features that the Chromium 83 runtime shipped with the Windows PyQt5 wheel cannot execute. The sidebar can render while the map stays black. This update uses PySide6 (Qt 6) through QtPy, retaining the PC Overlay and Server Only selector and the existing WebSocket protocol.

Tracking injection is scheduled after the map style becomes ready, without waiting for advertising frames to finish loading. Markers use the latest available position when created. Popup windows use the Qt 6 page parent API, allowing the login form to open normally. Credentials remain user-entered. The legacy map.js category patch is optional via `CD_PATCH_MAPGENIE_MAP_JS=1`; normal map loading uses the current upstream scripts unchanged.

`--overlay` and `--server-only` select a mode directly for launch shortcuts and smoke tests. Starting without either argument shows the existing selector.

## Windows packaging

The launcher explicitly imports the selected PySide6 modules so PyInstaller includes them. The build specification keeps the private MSVC runtime from the selected Qt wheel and excludes unrelated ICU binaries discovered on the build machine's PATH. Qt uses the Windows ICU API instead. No system-wide runtime installation is required on the tested Windows 11 system.

## Validation

- Five existing coordinate/signature unit tests pass on Windows.
- A separate local read-only build with these desktop changes rendered the current Pywel tiles and location pins, opened the MapGenie login form, and displayed a player marker with Follow enabled.
- Its desktop map center matched the coordinates broadcast on port 7891 to Android CD Companion 1.0.4; the user confirmed both screens followed while walking.
- The packaged local application was also launched after isolating its dependencies. Authentication completion, account sync, and all legacy teleport/entity features have not been validated by this test.

The public integration retains the opt-in exact-build reader described in `BUILD_26361232_READONLY.md`; the default engine selection remains unchanged. The local read-only executable and the public integration are distinct builds. Maintainers should test the release package and their supported game configurations before release.
