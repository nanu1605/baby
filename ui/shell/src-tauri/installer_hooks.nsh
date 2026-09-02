; Baby v6 W5 -- make the uninstaller's "delete application data" option tell the truth.
;
; Tauri's NSIS template removes %APPDATA%\<BUNDLEID> and %LOCALAPPDATA%\<BUNDLEID>
; when that box is ticked -- for us, com.tanishq.baby. Baby's state has never lived
; there: core/paths.py and the shell both resolve BABY_HOME to %LOCALAPPDATA%\baby
; (a name chosen long before the app had a bundle id), and that directory holds
; config.yaml, the .env with the user's cloud API KEYS, baby.db with every
; conversation, the downloaded models, the logs, and the ~1.5 GB venv.
;
; So without this hook the checkbox silently deletes nothing. Three problems, in
; order of how much they matter: the user's API keys stay on disk after they
; believe they removed the app; every conversation stays with them; and ~3.5 GB is
; orphaned in a directory nothing will ever clean up. The shipped EULA (section 5)
; promises the opposite.
;
; Two guards are the template's own: only when the user explicitly ticked the box,
; and never during an update (which reuses the same data dir).
;
; The third is ours. %LOCALAPPDATA%\baby is NOT exclusive to an installed build --
; core/paths.py resolves the logs, browser profile and screenshot caches there for a
; DEV checkout too, whether or not BABY_HOME is set. On a machine that also runs Baby
; from source, an unguarded RmDir /r takes the developer's browser profile, logs,
; shots and file index with it. So delete only a directory an install actually
; provisioned, proven by the venv that first_run.ps1 builds there; a dev checkout
; keeps its venv in the repo and never creates this one.
;
; This does NOT make the shared case safe -- if an install and a checkout are both
; using the directory the venv is present and everything still goes. Nothing the
; uninstaller can see distinguishes those. What it does remove is the worst outcome:
; an uninstaller wiping a directory no install ever owned. tests/manual carries the
; warning for the case this cannot cover.
;
; POSTUNINSTALL runs after the template's own deletion block, where both variables
; are still in scope.

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    SetShellVarContext current
    IfFileExists "$LOCALAPPDATA\baby\.venv\*.*" baby_data_is_ours baby_data_not_ours
    baby_data_is_ours:
      RmDir /r "$LOCALAPPDATA\baby"
    baby_data_not_ours:
  ${EndIf}
!macroend
