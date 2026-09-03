// Baby - native desktop shell (V1: daily-driver parity).
//
// THIN CHROME over the FastAPI-served UI (DECISIONS #119): the window loads
// http://127.0.0.1:8765/ (prod) / http://localhost:5173/ (dev, ui/app Vite). It
// bundles no SPA of its own. Docker-Desktop model (DECISIONS #120): the assistant
// is normally an always-on autostart service; the shell ATTACHES to it, or SPAWNS
// one if none is running. "Quit Baby (app)" closes the window and kills only a
// backend the shell itself spawned - an attached service keeps running. No HTTP
// shutdown endpoint anywhere.
//
// Jobs, all native chrome, zero product logic:
//   - attach-or-spawn : probe :8765 (uvicorn binds only after the model loads, so a
//                       reachable port == ready); else spawn `pythonw run.py --all`,
//                       poll until ready, then navigate the splash to the backend.
//   - close-to-tray   : the window X hides; only the tray "Quit Baby (app)" exits.
//   - single-instance : a second launch focuses the existing window.
//   - native tray     : green/amber/red mirrors the backend, folded off /ws/activity
//                       (the tray the backend skips when ui.shell: native).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use tauri::image::Image;
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, WindowEvent};

/// Backend the shell attaches to / spawns. Dev proxies to it through Vite (:5173).
const BACKEND_URL: &str = "http://127.0.0.1:8765/";
const BACKEND_ADDR: &str = "127.0.0.1:8765";
const READY_TIMEOUT: Duration = Duration::from_secs(180); // startup.wait_for_model_s (120) + margin
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
/// The backend exits with this to ASK to be restarted -- it is not a crash.
///
/// The router is built once, at boot, from a config read before the first-run wizard
/// stamped anything. A wizard that validates a cloud key and upgrades the mode to
/// cloud_primary therefore leaves the RUNNING process on the local-only ladder it
/// booted with: no cloud brain, and a game-mode button wired to a provider that has
/// no such method. Both looked broken, and the only thing saying otherwise was one
/// line of text at the end of the wizard. The backend now exits with this code once
/// the wizard finishes, and we bring it straight back on the stamped mode.
///
/// Only a backend WE spawned ever sends it (it keys on BABY_SHELL_TRAY), so an
/// always-on service in --attach-only mode is never restarted out from under itself.
const RESTART_EXIT_CODE: i32 = 86;

/// Only set when the shell SPAWNED the backend itself; on quit that child is killed,
/// an attached always-on service is left running (DECISIONS #120).
struct AppState {
    spawned: Mutex<Option<Child>>,
    /// True while attach-or-spawn (incl. the minutes-long first-run venv build) is in
    /// flight, so a relaunch that re-triggers setup can't start a second concurrent run.
    starting: AtomicBool,
}

#[derive(Clone, Copy)]
enum Status {
    Ready,
    Busy,
    Confirm,
}

/// Fold of /ws/activity into a tray colour. Mirrors ui/tray.py TrayState using the
/// kinds /ws/activity actually carries (it has no turn_start/turn_end): a pending
/// confirmation wins (red), any running tool/task/project is busy (amber), else
/// ready (green). task_queued is ignored - queued is not yet running.
struct Fold {
    confirms: i32,
    busy: i32,
}

impl Fold {
    fn new() -> Self {
        Self { confirms: 0, busy: 0 }
    }

    fn apply(&mut self, kind: &str) {
        match kind {
            "confirm_request" => self.confirms += 1,
            "confirm_resolved" => self.confirms = (self.confirms - 1).max(0),
            "tool_start" | "task_started" | "project_started" => self.busy += 1,
            "tool_end" | "task_done" | "project_done" => self.busy = (self.busy - 1).max(0),
            _ => {}
        }
    }

    fn status(&self) -> Status {
        if self.confirms > 0 {
            Status::Confirm
        } else if self.busy > 0 {
            Status::Busy
        } else {
            Status::Ready
        }
    }
}

