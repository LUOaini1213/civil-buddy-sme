//! One in-flight chat turn per session. Cancel sets a flag the turn future
//! observes; a dropped turn retains its registration until tracked blocking work exits.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use tokio::sync::Notify;

struct Slot {
    flag: Arc<AtomicBool>,
    notify: Arc<Notify>,
    drained: Arc<Notify>,
    blocking: usize,
    released: bool,
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
            drained: Arc::new(Notify::new()),
            blocking: 0,
            released: false,
        },
    );
    Ok((flag, notify))
}

pub fn end(sid: &str, flag: &Arc<AtomicBool>) {
    if let Ok(mut guard) = map().lock() {
        if let Some(slot) = guard
            .get_mut(sid)
            .filter(|slot| Arc::ptr_eq(&slot.flag, flag))
        {
            slot.released = true;
            if slot.blocking == 0 {
                guard.remove(sid);
            } else {
                slot.flag.store(true, Ordering::SeqCst);
                slot.notify.notify_one();
            }
        }
    }
}

pub fn is_active(sid: &str) -> bool {
    map()
        .lock()
        .map(|guard| guard.contains_key(sid))
        .unwrap_or(false)
}

/// Returns true only when a live turn was asked to stop.
pub fn request(sid: &str) -> bool {
    let slot = map().lock().ok().and_then(|guard| {
        guard
            .get(sid)
            .map(|slot| (slot.flag.clone(), slot.notify.clone()))
    });
    let Some((flag, notify)) = slot else {
        return false;
    };
    flag.store(true, Ordering::SeqCst);
    notify.notify_one();
    true
}

pub fn is_cancelled(sid: &str) -> bool {
    map()
        .lock()
        .map(|slots| {
            slots
                .get(sid)
                .is_some_and(|s| s.flag.load(Ordering::SeqCst))
        })
        .unwrap_or(true)
}

struct BlockingPermit {
    sid: String,
    flag: Arc<AtomicBool>,
}
impl Drop for BlockingPermit {
    fn drop(&mut self) {
        if let Ok(mut slots) = map().lock() {
            if let Some(slot) = slots
                .get_mut(&self.sid)
                .filter(|s| Arc::ptr_eq(&s.flag, &self.flag))
            {
                slot.blocking = slot.blocking.saturating_sub(1);
                if slot.blocking == 0 {
                    slot.drained.notify_one();
                    if slot.released {
                        slots.remove(&self.sid);
                    }
                }
            }
        }
    }
}

/// A detached blocking closure owns a permit until it actually exits. Dropping
/// its awaiting future must not release the session or claim cancellation done.
pub async fn blocking<F, T>(sid: &str, work: F) -> Result<T, String>
where
    F: FnOnce() -> T + Send + 'static,
    T: Send + 'static,
{
    let permit = {
        let mut slots = map().lock().map_err(|_| "session lock unavailable")?;
        if let Some(slot) = slots.get_mut(sid) {
            if slot.flag.load(Ordering::SeqCst) || slot.released {
                return Err("task cancelled".into());
            }
            slot.blocking += 1;
            Some(BlockingPermit {
                sid: sid.to_owned(),
                flag: slot.flag.clone(),
            })
        } else {
            None
        }
    };
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        if _permit
            .as_ref()
            .is_some_and(|p| p.flag.load(Ordering::SeqCst))
        {
            return Err("task cancelled before blocking work started".into());
        }
        Ok(work())
    })
    .await
    .map_err(|error| error.to_string())?
}

pub async fn wait_for_blocking(sid: &str, flag: &Arc<AtomicBool>) {
    loop {
        let notify = map().lock().ok().and_then(|slots| {
            slots
                .get(sid)
                .filter(|slot| Arc::ptr_eq(&slot.flag, flag) && slot.blocking > 0)
                .map(|slot| slot.drained.clone())
        });
        let Some(notify) = notify else {
            return;
        };
        notify.notified().await;
    }
}

pub async fn cancelled(flag: &AtomicBool, notify: &Notify) {
    loop {
        if flag.load(Ordering::SeqCst) {
            return;
        }
        notify.notified().await;
    }
}
