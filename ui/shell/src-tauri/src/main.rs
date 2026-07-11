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
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
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

/// Only set when the shell SPAWNED the backend itself; on quit that child is killed,
/// an attached always-on service is left running (DECISIONS #120).
struct AppState {
    spawned: Mutex<Option<Child>>,
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

/// Find the repo so the shell can spawn `run.py`. BABY_HOME wins; otherwise walk up
/// from the exe looking for run.py + .venv (true in dev, where the exe lives under
/// ui/shell/src-tauri/target/). An installed shell with no repo returns None and the
/// shell shows a "start the backend" message instead of guessing.
fn resolve_baby_home() -> Option<PathBuf> {
    if let Ok(home) = std::env::var("BABY_HOME") {
        let p = PathBuf::from(home);
        if p.join("run.py").is_file() {
            return Some(p);
        }
    }
    let mut dir = std::env::current_exe().ok()?;
    while dir.pop() {
        if dir.join("run.py").is_file() && dir.join(".venv").is_dir() {
            return Some(dir);
        }
    }
    None
}

/// Spawn `pythonw run.py --all` detached (no console window), recording the child so
/// quit can kill it. Prefers the repo venv's pythonw, falls back to PATH.
fn spawn_backend(app: &AppHandle, home: &Path) {
    let venv_pythonw = home.join(".venv").join("Scripts").join("pythonw.exe");
    let exe = if venv_pythonw.is_file() {
        venv_pythonw
    } else {
        PathBuf::from("pythonw")
    };
    let mut cmd = Command::new(exe);
    cmd.arg("run.py")
        .arg("--all")
        .current_dir(home)
        // Tell the backend the native shell owns the tray, so it skips its pystray icon
        // even when ui.shell isn't set to native (avoids a double tray). Only affects a
        // backend WE spawn; an attached always-on service relies on ui.shell instead.
        .env("BABY_SHELL_TRAY", "1")
        .creation_flags(CREATE_NO_WINDOW);
    match cmd.spawn() {
        Ok(child) => {
            *app.state::<AppState>().spawned.lock().unwrap() = Some(child);
        }
        Err(e) => show_splash_message(app, &format!("Failed to start Baby backend: {e}")),
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

fn attach_or_spawn(app: AppHandle) {
    // Already up (an autostart/manual service is running) → just attach.
    if backend_up() {
        reveal(&app);
        return;
    }
    // Launched by autostart next to the always-on service: the service binds :8765
    // only after its model loads, so the port is down at logon. WAIT for it — never
    // spawn a duplicate that would race the bind and (if it won) let quit kill the
    // real service. This is what keeps #120's "the service persists" guarantee true.
    if attach_only() {
        if wait_ready(READY_TIMEOUT) {
            reveal(&app);
        } else {
            show_splash_message(
                &app,
                "Baby service did not come up. Check %LOCALAPPDATA%\\baby\\logs\\baby.log",
            );
        }
        return;
    }
    // Manual/dev launch with nothing listening → spawn our own backend.
    match resolve_baby_home() {
        Some(home) => spawn_backend(&app, &home),
        None => {
            show_splash_message(
                &app,
                "Baby backend not found. Start it in the repo: uv run python run.py --all",
            );
            return;
        }
    }
    if wait_ready(READY_TIMEOUT) {
        reveal(&app);
    } else {
        show_splash_message(
            &app,
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
            show_main(app);
        }))
        .manage(AppState {
            spawned: Mutex::new(None),
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
