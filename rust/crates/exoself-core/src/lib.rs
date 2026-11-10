//! exoself, in Rust.
//!
//! An unchosen temperament for software agents. One 32 byte seed, drawn once,
//! deterministically expanded into a genome, a phenotype and the text a model
//! reads. `SPEC.md` is the authority; this crate is one implementation of it and
//! exists to prove the spec is implementable outside Python, bit for bit.
//!
//! ```text
//! seed -> genome -> phenotype (+ development) -> expression
//! ```
//!
//! No dependencies, no floating point, no platform RNG after the seed is drawn.
//! `cargo test` includes the conformance vectors, so a change that alters who
//! an agent is fails before it can be released.

pub mod birth;
pub mod conformance;
pub mod develop;
pub mod genome;
pub mod json;
pub mod phenotype;
pub mod primitives;
pub mod registry;
pub mod render;
pub mod sha256;

pub use birth::{verify_certificate, Certificate};
pub use conformance::Report;
pub use develop::State;
pub use phenotype::Phenotype;
pub use primitives::Seed;
pub use registry::Registry;

/// Version of the specification this crate implements.
pub fn spec_version() -> &'static str {
    primitives::SPEC_VERSION
}

/// The derivation epoch embedded in every hash input. It is not the spec
/// version and it only moves if an existing derivation has to change.
pub fn derivation_epoch() -> &'static str {
    std::str::from_utf8(primitives::PREFIX)
        .unwrap()
        .trim_end_matches('/')
}
