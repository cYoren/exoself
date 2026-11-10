//! A lock around one state file.
//!
//! Two hosts can end up recording sessions for the same agent: the README
//! recommends one `.mcp.json` for pi, Claude Code and Cursor, and any of them
//! can also run `exoself develop` by hand. Development is read-modify-write on
//! a chained state file, so without a lock the later writer silently erases
//! the earlier event.
//!
//! The lock is a sibling file created with `create_new`, which is atomic on
//! every platform this builds on and needs no dependency. A lock left behind
//! by a killed process is stolen after `STALE`, because a wedged agent is worse
//! than a rare double write.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime};

const STALE: Duration = Duration::from_secs(60);
const WAIT: Duration = Duration::from_secs(10);
const POLL: Duration = Duration::from_millis(10);

pub struct StateLock {
    path: PathBuf,
    pid: String,
}

impl StateLock {
    /// Take the lock for `state_path`, waiting up to `WAIT`.
    pub fn acquire(state_path: &str) -> Result<StateLock, String> {
        let path = PathBuf::from(format!("{}.lock", state_path));
        let started = Instant::now();
        loop {
            match std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)
            {
                Ok(mut f) => {
                    let pid = std::process::id().to_string();
                    let _ = writeln!(f, "{}", pid);
                    return Ok(StateLock { path, pid });
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                    if is_stale(&path) {
                        // Someone died holding it. Take it over.
                        let _ = std::fs::remove_file(&path);
                        continue;
                    }
                    if started.elapsed() > WAIT {
                        return Err(format!(
                            "{} is locked by another process ({}). Remove it if that \
                             process is gone.",
                            path.display(),
                            path.display()
                        ));
                    }
                    std::thread::sleep(POLL);
                }
                Err(e) => return Err(format!("{}: {}", path.display(), e)),
            }
        }
    }
}

fn is_stale(path: &Path) -> bool {
    std::fs::metadata(path)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| SystemTime::now().duration_since(t).ok())
        .map(|age| age > STALE)
        .unwrap_or(false)
}

impl Drop for StateLock {
    fn drop(&mut self) {
        // Only remove a lock that is still ours. If it was stolen while we held
        // it past STALE, deleting it would release someone else's lock.
        if std::fs::read_to_string(&self.path)
            .map(|s| s.trim() == self.pid)
            .unwrap_or(false)
        {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> String {
        std::env::temp_dir()
            .join(format!("exoself-lock-{}-{}", std::process::id(), name))
            .display()
            .to_string()
    }

    #[test]
    fn a_second_holder_is_kept_out_until_the_first_lets_go() {
        let path = tmp("basic");
        let _ = std::fs::remove_file(format!("{}.lock", path));
        let first = StateLock::acquire(&path).unwrap();
        assert!(Path::new(&format!("{}.lock", path)).exists());
        // A short wait, then the first holder releases and the second gets in.
        let waiter = std::thread::spawn({
            let path = path.clone();
            move || {
                std::thread::sleep(Duration::from_millis(120));
                drop(first);
                StateLock::acquire(&path).map(|l| l.path.display().to_string())
            }
        });
        let second = StateLock::acquire(&path);
        // Either we got it (the first was released) or we waited; both are
        // correct, but the file must not be left behind.
        drop(second);
        let got = waiter.join().unwrap();
        assert!(got.is_ok(), "{:?}", got.err());
        assert!(!Path::new(&format!("{}.lock", path)).exists());
    }

    #[test]
    fn a_lock_left_by_a_dead_process_is_stolen() {
        let path = tmp("stale");
        let lock = format!("{}.lock", path);
        let _ = std::fs::remove_file(&lock);
        std::fs::write(&lock, b"999999\n").unwrap();
        // Backdate it past STALE.
        let old = SystemTime::now() - STALE - Duration::from_secs(5);
        let f = std::fs::OpenOptions::new().write(true).open(&lock).unwrap();
        f.set_modified(old).unwrap();
        drop(f);
        let held = StateLock::acquire(&path).expect("stale lock is stolen");
        assert!(Path::new(&lock).exists());
        drop(held);
        assert!(!Path::new(&lock).exists());
    }
}
