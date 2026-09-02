use std::sync::Mutex;

use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

struct EngineProcess(Mutex<Option<CommandChild>>);

const ENGINE_PORT: &str = "8765";
const READY_MARKER: &str = "[INBOUND_SERVER_READY]";

fn parse_ready_port(line: &str) -> Option<u16> {
    let idx = line.find(READY_MARKER)?;
    let rest = line[idx + READY_MARKER.len()..].trim();
    for part in rest.split_whitespace() {
        if let Some(value) = part.strip_prefix("port=") {
            return value.parse().ok();
        }
    }
    None
}

fn notify_engine_ready(app: &tauri::AppHandle, port: u16) {
    let _ = app.emit("engine-ready", port);
    if let Some(window) = app.get_webview_window("main") {
        let script = format!(
            "window.__INBOUND_ENGINE_PORT__={port};\
             window.dispatchEvent(new CustomEvent('inbound-engine-ready',{{detail:{port}}}));\
             window.location.replace('http://127.0.0.1:{port}/');"
        );
        let _ = window.eval(&script);
    }
}

fn kill_engine(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<EngineProcess>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(child) = guard.take() {
                log::info!("stopping inbound-engine sidecar");
                let _ = child.kill();
            }
        }
    }
}

fn spawn_engine(app: tauri::AppHandle) {
    let command = match app.shell().sidecar("inbound-engine") {
        Ok(cmd) => cmd.args(["--port", ENGINE_PORT, "--no-browser"]),
        Err(err) => {
            log::warn!(
                "inbound-engine sidecar is not available ({err}); \
                 start `python edge/launcher.py --no-browser` for local development"
            );
            notify_engine_ready(&app, 8765);
            return;
        }
    };

    match command.spawn() {
        Ok((mut rx, child)) => {
            if let Ok(mut guard) = app.state::<EngineProcess>().0.lock() {
                *guard = Some(child);
            }
            tauri::async_runtime::spawn(async move {
                let mut stdout_buf = String::new();
                let mut notified = false;
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(bytes) => {
                            let chunk = String::from_utf8_lossy(&bytes);
                            let trimmed = chunk.trim();
                            if !trimmed.is_empty() {
                                log::info!("[engine] {trimmed}");
                            }
                            stdout_buf.push_str(&chunk);
                            stdout_buf.push('\n');
                            if !notified {
                                if let Some(port) = parse_ready_port(&stdout_buf) {
                                    notified = true;
                                    notify_engine_ready(&app, port);
                                }
                            }
                        }
                        CommandEvent::Stderr(bytes) => {
                            let chunk = String::from_utf8_lossy(&bytes);
                            let trimmed = chunk.trim();
                            if !trimmed.is_empty() {
                                log::info!("[engine:err] {trimmed}");
                            }
                            if !notified {
                                if let Some(port) = parse_ready_port(trimmed) {
                                    notified = true;
                                    notify_engine_ready(&app, port);
                                }
                            }
                        }
                        CommandEvent::Terminated(payload) => {
                            log::info!("inbound-engine exited: {payload:?}");
                            break;
                        }
                        CommandEvent::Error(message) => {
                            log::error!("inbound-engine error: {message}");
                        }
                        _ => {}
                    }
                }
            });
        }
        Err(err) => {
            log::error!(
                "failed to spawn inbound-engine ({err}); \
                 start `python edge/launcher.py --no-browser` for local development"
            );
            notify_engine_ready(&app, 8765);
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(EngineProcess(Mutex::new(None)))
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            if let Some(window) = app.get_webview_window("main") {
                let icon = tauri::include_image!("icons/128x128.png");
                let _ = window.set_icon(icon);
            }
            spawn_engine(app.handle().clone());
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(
                event,
                tauri::WindowEvent::Destroyed | tauri::WindowEvent::CloseRequested { .. }
            ) {
                kill_engine(window.app_handle());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(|app, event| {
            if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
                kill_engine(app);
            }
        });
}
