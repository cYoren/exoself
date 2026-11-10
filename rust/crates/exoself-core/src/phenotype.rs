//! Phenotype: the agent at one moment (SPEC.md section 7).
//!
//! The phenotype object is the normative output of this spec. Two
//! implementations conform if they produce equal phenotypes for the same seed,
//! registry and state.

use crate::develop::State;
use crate::genome;
use crate::json::Json;
use crate::primitives::*;
use crate::registry::Registry;

#[derive(Debug, Clone, PartialEq)]
pub struct Phenotype {
    pub id: String,
    pub handle: String,
    pub scores: Vec<(String, i64)>,
    pub salient: Vec<(String, &'static str)>,
    pub picks: Vec<(String, Vec<String>)>,
    pub quirks: Vec<String>,
    pub drives: Vec<(String, i64, &'static str)>,
    pub fixation: Option<String>,
}

impl Phenotype {
    pub fn score(&self, dim: &str) -> i64 {
        self.scores
            .iter()
            .find(|(k, _)| k == dim)
            .map(|(_, v)| *v)
            .expect("unknown dimension")
    }

    /// The phenotype exactly as SPEC 7.3 lays it out, in that key order.
    pub fn to_json(&self) -> Json {
        Json::obj(vec![
            ("exoself", Json::str(SPEC_VERSION)),
            ("id", Json::str(&self.id)),
            ("handle", Json::str(&self.handle)),
            (
                "scores",
                Json::Object(
                    self.scores
                        .iter()
                        .map(|(k, v)| (k.clone(), Json::Int(*v)))
                        .collect(),
                ),
            ),
            (
                "salient",
                Json::Array(
                    self.salient
                        .iter()
                        .map(|(d, b)| {
                            Json::obj(vec![("dimension", Json::str(d)), ("band", Json::str(*b))])
                        })
                        .collect(),
                ),
            ),
            (
                "picks",
                Json::Object(
                    self.picks
                        .iter()
                        .map(|(k, v)| (k.clone(), Json::Array(v.iter().map(Json::str).collect())))
                        .collect(),
                ),
            ),
            (
                "quirks",
                Json::Array(self.quirks.iter().map(Json::str).collect()),
            ),
            (
                "drives",
                Json::Array(
                    self.drives
                        .iter()
                        .map(|(d, t, s)| {
                            Json::obj(vec![
                                ("drive", Json::str(d)),
                                ("tension", Json::Int(*t)),
                                ("state", Json::str(*s)),
                            ])
                        })
                        .collect(),
                ),
            ),
            (
                "fixation",
                match &self.fixation {
                    Some(f) => Json::str(f),
                    None => Json::Null,
                },
            ),
        ])
    }
}

/// The drives this agent holds, strongest first, fixed for life (SPEC 9.3).
pub fn derive_drives(seed: &Seed, reg: &Registry) -> Vec<String> {
    let Some(drives) = &reg.drives else {
        return Vec::new();
    };
    let mut ranked: Vec<(u32, &str)> = drives
        .items
        .iter()
        .map(|it| {
            (
                u32_at(seed, &format!("drive/hold/{}", it.id)),
                it.id.as_str(),
            )
        })
        .collect();
    ranked.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(b.1)));
    ranked
        .iter()
        .take(drives.count)
        .map(|(_, id)| id.to_string())
        .collect()
}

/// How fast this want grows back, in tension points per event: 10..40.
pub fn drive_rate(seed: &Seed, drive: &str) -> i64 {
    10 + (u32_at(seed, &format!("drive/rate/{}", drive)) % 31) as i64
}

/// Tension at birth: 0..650. An agent is born already wanting.
pub fn drive_initial(seed: &Seed, drive: &str) -> i64 {
    (u32_at(seed, &format!("drive/initial/{}", drive)) % (TENSION_HUNGRY as u32 + 1)) as i64
}

/// The current fixation. It rotates on a schedule nobody controls (SPEC 9.5).
pub fn fixation(seed: &Seed, reg: &Registry, events: u64) -> Option<String> {
    let fix = reg.fixation.as_ref()?;
    let group = reg.picks.iter().find(|g| g.name == fix.group)?;
    let items: Vec<&str> = group.items.iter().map(|i| i.id.as_str()).collect();

    let mut epoch: u64 = 0;
    let mut start: u64 = 0;
    let mut prev: Option<&str> = None;
    loop {
        let length = 20 + (u32_at(seed, &format!("fixation/len/{}", epoch)) % 101) as u64;
        let mut best: Option<(u32, &str)> = None;
        for id in &items {
            if Some(*id) == prev {
                continue;
            }
            let rank = u32_at(seed, &format!("fixation/obj/{}/{}", epoch, id));
            best = match best {
                None => Some((rank, id)),
                Some((r, b)) => {
                    if rank > r || (rank == r && *id < b) {
                        Some((rank, id))
                    } else {
                        Some((r, b))
                    }
                }
            };
        }
        let obj = best.map(|(_, id)| id).unwrap_or("");
        if events < start + length {
            return Some(obj.to_string());
        }
        epoch += 1;
        start += length;
        prev = Some(obj);
    }
}

