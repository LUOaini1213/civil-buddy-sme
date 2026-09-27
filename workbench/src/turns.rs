//! One in-flight chat turn per session. Cancel sets a flag the turn future
//! observes; dropping the turn removes the registration.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use tokio::sync::Notify;

struct Slot {
    flag: Arc<AtomicBool>,
    notify: Arc<Notify>,
}

fn map() -> &'static Mutex<HashMap<String, Slot>> {
    static MAP: OnceLock<Mutex<HashMap<String, Slot>>> = OnceLock::new();
    MAP.get_or_init(|| Mutex::new(HashMap::new()))
}

pub struct Guard {
    pub sid: String,
    pub flag: Arc<AtomicBool>,
}

impl Drop for Guard {
    fn drop(&mut self) {
        end(&self.sid, &self.flag);
    }
}

pub fn try_begin(sid: &str) -> Result<(Arc<AtomicBool>, Arc<Notify>), &'static str> {
    let flag = Arc::new(AtomicBool::new(false));
    let notify = Arc::new(Notify::new());
    let mut guard = map().lock().map_err(|_| "session lock unavailable")?;
    if guard.contains_key(sid) {
        return Err("session is already active or cancelling");
    }
    guard.insert(
            sid.to_string(),
            Slot {
                flag: flag.clone(),
                notify: notify.clone(),
            },
    );
    Ok((flag, notify))
}

pub fn end(sid: &str, flag: &Arc<AtomicBool>) {
    if let Ok(mut guard) = map().lock() {
        if guard.get(sid).is_some_and(|slot| Arc::ptr_eq(&slot.flag, flag)) {
            guard.remove(sid);
        }
    }
}

pub fn is_active(sid: &str) -> bool {
    map().lock().map(|guard| guard.contains_key(sid)).unwrap_or(false)
}

/// Returns true only when a live turn was asked to stop.
pub fn request(sid: &str) -> bool {
    let slot = map().lock().ok().and_then(|guard| {
        guard.get(sid).map(|slot| Slot {
            flag: slot.flag.clone(),
            notify: slot.notify.clone(),
        })
    });
    let Some(slot) = slot else {
        return false;
    };
    slot.flag.store(true, Ordering::SeqCst);
    slot.notify.notify_one();
    true
}

pub async fn cancelled(flag: &AtomicBool, notify: &Notify) {
    loop {
        if flag.load(Ordering::SeqCst) {
            return;
        }
        notify.notified().await;
    }
}
