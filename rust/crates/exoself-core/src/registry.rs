//! The trait registry (SPEC.md section 5).
//!
//! A registry is a JSON file that defines the trait space. It is loaded as
//! bytes, hashed as those exact bytes, then parsed: the hash is what a birth
//! certificate pins, so the bytes matter and not their meaning.
//!
//! The bundled registries are `include_str!`ed straight out of the reference
//! implementation's package directory. There is one copy of `core-0.1.json` and
//! `core-0.2.json` in this repository, on purpose: two copies drift, and a
//! registry that drifts changes who everyone is.

use crate::json::{self, Json};
use crate::sha256::{hex, sha256};
use std::fmt;
use std::path::Path;

pub const DEFAULT_REGISTRY: &str = "core@0.3";

const CORE_0_1: &str = include_str!("../../../../src/exoself/registry/core-0.1.json");
const CORE_0_2: &str = include_str!("../../../../src/exoself/registry/core-0.2.json");
const CORE_0_3: &str = include_str!("../../../../src/exoself/registry/core-0.3.json");

#[derive(Debug, Clone)]
pub struct Bands {
    pub very_low: String,
    pub low: String,
    pub high: String,
    pub very_high: String,
}

impl Bands {
    pub fn get(&self, band: &str) -> &str {
        match band {
            "very_low" => &self.very_low,
            "low" => &self.low,
            "high" => &self.high,
            "very_high" => &self.very_high,
            "mid" => "",
            other => panic!("unknown band {}", other),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Dimension {
    pub id: String,
    pub loadings: Vec<i64>,
    pub unique: i64,
    pub text: Bands,
}

#[derive(Debug, Clone)]
pub struct Item {
    pub id: String,
    pub text: String,
}

#[derive(Debug, Clone)]
pub struct PickGroup {
    pub name: String,
    pub count: usize,
    pub lead: String,
    pub items: Vec<Item>,
}

#[derive(Debug, Clone)]
pub struct Quirk {
    pub id: String,
    pub permille: i64,
    pub text: String,
}

#[derive(Debug, Clone)]
pub struct DriveItem {
    pub id: String,
    pub want: String,
    pub hungry: String,
}

#[derive(Debug, Clone)]
pub struct Drives {
    pub count: usize,
    pub items: Vec<DriveItem>,
}

#[derive(Debug, Clone)]
pub struct FixationRule {
    pub group: String,
    pub lead: String,
    pub tail: String,
}

#[derive(Debug, Clone)]
pub struct Registry {
    pub name: String,
    pub version: String,
    pub factors: Vec<String>,
    pub dimensions: Vec<Dimension>,
    pub picks: Vec<PickGroup>,
    pub quirks: Vec<Quirk>,
    pub syllables: Vec<String>,
    pub drives: Option<Drives>,
    pub fixation: Option<FixationRule>,
    /// SHA-256 of the exact file bytes this registry was parsed from.
    pub sha256: String,
    /// The parsed file, kept for hosts that want to re-hash or re-validate it.
    pub raw: String,
}

#[derive(Debug)]
pub struct RegistryError(pub String);

impl fmt::Display for RegistryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for RegistryError {}

fn err<T>(msg: impl Into<String>) -> Result<T, RegistryError> {
    Err(RegistryError(msg.into()))
}

/// Words and phrases that turn an appetite into a complaint about the past.
/// Same list as the reference implementation; a registry that trips it fails
/// to load, so the rule in SPEC 9.2.5 is enforced rather than described.
pub const GRIEVANCE: [&str; 13] = [
    "in a while",
    "lately",
    "has been",
    "have been",
    "have not",
    "miss it",
    "you miss",
    "the lack",
    "too much",
    "weighs on you",
    "hungry for",
    "bothers you that",
    "and you feel",
];

pub fn grievance_words(text: &str) -> Vec<&'static str> {
    let low = text.to_lowercase();
    GRIEVANCE
        .iter()
        .copied()
        .filter(|w| low.contains(w))
        .collect()
}

/// A registry's `name@version` identifier, as recorded in a certificate.
impl Registry {
    pub fn id(&self) -> String {
        format!("{}@{}", self.name, self.version)
    }
}

impl Registry {
    /// Load a registry from a file path.
    pub fn from_path(path: impl AsRef<Path>) -> Result<Registry, RegistryError> {
        let raw = std::fs::read_to_string(path.as_ref())
            .map_err(|e| RegistryError(format!("{}: {}", path.as_ref().display(), e)))?;
        Registry::from_bytes(raw)
    }

    /// Load one of the bundled registries by name, e.g. `core@0.2`.
    pub fn bundled(name: &str) -> Result<Registry, RegistryError> {
        let raw = match name {
            "core@0.1" => CORE_0_1,
            "core@0.2" => CORE_0_2,
            "core@0.3" => CORE_0_3,
            other => return err(format!("no bundled registry {:?}", other)),
        };
        Registry::from_bytes(raw.to_string())
    }

    /// Load the registry a certificate pins, falling back to a path.
    pub fn resolve(
        registry_name: &str,
        override_path: Option<&str>,
    ) -> Result<Registry, RegistryError> {
        match override_path {
            Some(p) => Registry::from_path(p),
            None => Registry::bundled(registry_name),
        }
    }

    /// Parse and validate. The hash is taken over the exact bytes given.
    pub fn from_bytes(raw: String) -> Result<Registry, RegistryError> {
        let hash = hex(&sha256(raw.as_bytes()));
        let root = json::parse(&raw).map_err(RegistryError)?;
        let reg = Registry::from_json(&root, hash, raw)?;
        reg.validate()?;
        Ok(reg)
    }

    fn from_json(root: &Json, sha256: String, raw: String) -> Result<Registry, RegistryError> {
        let s = |v: Option<&Json>, what: &str| -> Result<String, RegistryError> {
            match v.and_then(|x| x.as_str()) {
                Some(x) => Ok(x.to_string()),
                None => err(format!("registry: missing or non-string {}", what)),
            }
        };
        let i = |v: Option<&Json>, what: &str| -> Result<i64, RegistryError> {
            match v.and_then(|x| x.as_i64()) {
                Some(x) => Ok(x),
                None => err(format!("registry: missing or non-integer {}", what)),
            }
        };

        let name = s(root.get("registry"), "registry")?;
        let version = s(root.get("version"), "version")?;

        let factors = root
            .get("factors")
            .and_then(|v| v.as_array())
            .ok_or_else(|| RegistryError("registry: missing factors".into()))?
            .iter()
            .map(|f| {
                f.as_str()
                    .map(|x| x.to_string())
                    .ok_or_else(|| RegistryError("registry: factors must be strings".into()))
            })
            .collect::<Result<Vec<_>, _>>()?;

        let mut dimensions = Vec::new();
        for d in root
            .get("dimensions")
            .and_then(|v| v.as_array())
            .ok_or_else(|| RegistryError("registry: missing dimensions".into()))?
        {
            let id = s(d.get("id"), "dimensions[].id")?;
            let loadings = d
                .get("loadings")
                .and_then(|v| v.as_array())
                .ok_or_else(|| RegistryError(format!("{}: missing loadings", id)))?
                .iter()
                .map(|x| {
                    x.as_i64()
                        .ok_or_else(|| RegistryError(format!("{}: loadings must be integers", id)))
                })
                .collect::<Result<Vec<_>, _>>()?;
            let unique = i(d.get("unique"), "unique")?;
            let text = d
                .get("text")
                .ok_or_else(|| RegistryError(format!("{}: missing text", id)))?;
            dimensions.push(Dimension {
                id,
                loadings,
                unique,
                text: Bands {
                    very_low: s(text.get("very_low"), "text.very_low")?,
                    low: s(text.get("low"), "text.low")?,
                    high: s(text.get("high"), "text.high")?,
                    very_high: s(text.get("very_high"), "text.very_high")?,
                },
            });
        }

        let mut picks = Vec::new();
        if let Some(groups) = root.get("picks").and_then(|v| match v {
            Json::Object(pairs) => Some(pairs),
            _ => None,
        }) {
            for (group_name, g) in groups {
                let count = i(g.get("count"), "picks.count")? as usize;
                let lead = s(g.get("lead"), "picks.lead")?;
                let mut items = Vec::new();
                for it in g
                    .get("items")
                    .and_then(|v| v.as_array())
                    .ok_or_else(|| RegistryError(format!("picks.{}: missing items", group_name)))?
                {
                    items.push(Item {
                        id: s(it.get("id"), "item.id")?,
                        text: s(it.get("text"), "item.text")?,
                    });
                }
                picks.push(PickGroup {
                    name: group_name.clone(),
                    count,
                    lead,
                    items,
                });
            }
        }

        let mut quirks = Vec::new();
        for q in root
            .get("quirks")
            .and_then(|v| v.as_array())
            .ok_or_else(|| RegistryError("registry: missing quirks".into()))?
        {
            quirks.push(Quirk {
                id: s(q.get("id"), "quirk.id")?,
                permille: i(q.get("permille"), "quirk.permille")?,
                text: s(q.get("text"), "quirk.text")?,
            });
        }

        let syllables = root
            .get("handle")
            .and_then(|h| h.get("syllables"))
            .and_then(|v| v.as_array())
            .ok_or_else(|| RegistryError("registry: missing handle.syllables".into()))?
            .iter()
            .map(|x| {
                x.as_str()
                    .map(|s| s.to_string())
                    .ok_or_else(|| RegistryError("handle.syllables must be strings".into()))
            })
            .collect::<Result<Vec<_>, _>>()?;

        let drives = match root.get("drives") {
            Some(d) => {
                let mut items = Vec::new();
                for it in d
                    .get("items")
                    .and_then(|v| v.as_array())
                    .ok_or_else(|| RegistryError("drives: missing items".into()))?
                {
                    items.push(DriveItem {
                        id: s(it.get("id"), "drive.id")?,
                        want: s(it.get("want"), "drive.want")?,
                        hungry: s(it.get("hungry"), "drive.hungry")?,
                    });
                }
                Some(Drives {
                    count: i(d.get("count"), "drives.count")? as usize,
                    items,
                })
            }
            None => None,
        };

        let fixation = match root.get("fixation") {
            Some(f) => Some(FixationRule {
                group: s(f.get("group"), "fixation.group")?,
                lead: s(f.get("lead"), "fixation.lead")?,
                tail: s(f.get("tail"), "fixation.tail")?,
            }),
            None => None,
        };

        Ok(Registry {
            name,
            version,
            factors,
            dimensions,
            picks,
            quirks,
            syllables,
            drives,
            fixation,
            sha256,
            raw,
        })
    }

    /// SPEC section 5.2. A registry that fails here must not be used: unit
    /// variance is what keeps scores comparable across registries.
    pub fn validate(&self) -> Result<(), RegistryError> {
        let nf = self.factors.len();
        let mut seen = std::collections::HashSet::new();
        for d in &self.dimensions {
            if !seen.insert(d.id.clone()) {
                return err(format!("duplicate dimension {}", d.id));
            }
            if d.loadings.len() != nf {
                return err(format!("{}: need {} loadings", d.id, nf));
            }
            let ss: i64 = d.loadings.iter().map(|x| x * x).sum::<i64>() + d.unique * d.unique;
            if !(9800..=10200).contains(&ss) {
                return err(format!(
                    "{}: squared loadings sum to {}, need 9800..10200",
                    d.id, ss
                ));
            }
            for band in ["very_low", "low", "high", "very_high"] {
                let text = d.text.get(band);
                if text.chars().any(|c| c.is_ascii_digit()) {
                    return err(format!(
                        "{}.{}: expression text must not contain digits",
                        d.id, band
                    ));
                }
            }
        }
        for g in &self.picks {
            let mut ids = std::collections::HashSet::new();
            for it in &g.items {
                if !ids.insert(it.id.clone()) {
                    return err(format!("picks.{}: duplicate id {}", g.name, it.id));
                }
            }
            if g.count > g.items.len() {
                return err(format!("picks.{}: count exceeds item count", g.name));
            }
        }
        for q in &self.quirks {
            if !(0..=1000).contains(&q.permille) {
                return err(format!("quirk {}: permille out of range", q.id));
            }
        }
        if let Some(d) = &self.drives {
            let mut ids = std::collections::HashSet::new();
            for it in &d.items {
                if !ids.insert(it.id.clone()) {
                    return err(format!("drives: duplicate id {}", it.id));
                }
                let text = format!("{}{}", it.want, it.hungry);
                if text.chars().any(|c| c.is_ascii_digit()) {
                    return err(format!(
                        "drive {}: expression text must not contain digits",
                        it.id
                    ));
                }
            }
            if d.count > d.items.len() {
                return err("drives: count exceeds item count");
            }
            if self.version.as_str() >= "0.3" {
                // SPEC 9.2.5: a hungry text may sharpen the want; it may not
                // present the unmet want as a grievance. 0.1 and 0.2 fail this
                // on purpose, which is the bug this rule exists to fix.
                for it in &d.items {
                    if let Some(w) = grievance_words(&it.hungry).first() {
                        return err(format!(
                            "drive {}: hungry text presents a grievance ({:?}); see SPEC 9.2.5",
                            it.id, w
                        ));
                    }
                }
            }
        }
        if let Some(f) = &self.fixation {
            let group = self
                .picks
                .iter()
                .find(|g| g.name == f.group)
                .ok_or_else(|| RegistryError(format!("fixation: unknown group {}", f.group)))?;
            if group.items.len() < 2 {
                return err("fixation: group needs at least two items");
            }
        }
        Ok(())
    }

    pub fn dimension(&self, id: &str) -> Option<&Dimension> {
        self.dimensions.iter().find(|d| d.id == id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn core_0_3_is_purely_additive() {
        let old = Registry::bundled("core@0.2").unwrap();
        let new = Registry::bundled("core@0.3").unwrap();
        assert_eq!(new.id(), "core@0.3");
        assert_eq!(
            new.sha256,
            "23803a3d4f1d5e3ec2b8f4621df083af0281ce299bba0b675b84f6d00d45375d"
        );
        assert_eq!(new.dimensions.len(), old.dimensions.len());
        assert_eq!(new.factors, old.factors);
        assert_eq!(new.quirks.len(), old.quirks.len());
        // Every group that existed keeps its name, order, count and items.
        for g in &old.picks {
            let n = new
                .picks
                .iter()
                .find(|x| x.name == g.name)
                .expect("group kept");
            assert_eq!(g.count, n.count);
            assert_eq!(g.items.len(), n.items.len());
            for (a, b) in g.items.iter().zip(n.items.iter()) {
                assert_eq!(a.id, b.id);
                assert_eq!(a.text, b.text);
            }
        }
        assert!(new.picks.iter().any(|g| g.name == "rhythm"));
    }

    #[test]
    fn a_new_registry_fails_the_grievance_rule_and_the_old_ones_load() {
        let raw = CORE_0_2.replace("\"version\": \"0.2\"", "\"version\": \"0.3\"");
        match Registry::from_bytes(raw) {
            Err(e) => assert!(e.0.contains("grievance"), "{}", e.0),
            Ok(_) => panic!("0.2 drive text passed the 9.2.5 rule"),
        }
        assert!(Registry::bundled("core@0.2").is_ok());
        assert!(Registry::bundled("core@0.3").is_ok());
    }

    #[test]
    fn core_registries_load_and_hash() {
        let r = Registry::bundled("core@0.2").unwrap();
        assert_eq!(r.id(), "core@0.2");
        assert_eq!(
            r.sha256,
            "8de4d3729c6e319d926dd6b5eb09efef97cfc0147d6402c60ef65d1812fd0ec3"
        );
        assert_eq!(r.dimensions.len(), 10);
        assert_eq!(r.factors.len(), 4);
        let r1 = Registry::bundled("core@0.1").unwrap();
        assert_eq!(
            r1.sha256,
            "bdbb3aa86ffa4915ed288ddd5f989eed255c02643b27502e70f59f1c1d905985"
        );
        assert!(r1.drives.is_none());
    }

    #[test]
    fn mismatched_loadings_are_rejected() {
        let mut raw = CORE_0_2.to_string();
        raw = raw.replace("\"unique\": 80", "\"unique\": 20");
        let e = Registry::from_bytes(raw).unwrap_err().to_string();
        assert!(e.contains("squared loadings"), "{}", e);
    }
}
