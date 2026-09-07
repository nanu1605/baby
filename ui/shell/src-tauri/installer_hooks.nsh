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

; ---------------------------------------------------------------------------------
; Baby v6.0.2 -- refuse to report success over an install that did not change.
;
; A 6.0.2 installer was run over an existing 6.0.0 install and reported that it had
; finished. It had not. Measured on that machine afterwards: DisplayVersion still
; 6.0.0, uninstall.exe still the 6.0.0 one, baby-shell.exe still the 6.0.0 binary,
; and payload\ui\server.py still the 1549-line 6.0.0 file with no autostart route in
; it. The only things that appeared were filenames that had not existed before --
; core\autostart.py and the new hashed bundles. Every pre-existing file survived.
;
; The user's report was "the new feature is missing", and it was: the build that has
; it never landed. Nothing contradicted them, because an installer that lies about
; finishing is indistinguishable from a build that shipped without the feature.
;
; That is worse than any single missing toggle. An upgrade that silently no-ops means
; NOTHING reaches an existing user -- not the fixes, and not the cross-origin check
; that stops a web page driving their local Baby.
;
; Why it happened is still unknown; the generated installer.nsi reads correctly
; (MAINBINARYNAME is baby-shell so the running-app check does find it, there is no
; SetOverwrite off, and $INSTDIR resolves through a clean registry value). So this
; hook does not claim to fix the cause. It makes the symptom impossible to miss:
; read the version back OUT of the payload that is now on disk and compare it to the
; version this installer was built to deliver. Reading the file we just wrote is the
; only check that cannot be fooled by the copy having been skipped.
;
; It runs at the END of Section Install, after the files and the shortcuts, so a
; mismatch here means the copy did not take. MessageBox then Abort: the installer
; must land on its failure page, because "Completed" is the exact word that sent a
; user away believing they had upgraded.
;
; Registers are pushed and popped because a hook has no claim on them.

