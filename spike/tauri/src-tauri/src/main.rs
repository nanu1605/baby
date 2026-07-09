// V0 shell spike — Tauri main. Thin shell glue only; the scene + measurement are
// byte-identical shared TS in ../../common. Two commands back the shared
// window.spikeAPI seam: cold_start_shell_ms and save_result. (Screenshot is a
// manual eyeball for the bloom verdict — see spikeApiTauri.ts.)

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;
use std::time::Instant;
use tauri::{Manager, State};

struct Timing {
    start: Instant,
    cold_shell_ms: Mutex<Option<u128>>,
}

#[tauri::command]
fn cold_start_shell_ms(state: State<Timing>) -> Option<u128> {
    *state.cold_shell_ms.lock().unwrap()
}

#[tauri::command]
fn save_result(result: serde_json::Value) -> Result<String, String> {
    let dir = std::env::current_dir().map_err(|e| e.to_string())?;
    let path = dir.join("result.json");
    let text = serde_json::to_string_pretty(&result).map_err(|e| e.to_string())?;
    std::fs::write(&path, &text).map_err(|e| e.to_string())?;
    // Also echo to stdout so the owner can grab it even if cwd is unexpected.
    println!("[spike] result.json written to {}", path.display());
    println!("{text}");
    Ok(path.display().to_string())
}

fn main() {
    tauri::Builder::default()
        .manage(Timing {
            start: Instant::now(),
            cold_shell_ms: Mutex::new(None),
        })
        .setup(|app| {
            // setup runs ~when the window is created → process-start → window ms.
            let state: State<Timing> = app.state();
            let ms = state.start.elapsed().as_millis();
            *state.cold_shell_ms.lock().unwrap() = Some(ms);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![cold_start_shell_ms, save_result])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
