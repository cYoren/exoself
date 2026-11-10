//! Birth: the one draw, and the certificate that records it (SPEC.md
//! section 3).
//!
//! An agent is born once. Nothing in this module will overwrite a certificate:
//! drawing again until the result is pleasing defeats the point.

use crate::genome::agent_id;
use crate::json::{self, Json};
use crate::primitives::*;
use crate::registry::Registry;
use crate::sha256::{hex, sha256, unhex};

#[derive(Debug, Clone)]
pub struct Certificate {
    pub exoself: String,
    pub id: String,
    pub seed: Seed,
    pub born_at: String,
    pub registry: String,
    pub registry_sha256: String,
    pub attestation: Json,
}

impl Certificate {
    pub fn to_json(&self) -> Json {
        Json::obj(vec![
            ("exoself", Json::str(&self.exoself)),
            ("id", Json::str(&self.id)),
            ("seed", Json::str(hex(&self.seed))),
            ("born_at", Json::str(&self.born_at)),
            ("registry", Json::str(&self.registry)),
            ("registry_sha256", Json::str(&self.registry_sha256)),
            ("attestation", self.attestation.clone()),
        ])
    }

    pub fn from_json(v: &Json) -> Result<Certificate, String> {
        let s = |k: &str| -> Result<String, String> {
            v.get(k)
                .and_then(|x| x.as_str())
                .map(|x| x.to_string())
                .ok_or_else(|| format!("certificate: missing {}", k))
        };
        let seed_hex = s("seed")?;
        let seed_bytes = unhex(&seed_hex).ok_or("certificate: seed is not hex")?;
        if seed_bytes.len() != SEED_BYTES {
            return Err("certificate: seed must be exactly 32 bytes".into());
        }
        let mut seed = [0u8; SEED_BYTES];
        seed.copy_from_slice(&seed_bytes);
        Ok(Certificate {
            exoself: s("exoself")?,
            id: s("id")?,
            seed,
            born_at: s("born_at")?,
            registry: s("registry")?,
            registry_sha256: s("registry_sha256")?,
            attestation: v.get("attestation").cloned().unwrap_or(Json::Null),
        })
    }

    pub fn parse(text: &str) -> Result<Certificate, String> {
        Certificate::from_json(&json::parse(text)?)
    }
}

/// Level 1 seed: neither the operator nor the beacon alone decides it.
pub fn bind_beacon(local_entropy: &[u8], beacon: &[u8]) -> Result<Seed, String> {
    if local_entropy.len() != SEED_BYTES {
        return Err("local entropy must be exactly 32 bytes".into());
    }
    let mut input = PREFIX.to_vec();
    input.extend_from_slice(b"beacon\x00");
    input.extend_from_slice(local_entropy);
    input.extend_from_slice(beacon);
    Ok(sha256(&input))
}

pub fn commitment(local_entropy: &[u8]) -> String {
    hex(&sha256(local_entropy))
}

/// Thirty-two bytes from the operating system CSPRNG.
///
/// std has no RNG, and this crate has no dependencies, so the entropy comes
/// from the OS device. On a platform without it, birth fails loudly instead of
/// quietly producing a weak seed: pass `--local-entropy` or use the browser
/// build, which draws from `crypto.getRandomValues`.
pub fn os_entropy() -> Result<Seed, String> {
    use std::io::Read;
    let mut seed = [0u8; SEED_BYTES];
    for path in ["/dev/urandom", "/dev/random"] {
        if let Ok(mut f) = std::fs::File::open(path) {
            if f.read_exact(&mut seed).is_ok() {
                return Ok(seed);
            }
        }
    }
    Err(
        "no OS entropy source on this platform: pass --local-entropy <64 hex chars> \
         (32 bytes from any CSPRNG) or --beacon-value, or use the browser build"
            .into(),
    )
}

pub fn born(seed: &Seed, reg: &Registry, born_at: &str, attestation: Json) -> Certificate {
    Certificate {
        exoself: SPEC_VERSION.to_string(),
        id: agent_id(seed),
        seed: *seed,
        born_at: born_at.to_string(),
        registry: reg.id(),
        registry_sha256: reg.sha256.clone(),
        attestation,
    }
}

pub fn attestation_self_asserted() -> Json {
    Json::obj(vec![
        ("level", Json::Int(0)),
        ("source", Json::str("os-csprng")),
    ])
}

