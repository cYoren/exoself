//! Development: history that turns an agent into an older one (SPEC.md
//! section 8).
//!
//! The envelope is the important part: a dimension moves at most 100 points
//! from its genome value, cumulatively. An agent matures; it does not become
//! someone else. Picks, quirks, handle and id never change.
//!
//! State is written to disk apart from the certificate. The `chain` makes the
//! history tamper-evident: the same state cannot be claimed for a different
//! sequence of events.

use crate::genome::agent_id;
use crate::json::Json;
use crate::phenotype::{derive_drives, drive_initial, drive_rate};
use crate::primitives::*;
use crate::registry::Registry;
use crate::sha256::{hex, sha256, unhex};

#[derive(Debug, Clone, PartialEq)]
pub struct State {
    pub id: String,
    /// Which development arithmetic built this history. Absent in a 0.2 state
    /// file, and read as `walk`, so an old history keeps replaying exactly.
    pub develop: String,
    pub events: u64,
    /// Hex of the running hash. Starts at `stream(seed, "develop", 32)`.
    pub chain: String,
    pub drift: Vec<(String, i64)>,
    pub tension: Vec<(String, i64)>,
}

impl State {
    pub fn drift_of(&self, dim: &str) -> i64 {
        self.drift
            .iter()
            .find(|(k, _)| k == dim)
            .map(|(_, v)| *v)
            .unwrap_or(0)
    }

    pub fn tension_of(&self, drive: &str, seed: &Seed) -> i64 {
        self.tension
            .iter()
            .find(|(k, _)| k == drive)
            .map(|(_, v)| *v)
            .unwrap_or_else(|| drive_initial(seed, drive))
    }

    pub fn from_json(v: &Json) -> Result<State, String> {
        let id = v
            .get("id")
            .and_then(|x| x.as_str())
            .ok_or("state: missing id")?
            .to_string();
        let develop = v
            .get("develop")
            .and_then(|x| x.as_str())
            .unwrap_or(DEVELOP_WALK)
            .to_string();
        let events = v
            .get("events")
            .and_then(|x| x.as_i64())
            .ok_or("state: missing events")? as u64;
        let chain = v
            .get("chain")
            .and_then(|x| x.as_str())
            .ok_or("state: missing chain")?
            .to_string();
        let mut drift = Vec::new();
        if let Some(Json::Object(pairs)) = v.get("drift") {
            for (k, val) in pairs {
                drift.push((
                    k.clone(),
                    val.as_i64()
                        .ok_or_else(|| format!("state: drift {} is not an integer", k))?,
                ));
            }
        }
        let mut tension = Vec::new();
        if let Some(Json::Object(pairs)) = v.get("tension") {
            for (k, val) in pairs {
                tension.push((
                    k.clone(),
                    val.as_i64()
                        .ok_or_else(|| format!("state: tension {} is not an integer", k))?,
                ));
            }
        }
        Ok(State {
            id,
            develop,
            events,
            chain,
            drift,
            tension,
        })
    }

    pub fn to_json(&self) -> Json {
        Json::obj(vec![
            ("id", Json::str(&self.id)),
            ("develop", Json::str(&self.develop)),
            ("events", Json::Int(self.events as i64)),
            ("chain", Json::str(&self.chain)),
            (
                "drift",
                Json::Object(
                    self.drift
                        .iter()
                        .map(|(k, v)| (k.clone(), Json::Int(*v)))
                        .collect(),
                ),
            ),
            (
                "tension",
                Json::Object(
                    self.tension
                        .iter()
                        .map(|(k, v)| (k.clone(), Json::Int(*v)))
                        .collect(),
                ),
            ),
        ])
    }
}