fn status_icon(status: Status) -> Image<'static> {
    let bytes: &'static [u8] = match status {
        Status::Ready => include_bytes!("../icons/status/green.png").as_slice(),
        Status::Busy => include_bytes!("../icons/status/amber.png").as_slice(),
        Status::Confirm => include_bytes!("../icons/status/red.png").as_slice(),
    };
    Image::from_bytes(bytes).expect("bundled status png is valid")
}

fn status_tooltip(status: Status) -> &'static str {
    match status {
        Status::Ready => "Baby - ready",
        Status::Busy => "Baby - working",
        Status::Confirm => "Baby - waiting for your confirmation",
    }
}

/// A reachable :8765 means the backend has bound uvicorn, which happens only after
/// the model is loaded (ui/server.py ready_check) - so this is a real readiness probe.
fn backend_up() -> bool {
    use std::net::{TcpStream, ToSocketAddrs};
    let Ok(mut addrs) = BACKEND_ADDR.to_socket_addrs() else {
        return false;
    };
    addrs.any(|addr| TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok())
}

fn wait_ready(timeout: Duration) -> bool {
    let start = std::time::Instant::now();
    while start.elapsed() < timeout {
        if backend_up() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(750));
    }
    backend_up()
}

/// Where the backend's code and writable state live. In dev these are the same
/// repo dir (run.py + .venv co-located). An installed build (v6) SPLITS them:
/// run.py + the Python source ship next to the exe (read-mostly install dir),
/// while the venv + config.yaml/.env/baby.db live in a per-user writable data
/// home (`%LOCALAPPDATA%\baby`, matched by the backend's own `core/paths.py`).
struct Layout {
    /// Holds run.py + the Python source + shipped assets; the process cwd.
    code_dir: PathBuf,
    /// BABY_HOME: the venv + config/db. Equals code_dir in dev.
    data_home: PathBuf,
}

impl Layout {
    fn installed(&self) -> bool {
        self.code_dir != self.data_home
    }
}

fn localappdata_baby() -> Option<PathBuf> {
    std::env::var("LOCALAPPDATA")
        .ok()
        .map(|p| PathBuf::from(p).join("baby"))
}

/// Resolve the code + data layout. Dev (repo, run.py + .venv co-located) is
/// detected first and behaves exactly as before. An installed shell finds the
/// staged backend under the Tauri resource dir (`payload/`) and points the
/// venv/state at `%LOCALAPPDATA%\baby`. Returns None only when no run.py exists.
fn resolve_layout(app: &AppHandle) -> Option<Layout> {
    // Explicit override: BABY_HOME pointing at a co-located run.py (advanced/dev).
    if let Ok(home) = std::env::var("BABY_HOME") {
        let p = PathBuf::from(home);
        if p.join("run.py").is_file() {
            return Some(Layout {
                code_dir: p.clone(),
                data_home: p,
            });
        }
    }
    // Dev: walk up from the exe for a dir with run.py + .venv co-located.
    let mut dir = std::env::current_exe().ok()?;
    while dir.pop() {
        if dir.join("run.py").is_file() && dir.join(".venv").is_dir() {
            return Some(Layout {
                code_dir: dir.clone(),
                data_home: dir,
            });
        }
    }
    // Installed (release builds only): the backend is staged under the bundle's
    // resource dir (payload/, via tauri.conf bundle.resources). Tauri tells us where
    // that is, so we never guess exe-relative paths; state lives in %LOCALAPPDATA%\baby.
    // Gated out of debug builds: in `tauri dev` the resource dir also holds a staged
    // payload/, so without this a contributor whose interpreter isn't a repo-root
    // .venv would fall through here and get the installed layout (misleading splash,
    // or cross-wiring an installed venv) instead of the honest dev "not found" message.
    if !cfg!(debug_assertions) {
        if let Ok(res) = app.path().resource_dir() {
            let code = res.join("payload");
            if code.join("run.py").is_file() {
                let data_home = localappdata_baby()?;
                return Some(Layout {
                    code_dir: code,
                    data_home,
                });
            }
        }
    }
    None
}

/// The last `ERROR:`-tagged line from the first-run script, for the splash. The
/// bootstrap prints one classified line on failure (never a raw trace).
fn last_error_line(stdout: &[u8], stderr: &[u8]) -> String {
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(stdout),
        String::from_utf8_lossy(stderr)
    );
    text.lines()
        .rev()
        .find(|l| l.trim_start().starts_with("ERROR:"))
        .map(|l| l.trim_start().trim_start_matches("ERROR:").trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "setup did not complete".to_string())
}