pub fn attestation_supplied() -> Json {
    Json::obj(vec![
        ("level", Json::Int(0)),
        ("source", Json::str("supplied")),
    ])
}

pub fn attestation_beacon(local_entropy: &[u8], beacon_id: &str, beacon_value: &[u8]) -> Json {
    Json::obj(vec![
        ("level", Json::Int(1)),
        ("commitment", Json::str(commitment(local_entropy))),
        ("local_entropy", Json::str(hex(local_entropy))),
        ("beacon", Json::str(beacon_id)),
        ("beacon_value", Json::str(hex(beacon_value))),
    ])
}

/// Return a list of problems. Empty means the certificate is consistent with
/// itself and with the registry in use.
pub fn verify_certificate(cert: &Certificate, registry_sha256: &str) -> Vec<String> {
    let mut problems = Vec::new();
    if !SUPPORTED_VERSIONS.contains(&cert.exoself.as_str()) {
        problems.push(format!("spec version {:?} is not supported", cert.exoself));
    }
    if cert.id != agent_id(&cert.seed) {
        problems.push("id does not match seed".into());
    }
    if cert.registry_sha256 != registry_sha256 {
        problems.push("registry hash differs from the registry in use".into());
    }
    if let Some(level) = cert.attestation.get("level").and_then(|x| x.as_i64()) {
        if level == 1 {
            let field = |k: &str| {
                cert.attestation
                    .get(k)
                    .and_then(|x| x.as_str())
                    .and_then(unhex)
            };
            match (
                field("local_entropy"),
                field("beacon_value"),
                field("commitment"),
            ) {
                (Some(l), Some(b), Some(c)) => {
                    if local_entropy_len_ok(&l) {
                        if bind_beacon(&l, &b) != Ok(cert.seed) {
                            problems.push("seed does not match beacon binding".into());
                        }
                    } else {
                        problems.push("level 1 attestation is incomplete".into());
                    }
                    if commitment(&l) != hex(&c) {
                        problems.push("commitment does not match local entropy".into());
                    }
                }
                _ => problems.push("level 1 attestation is incomplete".into()),
            }
        }
    }
    problems
}

fn local_entropy_len_ok(l: &[u8]) -> bool {
    l.len() == SEED_BYTES
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seed(n: u8) -> Seed {
        let mut s = [0u8; SEED_BYTES];
        s[0] = n;
        s
    }

    #[test]
    fn certificate_roundtrips_and_detects_tampering() {
        let reg = Registry::bundled("core@0.2").unwrap();
        let cert = born(
            &seed(5),
            &reg,
            "2026-11-10T00:00:00Z",
            attestation_self_asserted(),
        );
        let text = cert.to_json().pretty();
        let back = Certificate::parse(&text).unwrap();
        assert_eq!(back.id, cert.id);
        assert_eq!(back.seed, cert.seed);
        assert!(verify_certificate(&back, &reg.sha256).is_empty());

        let other = Registry::bundled("core@0.1").unwrap();
        assert_eq!(
            verify_certificate(&back, &other.sha256),
            vec!["registry hash differs from the registry in use".to_string()]
        );

        let mut bad = back.clone();
        bad.seed[0] ^= 1;
        assert!(verify_certificate(&bad, &reg.sha256)
            .iter()
            .any(|p| p.contains("id does not match seed")));
    }

    #[test]
    fn beacon_binding_is_bound_to_both_inputs() {
        let l = seed(1);
        let b = seed(2);
        let s = bind_beacon(&l, &b).unwrap();
        assert_eq!(s, bind_beacon(&l, &b).unwrap());
        assert_ne!(s, bind_beacon(&l, &seed(3)).unwrap());
        assert_ne!(s, bind_beacon(&seed(3), &b).unwrap());
        let att = attestation_beacon(&l, "drand:quicknet:42", &b);
        let reg = Registry::bundled("core@0.2").unwrap();
        let cert = born(&s, &reg, "now", att);
        assert!(verify_certificate(&cert, &reg.sha256).is_empty());
    }

    #[test]
    fn os_entropy_is_not_constant() {
        match os_entropy() {
            Ok(a) => {
                let b = os_entropy().unwrap();
                assert_ne!(a, b);
            }
            Err(_) => { /* platform without /dev/urandom: fine */ }
        }
    }
}