pub fn new_state(seed: &Seed, reg: &Registry) -> State {
    State {
        id: agent_id(seed),
        develop: DEVELOP_DEFAULT.to_string(),
        events: 0,
        chain: hex(&stream(seed, "develop", 32)),
        drift: Vec::new(),
        tension: derive_drives(seed, reg)
            .into_iter()
            .map(|d| {
                let t = drive_initial(seed, &d);
                (d, t)
            })
            .collect(),
    }
}

/// Apply one experience event. Returns a new state; does not mutate.
///
/// `satisfied` names drives the event satisfied. Drives the agent does not hold
/// are ignored and must not affect the chain (SPEC 9.4).
pub fn develop(
    seed: &Seed,
    state: &State,
    event: &[u8],
    reg: &Registry,
    satisfied: &[String],
) -> State {
    let held = derive_drives(seed, reg);
    let mut met: Vec<&str> = satisfied
        .iter()
        .map(|s| s.as_str())
        .filter(|s| held.iter().any(|h| h == s))
        .collect();
    met.sort_unstable();
    met.dedup();

    let mut input = Vec::new();
    match unhex(&state.chain) {
        Some(bytes) if bytes.len() == 32 => input.extend_from_slice(&bytes),
        _ => input.extend_from_slice(state.chain.as_bytes()),
    }
    input.extend_from_slice(&sha256(event));
    input.extend_from_slice(met.join(",").as_bytes());
    let chain = sha256(&input);

    let dim = reg.dimensions
        [(u16::from_be_bytes([chain[0], chain[1]]) as usize) % reg.dimensions.len()]
    .id
    .clone();
    let mut delta = (chain[2] % 7) as i64 - 3;
    if state.develop == DEVELOP_MEAN_REVERTING {
        // Pull toward the genome value. At zero drift this is the 0.2 walk; a
        // dimension that has wandered far gets steps back more often than out,
        // so the envelope is approached rather than sat on (SPEC 8.3).
        delta -= floor_div(state.drift_of(&dim), DRIFT_PULL);
    }

    let mut drift = state.drift.clone();
    match drift.iter_mut().find(|(k, _)| *k == dim) {
        Some((_, v)) => *v = clamp(*v + delta, -DRIFT_LIMIT, DRIFT_LIMIT),
        None => drift.push((dim, clamp(delta, -DRIFT_LIMIT, DRIFT_LIMIT))),
    }

    let tension = held
        .iter()
        .map(|d| {
            let t = clamp(state.tension_of(d, seed), 0, TENSION_MAX);
            let next = if met.contains(&d.as_str()) {
                floor_div(t, 4)
            } else {
                (t + drive_rate(seed, d)).min(TENSION_MAX)
            };
            (d.clone(), next)
        })
        .collect();

    State {
        id: state.id.clone(),
        develop: state.develop.clone(),
        events: state.events + 1,
        chain: hex(&chain),
        drift,
        tension,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry::DEFAULT_REGISTRY;

    fn seed(n: u8) -> Seed {
        let mut s = [0u8; SEED_BYTES];
        s[0] = n;
        s
    }

    #[test]
    fn drift_is_bounded_and_chained() {
        let reg = Registry::bundled("core@0.2").unwrap();
        let s = seed(9);
        let mut st = new_state(&s, &reg);
        for i in 0..5000 {
            st = develop(&s, &st, format!("event-{}", i).as_bytes(), &reg, &[]);
        }
        assert_eq!(st.events, 5000);
        assert!(st.drift.iter().all(|(_, v)| v.abs() <= DRIFT_LIMIT));
        assert!(
            !st.drift.is_empty(),
            "no dimension was touched in 5000 events"
        );
    }

    #[test]
    fn the_old_walk_still_replays_when_the_state_says_so() {
        let reg = Registry::bundled("core@0.2").unwrap();
        let s = seed(6);
        let mut st = new_state(&s, &reg);
        st.develop = DEVELOP_WALK.to_string();
        let mut artefacts = Vec::new();
        for i in 0..200 {
            st = develop(&s, &st, format!("event-{}", i).as_bytes(), &reg, &[]);
            artefacts.push(st.chain.clone());
        }
        // Same seed, same registry, same walk: a state file written before 0.3
        // has no mode field and must produce exactly this history.
        let mut legacy = State {
            id: st.id.clone(),
            develop: DEVELOP_WALK.to_string(),
            events: 0,
            chain: hex(&stream(&s, "develop", 32)),
            drift: Vec::new(),
            tension: st.tension.clone(),
        };
        let json = legacy.to_json();
        legacy = State::from_json(&json).unwrap();
        for (i, want) in artefacts.iter().enumerate() {
            legacy = develop(&s, &legacy, format!("event-{}", i).as_bytes(), &reg, &[]);
            assert_eq!(&legacy.chain, want, "event {}", i);
        }
        assert_eq!(legacy.drift, st.drift);
    }

    #[test]
    fn mean_reversion_stops_drift_at_the_envelope() {
        let reg = Registry::bundled(DEFAULT_REGISTRY).unwrap();
        let mut worst_walk = 0;
        let mut worst_revert = 0;
        for n in 0..40u8 {
            let s = seed(n);
            let mut walk = new_state(&s, &reg);
            walk.develop = DEVELOP_WALK.to_string();
            let mut revert = new_state(&s, &reg);
            for i in 0..3000 {
                let e = format!("event-{}", i);
                walk = develop(&s, &walk, e.as_bytes(), &reg, &[]);
                revert = develop(&s, &revert, e.as_bytes(), &reg, &[]);
            }
            let top = |st: &State| st.drift.iter().map(|(_, v)| v.abs()).max().unwrap_or(0);
            worst_walk = worst_walk.max(top(&walk));
            worst_revert = worst_revert.max(top(&revert));
        }
        assert!(
            worst_walk > 60,
            "walk did not wander ({}), test is blind",
            worst_walk
        );
        assert!(
            worst_revert < 60,
            "mean-reverting drift reached {}, the envelope is not soft",
            worst_revert
        );
    }

    #[test]
    fn tension_grows_with_deprivation_and_falls_with_satisfaction() {
        let reg = Registry::bundled("core@0.2").unwrap();
        let s = seed(4);
        let st = new_state(&s, &reg);
        let held = derive_drives(&s, &reg);
        let d = held[0].clone();
        let t0 = st.tension_of(&d, &s);
        let starved = develop(&s, &st, b"work", &reg, &[]);
        assert_eq!(
            starved.tension_of(&d, &s),
            (t0 + drive_rate(&s, &d)).min(TENSION_MAX)
        );
        let fed = develop(&s, &st, b"work", &reg, std::slice::from_ref(&d));
        assert_eq!(fed.tension_of(&d, &s), floor_div(t0, 4));
        // A drive the agent does not hold is ignored and does not touch the chain.
        let ghost = develop(&s, &st, b"work", &reg, &["not-a-held-drive".to_string()]);
        assert_eq!(ghost.chain, develop(&s, &st, b"work", &reg, &[]).chain);
    }

    #[test]
    fn same_events_same_state_and_tampering_changes_it() {
        let reg = Registry::bundled("core@0.2").unwrap();
        let s = seed(11);
        let a = develop(&s, &new_state(&s, &reg), b"one", &reg, &[]);
        let b = develop(&s, &new_state(&s, &reg), b"one", &reg, &[]);
        assert_eq!(a, b);
        let c = develop(&s, &new_state(&s, &reg), b"two", &reg, &[]);
        assert_ne!(a.chain, c.chain);
    }

    #[test]
    fn state_json_roundtrip() {
        let reg = Registry::bundled("core@0.2").unwrap();
        let s = seed(2);
        let st = develop(&s, &new_state(&s, &reg), b"x", &reg, &[]);
        let back = State::from_json(&crate::json::parse(&st.to_json().pretty()).unwrap()).unwrap();
        assert_eq!(back, st);
    }
}