/// First launch of an INSTALLED build has no venv yet (the installer stays small;
/// the backend is stood up here). Run the bundled-uv bootstrap (installer/
/// first_run.ps1): managed CPython + `uv sync` into the per-user venv + a functional
/// wheels probe. Blocking on this (attach-or-spawn) thread while a splash shows;
/// resumable on the next launch. Returns true when a runnable venv exists.
fn ensure_venv(app: &AppHandle, layout: &Layout) -> bool {
    // Gate on the completion sentinel first_run.ps1 writes AFTER its wheels probe --
    // NOT on pythonw.exe, which `uv sync` creates before installing the ~1.5 GB of
    // deps. Keying on pythonw would leave an interrupted first sync looking "done"
    // and never resume it. Sentinel absent → (re-)run the bootstrap; it's resumable.
    let ready = layout.data_home.join(".venv").join(".baby-ready");
    if ready.is_file() {
        return true; // a prior first-run completed and passed the wheels probe
    }
    if !layout.installed() {
        return true; // dev: a repo without a venv is handled by spawn_backend's fallback
    }
    let script = layout.code_dir.join("installer").join("first_run.ps1");
    let uv = layout.code_dir.join("uv.exe");
    // A release built without the bundled uv.exe (BABY_UV_EXE unset) can't bootstrap;
    // say so plainly rather than letting first_run.ps1 fail on a missing `uv`.
    if !script.is_file() || !uv.is_file() {
        show_splash_message(app, "First-run setup files are missing. Please reinstall Baby.");
        return false;
    }
    show_splash_message(
        app,
        "Setting up Baby - installing the local engine. This runs once and can take a few minutes...",
    );
    let output = Command::new("powershell")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
        .arg(&script)
        .arg("-UvExe")
        .arg(&uv)
        .arg("-SourceDir")
        .arg(&layout.code_dir)
        .arg("-BabyHome")
        .arg(&layout.data_home)
        .creation_flags(CREATE_NO_WINDOW)
        .output();
    match output {
        Ok(out) if out.status.success() => true,
        Ok(out) => {
            let msg = last_error_line(&out.stdout, &out.stderr);
            show_splash_message(app, &format!("Baby couldn't finish setup: {msg}"));
            false
        }
        Err(e) => {
            show_splash_message(app, &format!("Baby couldn't run first-run setup: {e}"));
            false
        }
    }
}

/// Spawn `pythonw run.py --all` detached (no console window), recording the child so
/// quit can kill it. Python comes from the data-home venv; the script + cwd come from
/// the code dir; an installed layout also exports BABY_HOME so the backend resolves
/// config/db into the per-user data home.
fn spawn_backend(app: &AppHandle, layout: &Layout) {
    let venv_pythonw = layout
        .data_home
        .join(".venv")
        .join("Scripts")
        .join("pythonw.exe");
    let exe = if venv_pythonw.is_file() {
        venv_pythonw
    } else if layout.installed() {
        // Installed but the venv isn't built yet: first-run setup (W3) hasn't
        // finished. Don't fall back to a system python that lacks Baby's deps.
        show_splash_message(
            app,
            "Baby is still finishing first-run setup. Reopen it once setup completes.",
        );
        return;
    } else {
        PathBuf::from("pythonw") // dev fallback: repo without a local venv
    };
    let mut cmd = Command::new(exe);
    cmd.arg("run.py")
        .arg("--all")
        .current_dir(&layout.code_dir)
        // Tell the backend the native shell owns the tray, so it skips its pystray icon
        // even when ui.shell isn't set to native (avoids a double tray). Only affects a
        // backend WE spawn; an attached always-on service relies on ui.shell instead.
        .env("BABY_SHELL_TRAY", "1")
        .creation_flags(CREATE_NO_WINDOW);
    // Only export BABY_HOME when the layout actually splits (installed). In dev the
    // two dirs are identical, so leaving it unset keeps the cwd-relative behavior
    // byte-identical to before.
    if layout.installed() {
        cmd.env("BABY_HOME", &layout.data_home);
    }
    match cmd.spawn() {
        Ok(child) => {
            *app.state::<AppState>().spawned.lock().unwrap() = Some(child);
            watch_backend(app.clone());
        }
        Err(e) => show_splash_message(app, &format!("Failed to start Baby backend: {e}")),
    }
}