/// Which dimensions get expressed, most extreme first (SPEC 7.2).
pub fn salient(scores: &[(String, i64)]) -> Vec<(String, &'static str)> {
    let mut ranked: Vec<(usize, &String, i64)> = scores
        .iter()
        .enumerate()
        .map(|(i, (id, v))| (i, id, *v))
        .collect();
    ranked.sort_by(|a, b| {
        let ka = (a.2 - SCORE_MEAN).abs();
        let kb = (b.2 - SCORE_MEAN).abs();
        kb.cmp(&ka).then(a.0.cmp(&b.0))
    });

    let mut chosen: Vec<&String> = ranked
        .iter()
        .filter(|(_, _, v)| band(*v) != "mid")
        .map(|(_, id, _)| *id)
        .take(MAX_EXPRESSED)
        .collect();
    if chosen.len() < MIN_EXPRESSED {
        for (_, id, _) in &ranked {
            if chosen.len() >= MIN_EXPRESSED {
                break;
            }
            if !chosen.contains(id) {
                chosen.push(id);
            }
        }
    }
    let lookup = |id: &String| {
        scores
            .iter()
            .find(|(k, _)| k == id)
            .map(|(_, v)| *v)
            .unwrap()
    };
    chosen
        .into_iter()
        .map(|id| {
            let v = lookup(id);
            let b = band(v);
            let b = if b == "mid" {
                if v >= SCORE_MEAN {
                    "high"
                } else {
                    "low"
                }
            } else {
                b
            };
            (id.clone(), b)
        })
        .collect()
}

pub fn phenotype(seed: &Seed, reg: &Registry, state: Option<&State>) -> Phenotype {
    let genome = genome::derive_scores(seed, reg);
    let scores: Vec<(String, i64)> = genome
        .iter()
        .map(|(id, s)| {
            let drift = state.map(|st| st.drift_of(id)).unwrap_or(0);
            (
                id.clone(),
                clamp(s + clamp(drift, -DRIFT_LIMIT, DRIFT_LIMIT), 0, SCORE_MAX),
            )
        })
        .collect();

    let held = derive_drives(seed, reg);
    let drives = held
        .iter()
        .map(|d| {
            let t = state
                .map(|st| st.tension_of(d, seed))
                .unwrap_or_else(|| drive_initial(seed, d));
            let t = clamp(t, 0, TENSION_MAX);
            (d.clone(), t, tension_band(t))
        })
        .collect();

    let events = state.map(|st| st.events).unwrap_or(0);

    Phenotype {
        id: genome::agent_id(seed),
        handle: genome::derive_handle(seed, reg),
        salient: salient(&scores),
        scores,
        picks: genome::derive_picks(seed, reg),
        quirks: genome::derive_quirks(seed, reg),
        drives,
        fixation: fixation(seed, reg, events),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seed(n: u8) -> Seed {
        let mut s = [0u8; SEED_BYTES];
        s[0] = n;
        s[31] = n.wrapping_mul(7);
        s
    }

    #[test]
    fn salient_counts_are_bounded_and_never_mid() {
        let reg = Registry::bundled("core@0.2").unwrap();
        for n in 0..500u16 {
            let mut s = seed(0);
            s[0] = (n % 256) as u8;
            s[1] = (n / 256) as u8;
            let p = phenotype(&s, &reg, None);
            assert!((MIN_EXPRESSED..=MAX_EXPRESSED).contains(&p.salient.len()));
            assert!(p.salient.iter().all(|(_, b)| *b != "mid"));
        }
    }

    #[test]
    fn drives_are_held_for_life_and_ordered() {
        let reg = Registry::bundled("core@0.2").unwrap();
        for n in 0..50u8 {
            let s = seed(n);
            let a = derive_drives(&s, &reg);
            let b = derive_drives(&s, &reg);
            assert_eq!(a, b);
            assert_eq!(a.len(), 3);
            assert_eq!(
                a.len(),
                a.iter().collect::<std::collections::HashSet<_>>().len()
            );
        }
    }

    #[test]
    fn fixation_rotates_and_never_repeats_back_to_back() {
        let reg = Registry::bundled("core@0.2").unwrap();
        let s = seed(3);
        let interests: Vec<&str> = reg
            .picks
            .iter()
            .find(|g| g.name == "interests")
            .unwrap()
            .items
            .iter()
            .map(|i| i.id.as_str())
            .collect();
        let seq: Vec<String> = (0..3000).map(|n| fixation(&s, &reg, n).unwrap()).collect();
        assert!(seq.iter().all(|f| interests.contains(&f.as_str())));
        // Collapse runs: consecutive epochs must differ, and an epoch lasts at
        // least 20 events (SPEC 9.5).
        let mut runs: Vec<&String> = vec![&seq[0]];
        for w in seq.windows(2) {
            if w[0] != w[1] {
                runs.push(&w[1]);
            }
        }
        assert!(
            runs.len() >= 3000 / 120,
            "fixation barely moved: {}",
            runs.len()
        );
        assert!(runs.windows(2).all(|w| w[0] != w[1]));
        for r in runs.iter().take(5) {
            let len = seq[..1500].iter().filter(|x| x == r).count();
            assert!(len >= 20, "fixation epoch shorter than 20 events");
        }
    }
}
