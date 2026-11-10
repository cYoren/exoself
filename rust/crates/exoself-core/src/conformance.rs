//! Conformance: does this implementation reproduce `spec/vectors.json`?
//!
//! SPEC section 11. A port that disagrees on one bit has created a different
//! species, so this is the only test that really matters.

use crate::develop::{develop, new_state, State};
use crate::json::{self, Json};
use crate::phenotype::{drive_initial, drive_rate, fixation, phenotype};
use crate::primitives::*;
use crate::registry::Registry;
use crate::sha256::{hex, unhex};

#[derive(Debug, Default)]
pub struct Report {
    pub passed: usize,
    pub failed: usize,
    pub failures: Vec<String>,
}

impl Report {
    pub fn ok(&self) -> bool {
        self.failed == 0
    }

    pub fn summary(&self) -> String {
        format!("{}/{} vectors pass", self.passed + self.failed, self.passed)
    }
}

fn seed_from(v: &Json, what: &str) -> Result<Seed, String> {
    let h = v
        .get("seed")
        .and_then(|x| x.as_str())
        .ok_or_else(|| format!("{}: missing seed", what))?;
    let bytes = unhex(h).ok_or_else(|| format!("{}: seed is not hex", what))?;
    if bytes.len() != SEED_BYTES {
        return Err(format!("{}: seed is not 32 bytes", what));
    }
    let mut s = [0u8; SEED_BYTES];
    s.copy_from_slice(&bytes);
    Ok(s)
}

/// Replay a vector's event log, honouring the reported satisfactions.
///
/// `mode` is the development arithmetic the vector was recorded under (SPEC
/// 8.2). A vector recorded before 0.3 has none, and replays as `walk`.
pub fn replay(
    seed: &Seed,
    reg: &Registry,
    log: &[Json],
    mode: Option<&str>,
) -> Result<State, String> {
    let mut state = new_state(seed, reg);
    if let Some(m) = mode {
        state.develop = m.to_string();
    }
    for (i, entry) in log.iter().enumerate() {
        let event = entry
            .get("event")
            .and_then(|x| x.as_str())
            .ok_or_else(|| format!("log[{}]: missing event", i))?;
        let satisfied: Vec<String> = match entry.get("satisfied").and_then(|x| x.as_array()) {
            Some(items) => items
                .iter()
                .map(|x| x.as_str().unwrap_or_default().to_string())
                .collect(),
            None => Vec::new(),
        };
        state = develop(seed, &state, event.as_bytes(), reg, &satisfied);
    }
    Ok(state)
}

