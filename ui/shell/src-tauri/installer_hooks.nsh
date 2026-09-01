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
; The guard is the template's own: only when the user explicitly ticked the box,
; and never during an update (which reuses the same data dir).
;
; POSTUNINSTALL runs after the template's own deletion block, where both variables
; are still in scope.

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    SetShellVarContext current
    RmDir /r "$LOCALAPPDATA\baby"
  ${EndIf}
!macroend