/// Notice when the backend exits on its own, and say so.
///
/// attach_or_spawn runs ONCE, at startup. Nothing re-probes :8765 afterwards, so a
/// backend that died mid-run left the window rendering whatever it had last received,
/// with no error and no end condition -- a first-run wizard sat on "Memory embedder
/// ... working" for three hours after the process was already gone. Polling the child
/// handle is enough to turn that into a message.
///
/// Quit takes the handle out of the mutex before killing it, so an empty slot means
/// "we are shutting down, or this was never ours" -- the watcher stops rather than
/// reporting a deliberate kill as a crash.
fn watch_backend(app: AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_secs(2));
        let exited = {
            let state = app.state::<AppState>();
            let mut guard = state.spawned.lock().unwrap();
            match guard.as_mut() {
                None => return,
                Some(child) => match child.try_wait() {
                    Ok(Some(status)) => status.code(),
                    Ok(None) => continue,
                    Err(_) => return,
                },
            }
        };
        // Drop the reaped handle so quit doesn't try to kill a dead pid.
        *app.state::<AppState>().spawned.lock().unwrap() = None;
        if exited == Some(RESTART_EXIT_CODE) {
            restart_backend(&app);
        } else {
            show_backend_died(&app, exited);
        }
        return;
    });
}

/// Bring the backend back after it asked to be restarted (RESTART_EXIT_CODE).
///
/// Runs on the watcher thread, which is already off the UI thread, so blocking on
/// readiness here is fine. spawn_backend arms a fresh watcher, so a crash on the way
/// back up still reports itself. The venv is not rebuilt: nothing can reach this
/// point without one.
fn restart_backend(app: &AppHandle) {
    show_overlay(
        app,
        "baby-restarting",
        "#e4e4e7",
        "Applying your setup — Baby is restarting. This takes a few seconds.",
    );
    let Some(layout) = resolve_layout(app) else {
        show_backend_died(app, Some(RESTART_EXIT_CODE));
        return;
    };
    spawn_backend(app, &layout);
    if wait_ready(READY_TIMEOUT) {
        reveal(app);
    } else {
        show_backend_died(app, None);
    }
}

/// Overlay the live page with the bad news. Not show_splash_message: that writes into
/// the splash's `.wrap`, which no longer exists once the window has navigated to the
/// backend-served UI -- exactly the case this fires in.
fn show_backend_died(app: &AppHandle, code: Option<i32>) {
    let how = match code {
        Some(c) => format!("exit code {c}"),
        None => "it was terminated".to_string(),
    };
    let msg = format!(
        "Baby's backend stopped ({how}). Nothing on this screen is live any more. \
         Close Baby and open it again — setup resumes from whatever already finished. \
         Details are in %LOCALAPPDATA%\\baby\\logs\\baby.log"
    );
    show_overlay(app, "baby-backend-died", "#f87171", &msg);
}

/// Cover the live page with one line of text.
///
/// Not show_splash_message: that writes into the splash's `.wrap`, which no longer
/// exists once the window has navigated to the backend-served UI — exactly the case
/// these fire in. `id` keeps a repeat call from stacking overlays.
fn show_overlay(app: &AppHandle, id: &str, colour: &str, msg: &str) {
    if let Some(w) = app.get_webview_window("main") {
        let safe = msg.replace('\\', "\\\\").replace('\'', "\\'");
        // textContent, not innerHTML: the message can carry a path, and nothing here
        // should ever be parsed as markup.
        let js = format!(
            "(function(){{if(document.getElementById('{id}'))return;\
             var d=document.createElement('div');d.id='{id}';\
             d.setAttribute('style','position:fixed;inset:0;z-index:2147483647;\
             display:flex;align-items:center;justify-content:center;text-align:center;\
             padding:2rem;background:rgba(9,9,11,0.94);color:{colour};\
             font:14px system-ui,sans-serif;line-height:1.6');\
             d.textContent='{safe}';\
             (document.body||document.documentElement).appendChild(d);}})();"
        );
        let _ = w.eval(&js);
        let _ = w.show();
    }
}

