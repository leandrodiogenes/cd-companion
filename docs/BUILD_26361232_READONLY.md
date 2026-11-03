# Proposed opt-in tracking recovery: Steam build 26361232

This is a draft integration proposal, not a general game-version compatibility claim.

On game executable 1.0.0.3479 (Steam 26361232), the old entity/static-position signatures are absent. Android can connect to the WebSocket server but remains at "Waiting for player". The updated static signature is unique on the exact executable below.

Set these environment variables before starting the existing launcher, and select PC Overlay or Server Only:

```powershell
$env:CD_READ_ONLY_26361232 = '1'
$env:CD_TELEPORT_ENABLED = '0'
```

The opt-in reader requests only PROCESS_QUERY_INFORMATION | PROCESS_VM_READ. It verifies executable SHA-256 `DF531A585BF84CEB47779304CC8B3CAAF976AEA88424C32605885623CD4519C7`, requires a unique signature, validates RIP targets and sample plausibility, and rejects unknown builds. It allocates no game memory, installs no hooks, and has no write or teleport implementation. Existing behavior is unchanged when the flag is unset. Disable this opt-in before using a different supported build through the legacy engine.

The existing `hooks_installed` / `scan_and_hook` interface names are retained for compatibility with the broadcast loop; no hooks are installed by this reader. Camera heading, entity-dependent functions, teleport and game-map marker capture are unavailable. Maintainer review is requested before enabling this path automatically or exposing it in Settings.

## Validation and limits

A separate local server-only build using this reader and the existing CD Companion message protocol was tested against the exact game executable with Android CD Companion 1.0.4. Coordinates and the player marker changed while walking in Hernand. Unknown-executable rejection, disabled teleport and handle cleanup were checked. The integrated upstream launcher in this PR has not been packaged or live-tested: its environment-flag selection needs maintainer validation. Unit tests cover missing/duplicate signatures, out-of-module targets and loading/invalid samples.

Abyss, realm transitions, game restarts and world-origin boundary crossings are not yet validated. This change does not claim to repair the legacy teleport engine.

## Attribution

The updated signature and exact-build identity come from the MIT-licensed CrimsonDesertTelemetry v2.2.6 definition:
https://github.com/ZappendusterFX/CrimsonDesertTelemetry/blob/v2.2.6/definitions/build-26361232.json

Its notice is retained in `TELEMETRY_SIGNATURE_LICENSE.txt`. The reader implementation is new Python code; no Telemetry binary or game plugin is required.