!macro NSIS_HOOK_POSTINSTALL
  Push $R4  ; file handle
  Push $R5  ; line just read
  Push $R6  ; 1 once the expected version line is seen
  Push $R7  ; length of the line we are looking for
  Push $R8  ; scratch: leading slice of $R5, then trailing character
  Push $R9  ; whatever version we DID find, for the message

  StrCpy $R6 0
  StrCpy $R9 ""
  ; The whole line, closing quote included, so "6.0.20" cannot satisfy a check for
  ; "6.0.2". Compared as a prefix so the trailing newline never matters.
  StrLen $R7 'version = "${VERSION}"'

  ClearErrors
  FileOpen $R4 "$INSTDIR\payload\pyproject.toml" r
  IfErrors baby_verify_verdict

  baby_verify_loop:
    ClearErrors
    FileRead $R4 $R5
    IfErrors baby_verify_eof
    StrCpy $R8 $R5 $R7
    StrCmp $R8 'version = "${VERSION}"' 0 baby_verify_keep_looking
    StrCpy $R6 1
    Goto baby_verify_eof
  baby_verify_keep_looking:
    ; Not our version -- but if it is A version line, remember it so the message can
    ; name what is actually installed instead of only what should have been.
    StrCpy $R8 $R5 11
    StrCmp $R8 'version = "' 0 baby_verify_loop
    StrCpy $R9 $R5
    Goto baby_verify_loop

  baby_verify_eof:
    FileClose $R4

  baby_verify_verdict:
  ${If} $R6 <> 1
    ; Trim the newline off the line we are about to show the user.
    ${Do}
      StrCpy $R8 $R9 1 -1
      ${If} $R8 == "$\r"
      ${OrIf} $R8 == "$\n"
        StrCpy $R9 $R9 -1
      ${Else}
        ${ExitDo}
      ${EndIf}
    ${Loop}
    ${If} $R9 == ""
      StrCpy $R9 "nothing"
    ${EndIf}
    MessageBox MB_ICONSTOP "Baby ${VERSION} did not install.$\r$\n$\r$\nThe files in $INSTDIR still report $R9, so the copy did not take and nothing was upgraded.$\r$\n$\r$\nQuit Baby completely from the tray icon (not just the window), then run this installer again."
    SetErrorLevel 1
    Pop $R9
    Pop $R8
    Pop $R7
    Pop $R6
    Pop $R5
    Pop $R4
    Abort "Baby ${VERSION} did not install -- the files on disk were not replaced."
  ${EndIf}

  ; --- "Start Baby when you sign in?" -------------------------------------------
  ;
  ; Asked HERE and not on the finish page because MUI gives that page exactly two
  ; checkbox slots and Tauri's template already spends both (desktop shortcut, run
  ; on finish). The four hooks it exposes all run inside sections, so none of them
  ; can draw a third control there. A real checkbox would mean forking the 1100-line
  ; template and owning it across every Tauri upgrade, which is a bigger liability
  ; than a modal is an inconvenience.
  ;
  ; Reached only after the version check above, so a half-applied install aborts
  ; before anyone is asked what it should do at logon.
  ;
  ; The value written here has to be byte-identical to the one core/autostart.py
  ; writes, or the toggle in Setup & repair and this question describe different
  ; things. tests/test_autostart.py compares the two.
  Push $R4
  Push $R5

  ; Silent and passive installs answer nothing, so they get no startup entry. A
  ; scripted deploy that adds one without being asked is worse than one that does
  ; not: nobody is at the keyboard to consent, and nobody sees the result.
  ${If} ${Silent}
    Goto baby_autostart_done
  ${EndIf}
  ${If} $PassiveMode = 1
    Goto baby_autostart_done
  ${EndIf}

  ; Already on? Then this is an upgrade of a machine that already chose. Leave it
  ; exactly as it is and say nothing -- re-asking every patch release trains people
  ; to click through, and an upgrade that silently turns the setting OFF because a
  ; default said so is the one-way-trip bug pointing the other way.
  ClearErrors
  ReadRegStr $R4 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Baby"
  ${IfNot} ${Errors}
  ${AndIf} $R4 != ""
    Goto baby_autostart_done
  ${EndIf}

  MessageBox MB_YESNO|MB_ICONQUESTION /SD IDNO "Start Baby when you sign in to Windows?$\r$\n$\r$\nBaby will be waiting in the notification area rather than opening a window. You can change this at any time in Setup & repair." IDNO baby_autostart_done

  ; Quoted, because an install path like C:\Users\Anna Maria\... is one unquoted
  ; space away from Windows trying to run C:\Users\Anna.
  StrCpy $R5 '"$INSTDIR\${MAINBINARYNAME}.exe" --minimized'
  ClearErrors
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Baby" "$R5"
  ${If} ${Errors}
    MessageBox MB_ICONEXCLAMATION "Baby could not be added to Windows startup. You can turn it on later in Setup & repair."
  ${EndIf}

  baby_autostart_done:
  Pop $R5
  Pop $R4

  Pop $R9
  Pop $R8
  Pop $R7
  Pop $R6
  Pop $R5
  Pop $R4
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  Push $R4
  Push $R5
  ; The two guards that decide whether this is a REAL uninstall are hoisted out of
  ; the data-deletion branch, because one thing has to happen on every real
  ; uninstall and not only when the user ticked a box.
  GetFullPathName $R4 "$EXEDIR"
  GetFullPathName $R5 "$INSTDIR"
  ClearErrors
  ; Path comparison FIRST, and not merely as a matter of taste: the executable
  ; test in tests/test_uninstall.py lifts everything from the GetFullPathName pair
  ; to the first ${If} and compiles it for real, so the path guard has to be that
  ; ${If}. Written the other way round the probe compiles $UpdateMode alone,
  ; which is undefined in the harness and therefore always true.
  ${If} $R4 != $R5
  ${AndIf} $UpdateMode <> 1
    SetShellVarContext current

    ; "Start Baby with Windows" writes a per-user Run value (core/autostart.py).
    ; It is NOT user data and it is not covered by the checkbox: an uninstalled
    ; Baby that still tries to launch at every logon is a broken startup entry
    ; the user has no obvious way to trace back to an app they removed. So this
    ; goes whenever the app does. Deleting a value that was never written is a
    ; no-op, so there is nothing to guard.
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Baby"

    ${If} $DeleteAppDataCheckboxState = 1
      IfFileExists "$LOCALAPPDATA\baby\.venv\*.*" baby_data_is_ours baby_data_not_ours
      baby_data_is_ours:
        RmDir /r "$LOCALAPPDATA\baby"
      baby_data_not_ours:
    ${EndIf}
  ${EndIf}
  Pop $R5
  Pop $R4
!macroend
