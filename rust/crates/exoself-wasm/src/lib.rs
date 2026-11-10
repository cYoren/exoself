//! WebAssembly surface for the browser build.
//!
//! One exported entry point and one request/response shape, so the page needs
//! about forty lines of glue and no bundler:
//!
//! ```text
//! ptr = exoself_wasm_alloc(len)
//! write request bytes at ptr
//! res = exoself_wasm_request(ptr, len)   // [u32 little endian length][json]
//! read the length, then the JSON
//! exoself_wasm_free(res, 4 + length)
//! ```
//!
//! The derivation itself is `exoself-core`, the same crate `exoself` (the CLI)
//! and the conformance vectors use. The browser cannot draw from an OS CSPRNG,
//! so it passes `crypto.getRandomValues` bytes in; everything after that is
//! deterministic and verifiable in the page.

use exoself_core::birth::{self, attestation_beacon, attestation_supplied};
use exoself_core::conformance;
use exoself_core::develop::{self, State};
use exoself_core::json::{self, Json};
use exoself_core::primitives::{self, Seed};
use exoself_core::registry::{Registry, DEFAULT_REGISTRY};
use exoself_core::sha256::{hex, unhex};
use exoself_core::{phenotype, render, Certificate};

fn ok(pairs: Vec<(&str, Json)>) -> Json {
    let mut out: Vec<(String, Json)> = vec![("ok".to_string(), Json::Bool(true))];
    out.extend(pairs.into_iter().map(|(k, v)| (k.to_string(), v)));
    Json::Object(out)
}

fn error(msg: impl std::fmt::Display) -> Json {
    Json::obj(vec![
        ("ok", Json::Bool(false)),
        ("error", Json::str(msg.to_string())),
    ])
}

fn seed_from(req: &Json) -> Result<Seed, String> {
    let h = req
        .get("seed")
        .and_then(|v| v.as_str())
        .ok_or("missing seed (64 hex characters)")?;
    let bytes = unhex(h).ok_or("seed is not hex")?;
    if bytes.len() != primitives::SEED_BYTES {
        return Err("seed must be exactly 32 bytes of hex".into());
    }
    let mut s = [0u8; primitives::SEED_BYTES];
    s.copy_from_slice(&bytes);
    Ok(s)
}

fn registry_of(req: &Json) -> Result<Registry, String> {
    match req.get("registry").and_then(|v| v.as_str()) {
        Some("core@0.1") => Registry::bundled("core@0.1").map_err(|e| e.to_string()),
        Some(raw) if raw.trim_start().starts_with('{') => {
            Registry::from_bytes(raw.to_string()).map_err(|e| e.to_string())
        }
        _ => Registry::bundled(DEFAULT_REGISTRY).map_err(|e| e.to_string()),
    }
}

fn state_of(req: &Json) -> Result<Option<State>, String> {
    match req.get("state") {
        Some(Json::Null) | None => Ok(None),
        Some(v) => Ok(Some(State::from_json(v)?)),
    }
}

fn handle(text: &str) -> Json {
    let req = match json::parse(text) {
        Ok(r) => r,
        Err(e) => return error(format!("bad request: {}", e)),
    };
    let op = req.get("op").and_then(|v| v.as_str()).unwrap_or("");
    let result = (|| -> Result<Json, String> {
        match op {
            "registry" => {
                let reg = registry_of(&req)?;
                Ok(ok(vec![
                    ("id", Json::str(reg.id())),
                    ("sha256", Json::str(&reg.sha256)),
                    ("spec", Json::str(primitives::SPEC_VERSION)),
                    ("raw", Json::str(reg.raw.clone())),
                ]))
            }
            "phenotype" | "prompt" | "soul" | "develop" => {
                let reg = registry_of(&req)?;
                let seed = seed_from(&req)?;
                let desire = req.get("desire").and_then(|v| v.as_bool()).unwrap_or(false);
                let mut state = state_of(&req)?;
                if op == "develop" {
                    let event = req
                        .get("event")
                        .and_then(|v| v.as_str())
                        .ok_or("missing event")?;
                    let satisfied: Vec<String> = req
                        .get("satisfied")
                        .and_then(|v| v.as_array())
                        .map(|a| {
                            a.iter()
                                .filter_map(|x| x.as_str())
                                .map(|s| s.to_string())
                                .collect()
                        })
                        .unwrap_or_default();
                    let from = state
                        .clone()
                        .unwrap_or_else(|| develop::new_state(&seed, &reg));
                    state = Some(develop::develop(
                        &seed,
                        &from,
                        event.as_bytes(),
                        &reg,
                        &satisfied,
                    ));
                }
                let ph = phenotype::phenotype(&seed, &reg, state.as_ref());
                let payload = match op {
                    "phenotype" => ph.to_json(),
                    "prompt" => Json::str(render::render_prompt(&ph, &reg, desire)),
                    "soul" => Json::str(render::render_soul(&ph, &reg, desire)),
                    _ => Json::Null,
                };
                let mut pairs = vec![("phenotype", ph.to_json()), ("payload", payload)];
                if let Some(st) = &state {
                    pairs.push(("state", st.to_json()));
                }
                Ok(ok(pairs))
            }
            "born" => {
                let reg = registry_of(&req)?;
                let seed = seed_from(&req)?;
                let born_at = req
                    .get("born_at")
                    .and_then(|v| v.as_str())
                    .unwrap_or("1970-01-01T00:00:00Z");
                let attestation = match (
                    req.get("local_entropy").and_then(|v| v.as_str()),
                    req.get("beacon_value").and_then(|v| v.as_str()),
                ) {
                    (Some(le), Some(bv)) => {
                        let l = unhex(le).ok_or("local_entropy is not hex")?;
                        let b = unhex(bv).ok_or("beacon_value is not hex")?;
                        let id = req
                            .get("beacon")
                            .and_then(|v| v.as_str())
                            .unwrap_or("unattested");
                        attestation_beacon(&l, id, &b)
                    }
                    _ => attestation_supplied(),
                };
                let cert = birth::born(&seed, &reg, born_at, attestation);
                Ok(ok(vec![
                    ("certificate", cert.to_json()),
                    ("registry_sha256", Json::str(&reg.sha256)),
                ]))
            }
            "verify" => {
                let cert_json = req.get("certificate").cloned().unwrap_or(Json::Null);
                let cert = Certificate::from_json(&cert_json)?;
                let reg = Registry::bundled(&cert.registry).map_err(|e| e.to_string())?;
                let problems = birth::verify_certificate(&cert, &reg.sha256);
                Ok(Json::obj(vec![
                    ("ok", Json::Bool(problems.is_empty())),
                    (
                        "problems",
                        Json::Array(problems.iter().map(Json::str).collect()),
                    ),
                ]))
            }
            "commit" => {
                let le = req
                    .get("local_entropy")
                    .and_then(|v| v.as_str())
                    .ok_or("missing local_entropy")?;
                let l = unhex(le).ok_or("local_entropy is not hex")?;
                Ok(ok(vec![
                    ("commitment", Json::str(birth::commitment(&l))),
                    ("local_entropy", Json::str(hex(&l))),
                ]))
            }
            "conformance" => {
                let reg = registry_of(&req)?;
                let vectors = req
                    .get("vectors")
                    .and_then(|v| v.as_str())
                    .ok_or("missing vectors")?;
                let primitives = req
                    .get("primitives")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(true);
                let report = conformance::run(vectors, &reg, primitives)?;
                Ok(Json::obj(vec![
                    ("ok", Json::Bool(report.ok())),
                    ("passed", Json::Int(report.passed as i64)),
                    ("failed", Json::Int(report.failed as i64)),
                    ("summary", Json::str(report.summary())),
                    (
                        "failures",
                        Json::Array(report.failures.iter().map(Json::str).collect()),
                    ),
                ]))
            }
            other => Err(format!("unknown op {:?}", other)),
        }
    })();
    result.unwrap_or_else(error)
}

