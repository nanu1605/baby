// Baby - native desktop shell (V0 scaffold).
//
// THIN CHROME over the FastAPI-served UI (DECISIONS #119): the shipped shell loads
// http://127.0.0.1:8765/ (prod) / http://localhost:5173/ (dev) - the same ui/app
// build the browser uses. It bundles no SPA of its own; in prod it opens a local
// splash (../placeholder) and V1's probe_backend navigates the window to the live
// backend once it is healthy.
//
// V0 = compiles + opens an empty native window + declares every V1 lifecycle seam
// as a stub. No product or backend behavior is wired yet. V1 fills the stubs:
//   - attach-or-spawn        (probe_backend / spawn_backend)
//   - close-to-tray + tray   "Quit Baby (app)" (DECISIONS #120)
//   - navigate the window    from the splash to the backend once healthy
// Only single-instance is live here - pure shell chrome, zero product logic.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Manager;

/// Prod backend the shell attaches to / spawns. Dev uses :5173 (ui/app Vite).
const BACKEND_URL: &str = "http://127.0.0.1:8765/";

/// TODO(V1): TCP-connect 127.0.0.1:8765, then GET /stats until 200. There is no
/// health endpoint - readiness == /stats responding (see ui/server.py). Honor
/// startup.wait_for_model_s. Returns whether a live backend was found to attach to.
#[allow(dead_code)]
fn probe_backend() -> bool {
    // V1 wires this. V0 declares the seam only.
    false
}

/// TODO(V1): when probe_backend() is false, spawn `pythonw run.py --all` (detached)
/// and record that WE spawned it, then poll probe_backend() until healthy. A shell-
/// spawned backend is killed on "Quit Baby (app)"; an attached always-on service is
/// NOT (DECISIONS #120). Spawn is Rust-side std::process::Command - no shell plugin,
/// no capability exposed to the webview, no HTTP shutdown endpoint.
#[allow(dead_code)]
fn spawn_backend() {
    // V1 wires this. V0 declares the seam only.
}

/// TODO(V1): build the native tray, reconciling with the backend's pystray tray
/// (ui/tray.py) via the additive `ui.shell` read at ui/server.py - when
/// ui.shell: native the backend skips its own tray and the shell owns it, same
/// green/amber/red semantics off /ws/activity. Menu: "Open" + "Quit Baby (app)"
/// (closes the window / kills only a shell-spawned backend; the always-on service
/// persists - DECISIONS #120). Adds the tauri "tray-icon" feature when wired.
#[allow(dead_code)]
fn setup_tray() {
    // V1 wires this. V0 declares the seam only.
}

fn main() {
    tauri::Builder::default()
        // Single-instance: a second launch focuses the existing window instead of
        // opening a second one. The backend's only guard today is the uvicorn port
        // bind; the shell needs its own. Pure shell chrome, no product logic.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.set_focus();
            }
        }))
        .setup(|_app| {
            // V1: probe_backend() -> spawn_backend() if needed -> navigate "main"
            // from the splash to BACKEND_URL; then setup_tray(). V0 opens the splash.
            let _ = BACKEND_URL;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running the Baby shell");
}
