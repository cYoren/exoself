//! Genome: the traits a seed implies (SPEC.md section 6).
//!
//! Every trait draws from its own label, so adding a trait to a registry never
//! changes the value of any existing trait. Nothing here looks at the model,
//! the host or the operator: the seed is the only input.

use crate::primitives::*;
use crate::registry::Registry;
use crate::sha256::hex as to_hex;

/// Scores in registry order. The order is part of the output format.
#[derive(Debug, Clone, PartialEq)]
pub struct Scores(pub Vec<(String, i64)>);

impl Scores {
    pub fn get(&self, id: &str) -> Option<i64> {
        self.0.iter().find(|(k, _)| k == id).map(|(_, v)| *v)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&String, i64)> {
        self.0.iter().map(|(k, v)| (k, *v))
    }
}

pub fn agent_id(seed: &Seed) -> String {
    format!("exo_{}", to_hex(&crate::primitives::stream(seed, "id", 8)))
}

pub fn derive_scores(seed: &Seed, reg: &Registry) -> Scores {
    let factors: Vec<i64> = reg
        .factors
        .iter()
        .map(|f| gauss(seed, &format!("factor/{}", f)))
        .collect();
    let mut out = Vec::with_capacity(reg.dimensions.len());
    for d in &reg.dimensions {
        let mut raw: i64 = d
            .loadings
            .iter()
            .zip(factors.iter())
            .map(|(l, f)| l * f)
            .sum();
        raw += d.unique * gauss(seed, &format!("unique/{}", d.id));
        let score = clamp(
            SCORE_MEAN + floor_div(raw * SCORE_SD, SCALE_DEN),
            0,
            SCORE_MAX,
        );
        out.push((d.id.clone(), score));
    }
    Scores(out)
}

/// The `count` items with the highest rank, descending, ties broken by
/// ascending id. A stable sort on `(-rank, id)` is the same thing.
pub fn derive_picks(seed: &Seed, reg: &Registry) -> Vec<(String, Vec<String>)> {
    reg.picks
        .iter()
        .map(|g| {
            let mut ranked: Vec<(u32, &str)> = g
                .items
                .iter()
                .map(|it| {
                    (
                        u32_at(seed, &format!("pick/{}/{}", g.name, it.id)),
                        it.id.as_str(),
                    )
                })
                .collect();
            ranked.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(b.1)));
            let ids = ranked
                .iter()
                .take(g.count)
                .map(|(_, id)| id.to_string())
                .collect();
            (g.name.clone(), ids)
        })
        .collect()
}

pub fn derive_quirks(seed: &Seed, reg: &Registry) -> Vec<String> {
    reg.quirks
        .iter()
        .filter(|q| {
            let threshold = ((q.permille as u64) << 32) / 1000;
            (u32_at(seed, &format!("quirk/{}", q.id)) as u64) < threshold
        })
        .map(|q| q.id.clone())
        .collect()
}

/// A convenience for humans. Not unique; the `id` is the identity (SPEC 6.4).
pub fn derive_handle(seed: &Seed, reg: &Registry) -> String {
    let syl = &reg.syllables;
    let mut parts = Vec::with_capacity(4);
    for k in 0..4 {
        parts.push(syl[(u32_at(seed, &format!("handle/{}", k)) as usize) % syl.len()].clone());
    }
    format!(
        "{}-{}",
        capitalize(&format!("{}{}", parts[0], parts[1])),
        capitalize(&format!("{}{}", parts[2], parts[3]))
    )
}

/// Python's `str.capitalize()`: first character upper, the rest lower. The
/// reference implementation uses it, so a port that uses `to_uppercase()` on
/// the first char only would disagree on mixed-case syllables.
fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        None => String::new(),
        Some(first) => {
            let mut out: String = first.to_uppercase().collect();
            for c in chars {
                out.extend(c.to_lowercase());
            }
            out
        }
    }
}

// Silence the unused imports above without changing the public surface.

#[cfg(test)]
mod tests {
    use super::*;

    fn seed(n: u8) -> Seed {
        let mut s = [0u8; SEED_BYTES];
        s[SEED_BYTES - 1] = n;
        s
    }

    #[test]
    fn capitalize_matches_python() {
        assert_eq!(capitalize("ka"), "Ka");
        assert_eq!(capitalize("KEL"), "Kel");
        assert_eq!(capitalize(""), "");
    }

    #[test]
    fn additions_do_not_disturb_existing_traits() {
        let reg = Registry::bundled("core@0.2").unwrap();
        let mut raw: crate::json::Json = crate::json::parse(&reg.raw).unwrap();
        if let crate::json::Json::Object(pairs) = &mut raw {
            // Add a quirk and an unrelated dimension.
            for (k, v) in pairs.iter_mut() {
                if k == "quirks" {
                    if let crate::json::Json::Array(items) = v {
                        items.push(crate::json::Json::obj(vec![
                            ("id", crate::json::Json::str("new-quirk")),
                            ("permille", crate::json::Json::Int(500)),
                            ("text", crate::json::Json::str("x")),
                        ]));
                    }
                }
                if k == "dimensions" {
                    if let crate::json::Json::Array(items) = v {
                        items.push(crate::json::Json::obj(vec![
                            ("id", crate::json::Json::str("extra")),
                            ("poles", crate::json::Json::Array(vec![])),
                            (
                                "loadings",
                                crate::json::Json::Array(vec![crate::json::Json::Int(0); 4]),
                            ),
                            ("unique", crate::json::Json::Int(100)),
                            (
                                "text",
                                crate::json::Json::obj(vec![
                                    ("very_low", crate::json::Json::str("x")),
                                    ("low", crate::json::Json::str("x")),
                                    ("high", crate::json::Json::str("x")),
                                    ("very_high", crate::json::Json::str("x")),
                                ]),
                            ),
                        ]));
                    }
                }
            }
        }
        let ext = Registry::from_bytes(raw.pretty()).unwrap();
        for n in 0..50u8 {
            let s = seed(n);
            let base = derive_scores(&s, &reg);
            let new = derive_scores(&s, &ext);
            for (id, v) in base.iter() {
                assert_eq!(new.get(id), Some(v));
            }
            assert_eq!(derive_picks(&s, &reg), derive_picks(&s, &ext));
            let bq: Vec<_> = derive_quirks(&s, &reg);
            let eq: Vec<_> = derive_quirks(&s, &ext)
                .into_iter()
                .filter(|q| q != "new-quirk")
                .collect();
            assert_eq!(bq, eq);
        }
    }
}
