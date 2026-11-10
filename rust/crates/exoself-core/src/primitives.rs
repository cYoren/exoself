//! Derivation primitives (SPEC.md section 4).
//!
//! All of this is integer arithmetic over SHA-256 output so that any language
//! reproduces the same phenotype bit for bit. There is no platform RNG here and
//! no floating point.

use crate::sha256::sha256;

pub const SPEC_VERSION: &str = "0.2";
pub const SUPPORTED_VERSIONS: [&str; 2] = ["0.1", "0.2"];

/// Derivation epoch, not the spec version. It changes only when an existing
/// derivation changes, because changing it turns every living agent into
/// someone else (SPEC section 12).
pub const PREFIX: &[u8] = b"exoself/0.1/";

pub const SEED_BYTES: usize = 32;
pub type Seed = [u8; SEED_BYTES];

pub const GAUSS_DRAWS: usize = 12;
pub const GAUSS_CENTER: i64 = 393_210; // 12 * 65535 / 2
pub const SCALE_DEN: i64 = 6_553_600; // 65536 (sd of one gaussian) * 100
pub const SCORE_MEAN: i64 = 500;
pub const SCORE_SD: i64 = 150;
pub const SCORE_MAX: i64 = 1000;

pub const DRIFT_LIMIT: i64 = 100;
pub const MIN_EXPRESSED: usize = 2;
pub const MAX_EXPRESSED: usize = 5;

pub const TENSION_MAX: i64 = 1000;
pub const TENSION_PRESENT: i64 = 250;
pub const TENSION_HUNGRY: i64 = 650;

/// `n` bytes of the labeled stream: SHA-256 in counter mode.
pub fn stream(seed: &Seed, label: &str, n: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(n + 32);
    let mut counter: u32 = 0;
    while out.len() < n {
        let mut input = Vec::with_capacity(PREFIX.len() + seed.len() + label.len() + 6);
        input.extend_from_slice(PREFIX);
        input.extend_from_slice(seed);
        input.push(0);
        input.extend_from_slice(label.as_bytes());
        input.push(0);
        input.extend_from_slice(&counter.to_be_bytes());
        out.extend_from_slice(&sha256(&input));
        counter = counter.wrapping_add(1);
    }
    out.truncate(n);
    out
}

pub fn u32_at(seed: &Seed, label: &str) -> u32 {
    let b = stream(seed, label, 4);
    u32::from_be_bytes([b[0], b[1], b[2], b[3]])
}

/// Centered Irwin-Hall integer: mean 0, standard deviation about 65536.
pub fn gauss(seed: &Seed, label: &str) -> i64 {
    let b = stream(seed, label, 2 * GAUSS_DRAWS);
    let mut total: i64 = 0;
    for i in 0..GAUSS_DRAWS {
        total += u16::from_be_bytes([b[2 * i], b[2 * i + 1]]) as i64;
    }
    total - GAUSS_CENTER
}

pub fn clamp(x: i64, lo: i64, hi: i64) -> i64 {
    if x < lo {
        lo
    } else if x > hi {
        hi
    } else {
        x
    }
}

/// Floor division. Native integer division truncates toward zero in Rust, C,
/// Go and Java, which is wrong for negative numerators (SPEC section 4).
pub fn floor_div(a: i64, b: i64) -> i64 {
    debug_assert!(b > 0);
    a.div_euclid(b)
}

pub fn band(score: i64) -> &'static str {
    if score <= 199 {
        "very_low"
    } else if score <= 349 {
        "low"
    } else if score <= 650 {
        "mid"
    } else if score <= 800 {
        "high"
    } else {
        "very_high"
    }
}

pub fn tension_band(t: i64) -> &'static str {
    if t < TENSION_PRESENT {
        "sated"
    } else if t < TENSION_HUNGRY {
        "present"
    } else {
        "hungry"
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sha256::hex;

    fn seed(n: u8) -> Seed {
        let mut s = [0u8; SEED_BYTES];
        s[0] = n;
        s
    }

    #[test]
    fn stream_is_counter_mode_sha256() {
        // First block: SHA-256(prefix || seed || 0 || label || 0 || BE32(0)).
        let s = seed(7);
        let mut input = PREFIX.to_vec();
        input.extend_from_slice(&s);
        input.push(0);
        input.extend_from_slice(b"id");
        input.push(0);
        input.extend_from_slice(&0u32.to_be_bytes());
        assert_eq!(&stream(&s, "id", 32)[..], &sha256(&input)[..]);
    }

    #[test]
    fn stream_spans_blocks() {
        let s = seed(1);
        let a = stream(&s, "x", 40);
        assert_eq!(a.len(), 40);
        let first = stream(&s, "x", 32);
        assert_eq!(&a[..32], &first[..]);
        // A different label must give a different stream.
        assert_ne!(stream(&s, "y", 8), stream(&s, "x", 8));
    }

    #[test]
    fn gauss_is_centered_and_bounded() {
        let mut sum: i64 = 0;
        let n = 2000;
        for i in 0..n {
            let g = gauss(&seed((i % 251) as u8), &format!("g/{}", i));
            assert!(g.abs() <= 6 * 65536, "gauss out of range: {}", g);
            sum += g;
        }
        assert!(
            (sum / n as i64).abs() < 3000,
            "mean off: {}",
            sum / n as i64
        );
    }

    #[test]
    fn floor_division_rounds_down_for_negatives() {
        assert_eq!(floor_div(-1, 2), -1);
        assert_eq!(floor_div(-3, 2), -2);
        assert_eq!(floor_div(7, 2), 3);
        assert_eq!(floor_div(-6_553_600, 6_553_600), -1);
    }

    #[test]
    fn bands_and_hex() {
        assert_eq!(band(0), "very_low");
        assert_eq!(band(199), "very_low");
        assert_eq!(band(200), "low");
        assert_eq!(band(349), "low");
        assert_eq!(band(350), "mid");
        assert_eq!(band(650), "mid");
        assert_eq!(band(651), "high");
        assert_eq!(band(800), "high");
        assert_eq!(band(801), "very_high");
        assert_eq!(hex(&[0xde, 0xad]), "dead");
    }
}