/// Run the vectors. `check_primitives` additionally verifies the primitive
/// checkpoints, which is what you want when porting or debugging.
pub fn run(vectors_text: &str, reg: &Registry, check_primitives: bool) -> Result<Report, String> {
    let doc = json::parse(vectors_text)?;
    let mut report = Report::default();

    if let Some(h) = doc.get("registry_sha256").and_then(|x| x.as_str()) {
        if h != reg.sha256 {
            return Err(format!(
                "vectors were generated for a different registry ({} != {})",
                h, reg.sha256
            ));
        }
    }

    if check_primitives {
        if let Some(p) = doc.get("primitives") {
            let seed = seed_from(p, "primitives")?;
            let mut checks: Vec<(String, String, String)> = Vec::new();
            if let Some(h) = p.get("stream('id', 40)").and_then(|x| x.as_str()) {
                checks.push((
                    "stream('id', 40)".into(),
                    h.to_string(),
                    hex(&stream(&seed, "id", 40)),
                ));
            }
            let u32_cases = [("u32('quirk/units')", "quirk/units")];
            for (label, l) in u32_cases {
                if let Some(want) = p.get(label).and_then(|x| x.as_i64()) {
                    checks.push((label.into(), want.to_string(), u32_at(&seed, l).to_string()));
                }
            }
            for (label, l) in [
                ("gauss('factor/rigor')", "factor/rigor"),
                ("gauss('unique/warmth')", "unique/warmth"),
            ] {
                if let Some(want) = p.get(label).and_then(|x| x.as_i64()) {
                    checks.push((label.into(), want.to_string(), gauss(&seed, l).to_string()));
                }
            }
            for (label, d) in [
                ("drive_rate('teach')", "teach"),
                ("drive_initial('teach')", "teach"),
            ] {
                if let Some(want) = p.get(label).and_then(|x| x.as_i64()) {
                    let got = if label.starts_with("drive_rate") {
                        drive_rate(&seed, d)
                    } else {
                        drive_initial(&seed, d)
                    };
                    checks.push((label.into(), want.to_string(), got.to_string()));
                }
            }
            // The grievance list is duplicated between the two
            // implementations. This vector is what stops the copies drifting:
            // both must return the same matches for the same text.
            let primitive_keys: Vec<String> = match p {
                Json::Object(pairs) => pairs.iter().map(|(k, _)| k.clone()).collect(),
                _ => Vec::new(),
            };
            for key in &primitive_keys {
                if let Some(text) = key
                    .strip_prefix("grievance('")
                    .and_then(|t| t.strip_suffix("')"))
                {
                    let got = crate::registry::grievance_words(text).join(",");
                    let want = p
                        .get(key)
                        .and_then(|x| x.as_array())
                        .map(|a| {
                            a.iter()
                                .filter_map(|x| x.as_str())
                                .collect::<Vec<_>>()
                                .join(",")
                        })
                        .unwrap_or_default();
                    checks.push((key.clone(), want, got));
                }
            }
            if let Some(want) = p
                .get("fixation at events 0, 100, 1000")
                .and_then(|x| x.as_array())
            {
                let events = [0u64, 100, 1000];
                for (i, w) in want.iter().enumerate() {
                    let got = fixation(&seed, reg, events[i]).unwrap_or_default();
                    checks.push((
                        format!("fixation at events {}", events[i]),
                        w.as_str().unwrap_or_default().to_string(),
                        got,
                    ));
                }
            }
            for (label, want, got) in checks {
                if want == got {
                    report.passed += 1;
                } else {
                    report.failed += 1;
                    report
                        .failures
                        .push(format!("{}: want {}, got {}", label, want, got));
                }
            }
        }
    }

    let vectors = doc
        .get("vectors")
        .and_then(|x| x.as_array())
        .ok_or("vectors.json has no vectors")?;

    for (i, v) in vectors.iter().enumerate() {
        let seed = match seed_from(v, &format!("vector {}", i)) {
            Ok(s) => s,
            Err(e) => {
                report.failed += 1;
                report.failures.push(e);
                continue;
            }
        };
        let state = match (v.get("log"), v.get("state")) {
            (Some(Json::Array(log)), _) => match replay(
                &seed,
                reg,
                log,
                v.get("state")
                    .and_then(|s| s.get("develop"))
                    .and_then(|d| d.as_str()),
            ) {
                Ok(s) => Some(s),
                Err(e) => {
                    report.failed += 1;
                    report.failures.push(e);
                    continue;
                }
            },
            (_, Some(st)) => match State::from_json(st) {
                Ok(s) => Some(s),
                Err(e) => {
                    report.failed += 1;
                    report.failures.push(e);
                    continue;
                }
            },
            _ => None,
        };
        let got = phenotype(&seed, reg, state.as_ref()).to_json();
        match v.get("phenotype") {
            Some(want) if *want == got => report.passed += 1,
            Some(want) => {
                report.failed += 1;
                report.failures.push(format!(
                    "vector {} (seed {}): phenotype differs\nwant {}\ngot  {}",
                    i,
                    hex(&seed),
                    want.compact(),
                    got.compact()
                ));
            }
            None => {
                report.failed += 1;
                report
                    .failures
                    .push(format!("vector {}: missing phenotype", i));
            }
        }
    }

    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry::DEFAULT_REGISTRY;

    const VECTORS: &str = include_str!("../../../../spec/vectors.json");

    #[test]
    fn reproduces_every_vector_and_primitive() {
        let reg = Registry::bundled(DEFAULT_REGISTRY).unwrap();
        let r = run(VECTORS, &reg, true).unwrap();
        assert!(r.ok(), "{}\n{}", r.summary(), r.failures.join("\n"));
        assert!(r.passed >= 20, "{}", r.summary());
    }

    #[test]
    fn detects_a_changed_bit() {
        let reg = Registry::bundled(DEFAULT_REGISTRY).unwrap();
        let tampered = VECTORS.replace("\"warmth\": 372", "\"warmth\": 373");
        let r = run(&tampered, &reg, false).unwrap();
        assert!(!r.ok());
        assert_eq!(r.failed, 1);
    }
}