/// True when launched by the autostart "Baby Shell" task, which passes --attach-only
/// alongside the always-on backend service. In that mode the shell NEVER spawns — it
/// waits for the service to bind — so the two logon tasks can't race two backends and
/// the always-on service always persists (DECISIONS #120, #122).
fn attach_only() -> bool {
    std::env::args().any(|a| a == "--attach-only")
}

/// Reveal the real UI once the backend is ready. Dev already renders the live SPA via
/// Vite (:5173, which proxies to :8765); only prod leaves the splash for the
/// FastAPI-served UI.
fn reveal(app: &AppHandle) {
    if cfg!(debug_assertions) {
        show_main(app);
    } else {
        navigate_to_backend(app);
    }
}

/// Guarded entry: a relaunch can re-trigger setup (single-instance callback), but the
/// first-run venv build takes minutes — never run two concurrently. The `starting`
/// flag serializes it; a second entry just focuses the window.
fn attach_or_spawn(app: AppHandle) {
    if app.state::<AppState>().starting.swap(true, Ordering::SeqCst) {
        show_main(&app);
        return;
    }
    attach_or_spawn_inner(&app);
    app.state::<AppState>().starting.store(false, Ordering::SeqCst);
}

fn attach_or_spawn_inner(app: &AppHandle) {
    // Already up (an autostart/manual service is running) → just attach.
    if backend_up() {
        reveal(app);
        return;
    }
    // Launched by autostart next to the always-on service: the service binds :8765
    // only after its model loads, so the port is down at logon. WAIT for it — never
    // spawn a duplicate that would race the bind and (if it won) let quit kill the
    // real service. This is what keeps #120's "the service persists" guarantee true.
    if attach_only() {
        if wait_ready(READY_TIMEOUT) {
            reveal(app);
        } else {
            show_splash_message(
                app,
                "Baby service did not come up. Check %LOCALAPPDATA%\\baby\\logs\\baby.log",
            );
        }
        return;
    }
    // Manual/dev launch with nothing listening → build the venv on first run if
    // needed, then spawn our own backend.
    match resolve_layout(app) {
        Some(layout) => {
            if !ensure_venv(app, &layout) {
                return; // first-run setup failed; the splash carries the reason
            }
            spawn_backend(app, &layout)
        }
        None => {
            show_splash_message(
                app,
                "Baby backend not found. Start it in the repo: uv run python run.py --all",
            );
            return;
        }
    }
    if wait_ready(READY_TIMEOUT) {
        reveal(app);
    } else {
        show_splash_message(
            app,
            "Baby backend did not become ready. Start it: uv run python run.py --all",
        );
    }
}

/// Navigate the window to the FastAPI-served UI. A per-navigation cache-buster on
/// the (tiny) index.html defeats WebView2's shared-profile HTTP cache, which can
/// otherwise serve a stale SPA entry across launches after a rebuild; the
/// content-hashed assets it references still cache correctly. useDeepLink only reads
/// location.hash and preserves location.search, so the `?r=` is inert.
fn navigate_to_backend(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let bust = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or(0);
        if let Ok(url) = format!("{BACKEND_URL}?r={bust}").parse() {
            let _ = w.navigate(url);
        }
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.set_focus();
    }
}

/// Force the webview to pull the current frontend. The shell navigates to the
/// FastAPI-served UI once at startup and never on its own, so after a rebuild
/// (`npm run build`) the window keeps the OLD bundle while a browser tab, refreshed,
/// shows the new one — "works in the browser, not the app". A prod reload re-fetches
/// with a cache-buster so WebView2 cannot serve a heuristically-cached index.html
/// (the content-hashed assets then cache correctly); dev just reloads Vite.
fn reload_ui(app: &AppHandle) {
    if cfg!(debug_assertions) {
        // Dev serves Vite (:5173) directly — a plain reload picks up HMR output.
        if let Some(w) = app.get_webview_window("main") {
            let _ = w.eval("location.reload()");
            let _ = w.show();
            let _ = w.set_focus();
        }
    } else {
        navigate_to_backend(app); // fresh, cache-busted fetch of the prod UI
    }
}

