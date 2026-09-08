use std::collections::VecDeque;
use std::sync::Mutex;

use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

struct EngineProcess(Mutex<Option<CommandChild>>);

const ENGINE_PORT: &str = "8765";
const READY_MARKER: &str = "[INBOUND_SERVER_READY]";

#[derive(Clone, serde::Serialize)]
struct EngineFailure {
    exit_code: Option<i32>,
    error_summary: String,
    log_path: String,
    is_dll_error: bool,
}

fn apply_linux_webkit_workarounds() {
    #[cfg(target_os = "linux")]
    {
        // Intel Iris Xe + WebKitGTK 2.42+/2.52: DMABUF/EGL compositing tears
        // frames and can take down WebKitWebProcess. Set only when unset so
        // operators can override from the environment.
        for (key, value) in [
            ("WEBKIT_DISABLE_DMABUF_RENDERER", "1"),
            ("WEBKIT_DISABLE_COMPOSITING_MODE", "1"),
        ] {
            if std::env::var_os(key).is_none() {
                #[allow(unused_unsafe)]
                unsafe {
                    std::env::set_var(key, value);
                }
            }
        }
    }
}

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

fn engine_log_path(app: &tauri::AppHandle) -> std::path::PathBuf {
    let dir = app
        .path()
        .app_log_dir()
        .or_else(|_| app.path().app_data_dir())
        .unwrap_or_else(|_| std::env::temp_dir().join("inbound-surveillance"));
    let _ = std::fs::create_dir_all(&dir);
    dir.join("engine.log")
}

fn append_to_engine_log(log_path: &std::path::Path, text: &str) {
    use std::io::Write;
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)
    {
        let _ = writeln!(file, "{text}");
    }
}

fn check_is_dll_error(text: &str, exit_code: Option<i32>) -> bool {
    if let Some(code) = exit_code {
        if code == -1073741515 || code == 3221225781u32 as i32 {
            return true;
        }
    }
    let lower = text.to_lowercase();
    lower.contains("dll load failed")
        || lower.contains("specified module could not be found")
        || lower.contains("vcruntime")
        || lower.contains("msvcp")
        || lower.contains("c_extension")
        || lower.contains("torch_python")
        || lower.contains("fbgemm.dll")
        || lower.contains("import _c")
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

fn notify_engine_failed(app: &tauri::AppHandle, failure: EngineFailure) {
    let _ = app.emit("engine-failed", &failure);
    if let Some(window) = app.get_webview_window("main") {
        if let Ok(json) = serde_json::to_string(&failure) {
            let escaped = json.replace('\\', "\\\\").replace('\'', "\\'");
            let script = format!(
                "window.dispatchEvent(new CustomEvent('inbound-engine-failed', {{ detail: JSON.parse('{escaped}') }}));\
                 if (window.__onEngineFailed) {{ window.__onEngineFailed(JSON.parse('{escaped}')); }}"
            );
            let _ = window.eval(&script);
        }
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
    let log_file = engine_log_path(&app);
    let log_path_str = log_file.to_string_lossy().to_string();

    let command = match app.shell().sidecar("inbound-engine") {
        Ok(cmd) => cmd.args(["--port", ENGINE_PORT, "--no-browser"]),
        Err(err) => {
            log::warn!(
                "inbound-engine sidecar is not available ({err}); \
                 start `python edge/launcher.py --no-browser` for local development"
            );
            if cfg!(debug_assertions) {
                notify_engine_ready(&app, 8765);
            } else {
                let failure = EngineFailure {
                    exit_code: None,
                    error_summary: format!("Sidecar executable not found: {err}"),
                    log_path: log_path_str,
                    is_dll_error: false,
                };
                notify_engine_failed(&app, failure);
            }
            return;
        }
    };

    match command.spawn() {
        Ok((mut rx, child)) => {
            if let Ok(mut guard) = app.state::<EngineProcess>().0.lock() {
                *guard = Some(child);
            }
            let log_file_cloned = log_file.clone();
            let log_path_str_cloned = log_path_str.clone();

            tauri::async_runtime::spawn(async move {
                let mut stdout_buf = String::new();
                let mut notified = false;
                let mut recent_lines: VecDeque<String> = VecDeque::with_capacity(60);

                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(bytes) => {
                            let chunk = String::from_utf8_lossy(&bytes);
                            let trimmed = chunk.trim();
                            if !trimmed.is_empty() {
                                log::info!("[engine] {trimmed}");
                                for line in trimmed.lines() {
                                    if recent_lines.len() >= 50 {
                                        recent_lines.pop_front();
                                    }
                                    recent_lines.push_back(line.to_string());
                                    append_to_engine_log(&log_file_cloned, line);
                                }
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
                                log::warn!("[engine:err] {trimmed}");
                                for line in trimmed.lines() {
                                    if recent_lines.len() >= 50 {
                                        recent_lines.pop_front();
                                    }
                                    recent_lines.push_back(line.to_string());
                                    append_to_engine_log(&log_file_cloned, &format!("[ERR] {line}"));
                                }
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
                            if !notified {
                                let exit_code = payload.code;
                                let summary = if !recent_lines.is_empty() {
                                    recent_lines.iter().cloned().collect::<Vec<_>>().join("\n")
                                } else {
                                    format!("Process exited prematurely with exit code {exit_code:?}")
                                };
                                let is_dll = check_is_dll_error(&summary, exit_code);
                                let failure = EngineFailure {
                                    exit_code,
                                    error_summary: summary,
                                    log_path: log_path_str_cloned.clone(),
                                    is_dll_error: is_dll,
                                };
                                notify_engine_failed(&app, failure);
                            }
                            break;
                        }
                        CommandEvent::Error(message) => {
                            log::error!("inbound-engine error: {message}");
                            append_to_engine_log(&log_file_cloned, &format!("[FATAL] {message}"));
                            if !notified {
                                let failure = EngineFailure {
                                    exit_code: None,
                                    error_summary: format!("Engine error: {message}"),
                                    log_path: log_path_str_cloned.clone(),
                                    is_dll_error: false,
                                };
                                notify_engine_failed(&app, failure);
                            }
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
            if cfg!(debug_assertions) {
                notify_engine_ready(&app, 8765);
            } else {
                let failure = EngineFailure {
                    exit_code: None,
                    error_summary: format!("Failed to spawn engine: {err}"),
                    log_path: log_path_str,
                    is_dll_error: false,
                };
                notify_engine_failed(&app, failure);
            }
        }
    }
}

#[tauri::command]
fn retry_engine(app: tauri::AppHandle) {
    kill_engine(&app);
    spawn_engine(app);
}

#[tauri::command]
fn open_engine_log(app: tauri::AppHandle) -> Result<(), String> {
    let path = engine_log_path(&app);
    if let Some(parent) = path.parent() {
        #[cfg(target_os = "windows")]
        {
            let _ = std::process::Command::new("explorer").arg(parent).spawn();
        }
        #[cfg(target_os = "macos")]
        {
            let _ = std::process::Command::new("open").arg(parent).spawn();
        }
        #[cfg(target_os = "linux")]
        {
            let _ = std::process::Command::new("xdg-open").arg(parent).spawn();
        }
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    apply_linux_webkit_workarounds();
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(EngineProcess(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![retry_engine, open_engine_log])
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

