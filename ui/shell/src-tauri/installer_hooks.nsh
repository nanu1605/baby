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
; The template's SECOND guard does not mean what it says, and that is how this hook
; came to delete a user's data during an UPGRADE. $UpdateMode is set only by a /UPDATE
; flag on the command line, which the built-in updater passes and a human never does.
; Double-clicking a newer Baby_x.y.z_x64-setup.exe over an existing install is an
; upgrade in every sense the user means -- but NSIS implements it by running the OLD
; uninstaller first (PageLeaveReinstall -> reinst_uninstall), with a plain ExecWait
; and no /UPDATE. So $UpdateMode is 0, the confirm page appears in full, and the
; "Delete application data" checkbox is live and destructive. Tick it there while
; believing you are upgrading, and every conversation and every API key is deleted a
; second before the new build installs onto the empty directory. That happened twice
; on the author's own machine during v6 testing.
;
; The fourth guard closes it. NSIS strips `_?=` out of $CMDLINE before the script
; runs -- measured; the uninstaller sees only its own quoted path either way -- so
; the flag itself cannot be read. What `_?=` DOES is observable: it tells the
; uninstaller not to copy itself to %TEMP% and re-exec, which is the only way a
; parent process can ExecWait on it. So the copy is the signal:
;
;   $EXEDIR == $INSTDIR   an installer is waiting on us  -> reinstall, keep the data
;   $EXEDIR <> $INSTDIR   we were copied to %TEMP%       -> real uninstall, honour it
;
; Measured both ways against this NSIS: %TEMP%\~nsu.tmp for a standalone run, the
; install directory for a reinstall. Add/Remove Programs, the Start Menu entry and
; double-clicking uninstall.exe all run the UninstallString without `_?=`, so all
; three land in the second branch and the checkbox still does exactly what the docs
; say. A user who wants a genuinely clean slate uninstalls first, then installs.
;
; The registers are pushed and popped because a hook has no claim on them, and the
; error flag is cleared because $INSTDIR is legitimately gone by this point on the
; standalone path -- GetFullPathName then yields an empty string, the compare cannot
; match, and the delete proceeds, which is right.
;
; GetFullPathName normalises both sides, and LogicLib's `!=` compares them the way
; Windows compares paths, case-insensitively. If $INSTDIR is already gone it yields
; an empty string and the compare cannot match -- which is the standalone branch,
; and the branch that is supposed to delete.
;
; POSTUNINSTALL runs after the template's own deletion block, where both variables
; are still in scope.

!macro NSIS_HOOK_POSTUNINSTALL
  Push $R4
  Push $R5
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    GetFullPathName $R4 "$EXEDIR"
    GetFullPathName $R5 "$INSTDIR"
    ClearErrors
    ${If} $R4 != $R5
      SetShellVarContext current
      IfFileExists "$LOCALAPPDATA\baby\.venv\*.*" baby_data_is_ours baby_data_not_ours
      baby_data_is_ours:
        RmDir /r "$LOCALAPPDATA\baby"
      baby_data_not_ours:
    ${EndIf}
  ${EndIf}
  Pop $R5
  Pop $R4
!macroend