/// Replace the splash text (used when attach-or-spawn cannot reach a backend).
fn show_splash_message(app: &AppHandle, msg: &str) {
    if let Some(w) = app.get_webview_window("main") {
        let safe = msg.replace('\\', "\\\\").replace('\'', "\\'");
        let js = format!(
            "(function(){{var e=document.querySelector('.wrap');if(e){{e.innerHTML=\"<div style='color:#f87171;max-width:32rem'>{safe}</div>\";}}}})();"
        );
        let _ = w.eval(&js);
        let _ = w.show();
    }
}

fn set_tray(app: &AppHandle, status: Status) {
    if let Some(tray) = app.tray_by_id("main") {
        let _ = tray.set_icon(Some(status_icon(status)));
        let _ = tray.set_tooltip(Some(status_tooltip(status)));
    }
}

/// Background reconnect loop: fold /ws/activity into the tray colour. Blocking
/// tungstenite on its own thread; on any drop, reset to ready and reconnect.
fn run_activity_tray(app: AppHandle) {
    let url = format!("ws://{BACKEND_ADDR}/ws/activity");
    loop {
        if let Ok((mut socket, _)) = tungstenite::connect(&url) {
            let mut fold = Fold::new();
            set_tray(&app, fold.status());
            loop {
                match socket.read() {
                    Ok(tungstenite::Message::Text(txt)) => {
                        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&txt) {
                            if let Some(kind) = v.get("type").and_then(|x| x.as_str()) {
                                fold.apply(kind);
                                set_tray(&app, fold.status());
                            }
                        }
                    }
                    Ok(tungstenite::Message::Close(_)) | Err(_) => break,
                    Ok(_) => {}
                }
            }
        }
        std::thread::sleep(Duration::from_secs(2));
    }
}

fn build_tray(app: &tauri::App) -> tauri::Result<()> {
    let open = MenuItemBuilder::with_id("open", "Open Baby").build(app)?;
    let reload = MenuItemBuilder::with_id("reload", "Reload UI").build(app)?;
    let quit = MenuItemBuilder::with_id("quit", "Quit Baby (app)").build(app)?;
    let menu = MenuBuilder::new(app).items(&[&open, &reload, &quit]).build()?;
    TrayIconBuilder::with_id("main")
        .icon(status_icon(Status::Ready))
        .tooltip(status_tooltip(Status::Ready))
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_main(app),
            "reload" => reload_ui(app),
            "quit" => {
                // Kill only a backend WE spawned; an attached service persists (#120).
                if let Some(mut child) = app.state::<AppState>().spawned.lock().unwrap().take() {
                    let _ = child.kill();
                }
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // Reopening while first-run setup hasn't finished RESUMES it — the
            // failed-setup splash tells the user to reopen Baby, so honor that (the
            // starting-guard keeps it from racing an in-flight run). Backend already
            // up → just focus.
            if backend_up() {
                show_main(app);
            } else {
                let h = app.clone();
                std::thread::spawn(move || attach_or_spawn(h));
            }
        }))
        .manage(AppState {
            spawned: Mutex::new(None),
            starting: AtomicBool::new(false),
        })
        .setup(|app| {
            build_tray(app)?;

            // Close-to-tray: the window X hides instead of quitting; only the tray
            // "Quit Baby (app)" exits (DECISIONS #120).
            if let Some(win) = app.get_webview_window("main") {
                let hide_target = win.clone();
                win.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = hide_target.hide();
                    }
                });
            }

            // Attach-or-spawn the backend, then reveal the real UI. Tray status
            // follows /ws/activity. Both run off-thread so the window paints the
            // splash immediately.
            let h1 = app.handle().clone();
            std::thread::spawn(move || attach_or_spawn(h1));
            let h2 = app.handle().clone();
            std::thread::spawn(move || run_activity_tray(h2));
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running the Baby shell");
}
