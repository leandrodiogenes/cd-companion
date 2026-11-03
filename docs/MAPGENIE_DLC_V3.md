# Proposed opt-in Pywel map-layer conversion

The current public Pywel page serves `crimson-desert/pywel/dlc-v3`, while the existing built-in calibration maps to the older layer. Correct game coordinates therefore place the player over empty terrain. This is independent of the game's player-position signature change.

For the read-only mode proposed in `BUILD_26361232_READONLY.md`, add:

```powershell
$env:CD_MAPGENIE_PYWEL_LAYER = 'dlc-v3'
```

This converts only outgoing Pywel player-position messages. It does not rewrite stored calibration, change the Android app, replace the map website or alter paid features. It requires read-only mode so transformed coordinates cannot accidentally be fed into legacy teleport commands. Leave unset on older map layers or when using a custom calibration already measured on the current layer. Abyss is intentionally unchanged and unvalidated.

## Derivation

Compared matching public landmark IDs from:

- Older snapshot: https://github.com/CoderFX/crimson-desert-interactive-progress-map/blob/master/data/markers/pywel.js
- Current data: https://mapgenie.io/api/v1/maps/887/data
- Current layer configuration: https://mapgenie.io/crimson-desert/maps/pywel

Using longitude and Mercator Y in degrees, least-squares affine fitting with residual trimming retained 6,603 Pywel landmarks. Median residual was approximately 9.64e-8 degrees and the 95th percentile was 3.58e-7 degrees. This measures conversion between datasets, not absolute in-game position accuracy. Source marker datasets are not bundled; only the coefficients and four small regression fixtures are included.

After applying the conversion in a separate local CD Companion server-only repair, the existing Android 1.0.4 app showed Hernand's streets, buildings and grocer beside the player marker, matching the user's in-game screenshot. Coordinates also progressed while walking. The upstream-integrated PR itself has not been packaged or tested through its original launcher; broader map travel, Abyss, realm transitions and future website-layer changes require validation.

This opt-in flag is a reviewable reference integration. Maintainer feedback is requested on layer discovery and Settings UI before making it automatic.