/// Allocate `len` bytes for the caller to write a request into.
#[no_mangle]
pub extern "C" fn exoself_wasm_alloc(len: usize) -> *mut u8 {
    let mut v = Vec::<u8>::with_capacity(len.max(1));
    let ptr = v.as_mut_ptr();
    std::mem::forget(v);
    ptr
}

/// # Safety
///
/// `ptr` and `len` must be exactly what `exoself_wasm_alloc` or
/// `exoself_wasm_request` returned, and must not be freed twice.
#[no_mangle]
pub unsafe extern "C" fn exoself_wasm_free(ptr: *mut u8, len: usize) {
    if ptr.is_null() {
        return;
    }
    drop(Vec::from_raw_parts(ptr, 0, len.max(1)));
}

/// Handle one request. The returned buffer starts with a four byte little
/// endian payload length, then the JSON response. Free it with
/// `exoself_wasm_free(ptr, 4 + length)`.
///
/// # Safety
///
/// `ptr` must point to `len` readable bytes owned by the caller, as produced by
/// `exoself_wasm_alloc` and filled by the JavaScript glue.
#[no_mangle]
pub unsafe extern "C" fn exoself_wasm_request(ptr: *const u8, len: usize) -> *mut u8 {
    let input = if ptr.is_null() || len == 0 {
        ""
    } else {
        std::str::from_utf8(std::slice::from_raw_parts(ptr, len)).unwrap_or("")
    };
    let response = handle(input).compact();
    let mut out = Vec::with_capacity(4 + response.len());
    out.extend_from_slice(&(response.len() as u32).to_le_bytes());
    out.extend_from_slice(response.as_bytes());
    let p = out.as_mut_ptr();
    std::mem::forget(out);
    p
}

/// The spec version this build implements, for the page footer.
#[no_mangle]
pub extern "C" fn exoself_wasm_spec_version() -> u32 {
    let (major, minor) = primitives::SPEC_VERSION
        .split_once('.')
        .unwrap_or(("0", "0"));
    (major.parse::<u32>().unwrap_or(0) << 16) | minor.parse::<u32>().unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn phenotype_request_works() {
        let r = handle(
            r#"{"op":"phenotype","seed":"0000000000000000000000000000000000000000000000000000000000000000"}"#,
        );
        assert_eq!(r.get("ok").unwrap().as_bool(), Some(true));
        assert!(r.get("phenotype").unwrap().get("handle").is_some());
    }

    #[test]
    fn bad_requests_are_errors_not_panics() {
        assert_eq!(handle("not json").get("ok").unwrap().as_bool(), Some(false));
        assert_eq!(
            handle(r#"{"op":"nope"}"#).get("ok").unwrap().as_bool(),
            Some(false)
        );
        assert_eq!(
            handle(r#"{"op":"phenotype","seed":"ab"}"#)
                .get("ok")
                .unwrap()
                .as_bool(),
            Some(false)
        );
    }

    #[test]
    fn conformance_runs_in_the_browser_build() {
        let vectors = exoself_core::conformance::bundled_vectors();
        let req = Json::obj(vec![
            ("op", Json::str("conformance")),
            ("vectors", Json::str(vectors)),
        ]);
        let r = handle(&req.compact());
        assert_eq!(
            r.get("ok").unwrap().as_bool(),
            Some(true),
            "{}",
            r.compact()
        );
        assert!(r.get("passed").unwrap().as_i64().unwrap() >= 20);
    }
}
