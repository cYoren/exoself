//! Command line interface: born | show | develop | verify | conformance |
//! commit | install | mcp.
//!
//! Mirrors the reference implementation's flags and output so that a host can
//! swap binaries without noticing. Rendering is byte-identical to the Python
//! implementation, which `tools/parity.py` checks over a few hundred seeds.

mod install;
mod lock;
mod mcp;

use exoself_core::json::{self, Json};
use exoself_core::primitives::{self, Seed};
use exoself_core::registry::{Registry, DEFAULT_REGISTRY};
use exoself_core::sha256::{hex, unhex};
use exoself_core::{birth, conformance, develop, phenotype, render, Certificate, State};
use std::io::Write;
use std::path::Path;

pub(crate) const USAGE: &str = "\
exoself: an unchosen temperament for software agents

  exoself born [-o birth.json] [--registry PATH]
        [--local-entropy HEX] [--beacon ID --beacon-value HEX]
        Draw a seed once and write the birth certificate. Refuses to overwrite.

  exoself show CERT [--state PATH] [--format prompt|soul|json] [--desire]
        Derive the phenotype and render it. Desire is off unless --desire.

  exoself develop CERT STATE --event TEXT [--satisfied DRIVE]...
        Apply one experience event. Writes STATE atomically.

  exoself verify CERT [--registry PATH]
        Check a birth certificate. Exit code is non-zero on any problem.

  exoself conformance [VECTORS] [--registry PATH]
        Reproduce spec/vectors.json. Default: the bundled vectors.

  exoself commit [-o commitment.json] [--local-entropy HEX]
        Print the commitment for a level 1 (beacon-bound) birth.

  exoself install [--dir DIR] [--file AGENTS.md] [--soul SOUL.md]
        [--json-config PATH] [--desire]
        Plug an agent into a directory: certificate, SOUL.md, a managed block in
        AGENTS.md, and optionally an MCP server entry.

  exoself mcp [--cert PATH] [--state PATH] [--desire]
        Speak the Model Context Protocol on stdin/stdout. Tools:
        exoself_temperament, exoself_record_session, exoself_status,
        exoself_verify.

  exoself version

A certificate is never overwritten and a seed is never redrawn by this tool.
SPEC.md is the authority; see AGENTS.md for what an agent should do with this.
";

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = match run(&args) {
        Ok(code) => code,
        Err(e) => {
            eprintln!("{}", e);
            2
        }
    };
    std::process::exit(code);
}

fn run(args: &[String]) -> Result<i32, String> {
    let Some(cmd) = args.first() else {
        print!("{}", USAGE);
        return Ok(0);
    };
    let rest = &args[1..];
    match cmd.as_str() {
        "born" => cmd_born(rest),
        "show" => cmd_show(rest),
        "develop" => cmd_develop(rest),
        "verify" => cmd_verify(rest),
        "conformance" => cmd_conformance(rest),
        "commit" => cmd_commit(rest),
        "install" => install::cmd_install(rest),
        "mcp" => mcp::cmd_mcp(rest),
        "version" | "--version" | "-V" => {
            println!(
                "exoself {} (spec {})",
                env!("CARGO_PKG_VERSION"),
                exoself_core::spec_version()
            );
            Ok(0)
        }
        "help" | "--help" | "-h" => {
            print!("{}", USAGE);
            Ok(0)
        }
        other => Err(format!("unknown command {:?}\n\n{}", other, USAGE)),
    }
}

// --------------------------------------------------------------------------
// Tiny flag parser
// --------------------------------------------------------------------------

#[derive(Default)]
pub struct Flags {
    pub positional: Vec<String>,
    pub values: Vec<(String, String)>,
    pub switches: Vec<String>,
}

impl Flags {
    /// `takes_value` lists the flags that consume the next argument.
    pub fn parse(args: &[String], takes_value: &[&str]) -> Result<Flags, String> {
        let mut f = Flags::default();
        let mut i = 0;
        while i < args.len() {
            let a = &args[i];
            if let Some(name) = a.strip_prefix("--") {
                if let Some((k, v)) = name.split_once('=') {
                    f.values.push((k.to_string(), v.to_string()));
                } else if takes_value.contains(&name) {
                    let v = args
                        .get(i + 1)
                        .ok_or_else(|| format!("--{} needs a value", name))?;
                    f.values.push((name.to_string(), v.clone()));
                    i += 1;
                } else {
                    f.switches.push(name.to_string());
                }
            } else if a.len() > 1 && a.starts_with('-') && !a.starts_with("--") {
                // Short flags. -o is the only one that takes a value.
                let body = &a[1..];
                if body == "o" {
                    let v = args
                        .get(i + 1)
                        .ok_or_else(|| "-o needs a value".to_string())?;
                    f.values.push(("out".to_string(), v.clone()));
                    i += 1;
                } else {
                    f.switches.push(body.to_string());
                }
            } else {
                f.positional.push(a.clone());
            }
            i += 1;
        }
        Ok(f)
    }

    pub fn value(&self, name: &str) -> Option<&str> {
        self.values
            .iter()
            .find(|(k, _)| k == name)
            .map(|(_, v)| v.as_str())
    }

    pub fn all(&self, name: &str) -> Vec<String> {
        self.values
            .iter()
            .filter(|(k, _)| k == name)
            .map(|(_, v)| v.clone())
            .collect()
    }

    pub fn has(&self, name: &str) -> bool {
        self.switches.iter().any(|s| s == name)
    }

    /// Reject anything this command does not understand.
    ///
    /// A typo must never be silently ignored: `--becon` would draw a level 0
    /// seed when the operator asked for a beacon-bound one, and `--formt soul`
    /// would quietly render the wrong format. Both are worse than an error.
    pub fn reject_unknown(
        &self,
        values: &[&str],
        switches: &[&str],
        max_positional: usize,
    ) -> Result<(), String> {
        for (k, _) in &self.values {
            if !values.contains(&k.as_str()) {
                return Err(format!("unknown flag --{}", k));
            }
        }
        for s in &self.switches {
            if !switches.contains(&s.as_str()) && s != "help" && s != "h" {
                return Err(format!(
                    "unknown flag {}",
                    if s.len() == 1 {
                        format!("-{}", s)
                    } else {
                        format!("--{}", s)
                    }
                ));
            }
        }
        if self.positional.len() > max_positional {
            return Err(format!(
                "unexpected argument {:?}",
                self.positional[max_positional]
            ));
        }
        Ok(())
    }

    /// `--help` on any subcommand prints the usage block and exits cleanly.
    pub fn help(&self) -> bool {
        self.has("help") || self.has("h")
    }
}

// --------------------------------------------------------------------------
// Shared helpers
// --------------------------------------------------------------------------

fn read_json(path: &str) -> Result<Json, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {}", path, e))?;
    json::parse(&text).map_err(|e| format!("{}: {}", path, e))
}

fn read_cert(path: &str) -> Result<Certificate, String> {
    Certificate::from_json(&read_json(path)?).map_err(|e| format!("{}: {}", path, e))
}

fn registry_for(
    cert: &Certificate,
    override_path: Option<&str>,
) -> Result<(Registry, Vec<String>), String> {
    let reg = Registry::resolve(&cert.registry, override_path).map_err(|e| e.to_string())?;
    if override_path.is_some() && reg.sha256 != cert.registry_sha256 {
        eprintln!(
            "note: running {} against {}, not the registry it was born with ({})",
            cert.id,
            reg.id(),
            cert.registry
        );
    }
    // The pin is always checked against the registry actually in use, so an
    // override cannot silence it: it shows up as a problem and the run stops.
    let problems = birth::verify_certificate(cert, &reg.sha256);
    Ok((reg, problems))
}

fn iso_now() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    iso8601(secs)
}

/// UTC timestamp in the exact shape the reference implementation writes
/// (`YYYY-MM-DDTHH:MM:SSZ`), without a date library.
pub fn iso8601(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let (y, m, d) = civil_from_days(days);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        y,
        m,
        d,
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

/// Howard Hinnant's days-from-civil, inverted.
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// Write a file that must not be readable by anyone else.
fn write_secret(path: &str, json: &Json) -> Result<(), String> {
    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut f = opts.open(path).map_err(|e| {
        if e.kind() == std::io::ErrorKind::AlreadyExists {
            format!(
                "{} already exists. Refusing to overwrite a commitment.",
                path
            )
        } else {
            format!("{}: {}", path, e)
        }
    })?;
    f.write_all(json.pretty().as_bytes())
        .and_then(|_| f.write_all(b"\n"))
        .map_err(|e| format!("{}: {}", path, e))
}

fn write_new(path: &str, json: &Json) -> Result<(), String> {
    // create_new fails if the file exists: a certificate is never overwritten.
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| {
            if e.kind() == std::io::ErrorKind::AlreadyExists {
                format!(
                    "{} already exists. An agent is born once; refusing to reroll.",
                    path
                )
            } else {
                format!("{}: {}", path, e)
            }
        })?;
    f.write_all(json.pretty().as_bytes())
        .and_then(|_| f.write_all(b"\n"))
        .map_err(|e| format!("{}: {}", path, e))
}

/// Write via a temporary file and a rename, so a crash mid-write cannot lose a
/// chain that took a thousand sessions to build.
pub fn write_atomic(path: &str, json: &Json) -> Result<(), String> {
    let p = Path::new(path);
    let dir = p
        .parent()
        .filter(|d| !d.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let tmp = dir.join(format!(
        ".{}.tmp.{}",
        p.file_name().and_then(|n| n.to_str()).unwrap_or("state"),
        std::process::id()
    ));
    {
        let mut f = std::fs::File::create(&tmp).map_err(|e| format!("{}: {}", tmp.display(), e))?;
        f.write_all(json.pretty().as_bytes())
            .and_then(|_| f.write_all(b"\n"))
            .and_then(|_| f.sync_all())
            .map_err(|e| format!("{}: {}", tmp.display(), e))?;
    }
    std::fs::rename(&tmp, p).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("{}: {}", path, e)
    })
}

fn parse_seed_hex(h: &str) -> Result<Seed, String> {
    let bytes = unhex(h).ok_or("seed is not hex")?;
    if bytes.len() != primitives::SEED_BYTES {
        return Err(format!("seed must be 32 bytes of hex, got {}", bytes.len()));
    }
    let mut s = [0u8; primitives::SEED_BYTES];
    s.copy_from_slice(&bytes);
    Ok(s)
}

// --------------------------------------------------------------------------
// Commands
// --------------------------------------------------------------------------

fn cmd_born(args: &[String]) -> Result<i32, String> {
    let f = Flags::parse(
        args,
        &["out", "registry", "local-entropy", "beacon", "beacon-value"],
    )?;
    f.reject_unknown(
        &["out", "registry", "local-entropy", "beacon", "beacon-value"],
        &[],
        0,
    )?;
    if f.help() {
        print!("{}", USAGE);
        return Ok(0);
    }
    let out = f.value("out").unwrap_or("birth.json").to_string();
    if Path::new(&out).exists() {
        return Err(format!(
            "{} already exists. An agent is born once; refusing to reroll.",
            out
        ));
    }
    let reg = match f.value("registry") {
        Some(p) => Registry::from_path(p).map_err(|e| e.to_string())?,
        None => Registry::bundled(DEFAULT_REGISTRY).map_err(|e| e.to_string())?,
    };

    let beacon_value = f.value("beacon-value");
    let local_entropy = f.value("local-entropy");

    let (seed, attestation) = match (beacon_value, local_entropy) {
        (Some(bv), Some(le)) => {
            let l = parse_seed_hex(le)?;
            let b = unhex(bv).ok_or("--beacon-value is not hex")?;
            let beacon_id = f
                .value("beacon")
                .ok_or("--beacon-value needs --beacon <id>")?;
            let seed = birth::bind_beacon(&l, &b)?;
            (seed, birth::attestation_beacon(&l, beacon_id, &b))
        }
        (Some(_), None) => {
            return Err(
                "--beacon-value needs --local-entropy <64 hex>, the bytes you committed to".into(),
            )
        }
        // A seed the operator chose is not a draw. SPEC 3.2 allows level 0 only
        // from the OS CSPRNG, and an unchosen seed is the point of the
        // framework, so this path is closed rather than documented.
        (None, Some(_)) => {
            return Err(
                "--local-entropy only binds a seed to a beacon round. Pass --beacon <id> and \
                 --beacon-value <hex> from the round, or drop it and let the OS draw. \
                 See `exoself commit`."
                    .into(),
            )
        }
        (None, None) => {
            if f.value("beacon").is_some() {
                return Err(
                    "--beacon needs --beacon-value <hex> from the round, and --local-entropy \
                     <64 hex> from `exoself commit`"
                        .into(),
                );
            }
            (birth::os_entropy()?, birth::attestation_self_asserted())
        }
    };

    let cert = birth::born(&seed, &reg, &iso_now(), attestation);
    write_new(&out, &cert.to_json())?;
    let ph = phenotype::phenotype(&seed, &reg, None);
    println!("{} ({}) written to {}", ph.handle, cert.id, out);
    Ok(0)
}

fn cmd_show(args: &[String]) -> Result<i32, String> {
    let f = Flags::parse(args, &["state", "format", "registry"])?;
    f.reject_unknown(&["state", "format", "registry"], &["desire"], 1)?;
    if f.help() {
        print!("{}", USAGE);
        return Ok(0);
    }
    let path = f
        .positional
        .first()
        .ok_or("usage: exoself show CERT [--state PATH] [--format prompt|soul|json]")?;
    let cert = read_cert(path)?;
    let (reg, problems) = registry_for(&cert, f.value("registry"))?;
    if !problems.is_empty() {
        return Err(format!("certificate rejected: {}", problems.join("; ")));
    }
    let state = match f.value("state") {
        Some(p) => Some(State::from_json(&read_json(p)?)?),
        None => None,
    };
    let ph = phenotype::phenotype(&cert.seed, &reg, state.as_ref());
    let desire = f.has("desire");
    match f.value("format").unwrap_or("prompt") {
        "json" => println!("{}", ph.to_json().pretty()),
        "prompt" => print!("{}", render::render_prompt(&ph, &reg, desire)),
        "soul" => print!("{}", render::render_soul(&ph, &reg, desire)),
        other => return Err(format!("unknown format {:?}", other)),
    }
    Ok(0)
}

fn cmd_develop(args: &[String]) -> Result<i32, String> {
    let f = Flags::parse(args, &["event", "satisfied", "registry"])?;
    f.reject_unknown(&["event", "satisfied", "registry"], &[], 2)?;
    if f.help() {
        print!("{}", USAGE);
        return Ok(0);
    }
    let mut pos = f.positional.iter();
    let cert_path = pos
        .next()
        .ok_or("usage: exoself develop CERT STATE --event TEXT")?;
    let state_path = pos
        .next()
        .ok_or("usage: exoself develop CERT STATE --event TEXT")?;
    let event = f.value("event").ok_or("--event is required")?;
    let cert = read_cert(cert_path)?;
    let (reg, problems) = registry_for(&cert, f.value("registry"))?;
    if !problems.is_empty() {
        return Err(format!("certificate rejected: {}", problems.join("; ")));
    }
    let known: Vec<String> = reg
        .drives
        .as_ref()
        .map(|d| d.items.iter().map(|i| i.id.clone()).collect())
        .unwrap_or_default();
    let satisfied = f.all("satisfied");
    if let Some(bad) = satisfied.iter().find(|s| !known.contains(s)) {
        return Err(format!("unknown drive: {}", bad));
    }

    // Hold the lock across the whole read-modify-write. Two hosts recording a
    // session for the same agent at the same time would otherwise lose one.
    let _lock = lock::StateLock::acquire(state_path)?;
    let existing = Path::new(state_path).exists();
    let state = if existing {
        let st = State::from_json(&read_json(state_path)?)?;
        if st.id != cert.id {
            return Err("state file belongs to a different agent".into());
        }
        st
    } else {
        develop::new_state(&cert.seed, &reg)
    };
    let next = develop::develop(&cert.seed, &state, event.as_bytes(), &reg, &satisfied);
    write_atomic(state_path, &next.to_json())?;
    if !existing {
        eprintln!(
            "note: {} did not exist, so a new history was started",
            state_path
        );
    }
    let drift: Vec<String> = next
        .drift
        .iter()
        .map(|(k, v)| format!("{}:{:+}", k, v))
        .collect();
    let tension: Vec<String> = next
        .tension
        .iter()
        .map(|(k, v)| format!("{}:{}", k, v))
        .collect();
    println!(
        "event {} applied, drift [{}], tension [{}]",
        next.events,
        drift.join(", "),
        tension.join(", ")
    );
    Ok(0)
}

fn cmd_verify(args: &[String]) -> Result<i32, String> {
    let f = Flags::parse(args, &["registry"])?;
    f.reject_unknown(&["registry"], &[], 1)?;
    if f.help() {
        print!("{}", USAGE);
        return Ok(0);
    }
    let path = f.positional.first().ok_or("usage: exoself verify CERT")?;
    let cert = match read_cert(path) {
        Ok(c) => c,
        Err(e) => {
            println!("FAIL {}", e);
            return Ok(1);
        }
    };
    let reg = Registry::resolve(&cert.registry, f.value("registry"));
    let problems = match reg {
        Ok(reg) => birth::verify_certificate(&cert, &reg.sha256),
        Err(e) => vec![e.to_string()],
    };
    for p in &problems {
        println!("FAIL {}", p);
    }
    if problems.is_empty() {
        println!("ok");
        Ok(0)
    } else {
        Ok(1)
    }
}

fn cmd_conformance(args: &[String]) -> Result<i32, String> {
    let f = Flags::parse(args, &["registry"])?;
    f.reject_unknown(&["registry"], &[], 1)?;
    if f.help() {
        print!("{}", USAGE);
        return Ok(0);
    }
    let reg = match f.value("registry") {
        Some(p) => Registry::from_path(p).map_err(|e| e.to_string())?,
        None => Registry::bundled(DEFAULT_REGISTRY).map_err(|e| e.to_string())?,
    };
    let vectors = match f.positional.first() {
        Some(p) => std::fs::read_to_string(p).map_err(|e| format!("{}: {}", p, e))?,
        None => include_str!("../../../../spec/vectors.json").to_string(),
    };
    let report = conformance::run(&vectors, &reg, true)?;
    for failure in &report.failures {
        println!("FAIL {}", failure);
    }
    println!("{}", report.summary());
    Ok(if report.ok() { 0 } else { 1 })
}

/// The half of a commitment that is safe to publish: the hash and nothing that
/// produced it. Kept separate from the entropy on purpose, because a commitment
/// published together with its pre-image proves nothing.
fn commitment_record(c: &str, secret_path: Option<&str>) -> Json {
    let next = match secret_path {
        Some(p) => format!(
            "publish this file. The entropy that opens it is in {} and is not for \
             publication until the beacon round closes: exoself born --beacon <id> \
             --beacon-value <hex> --local-entropy <hex>",
            p
        ),
        None => "publish this file, then: exoself born --beacon <id> \
                 --beacon-value <hex> --local-entropy <hex you supplied>"
            .to_string(),
    };
    Json::obj(vec![
        ("commitment", Json::str(c)),
        ("committed_at", Json::str(iso_now())),
        ("next", Json::str(next)),
    ])
}

fn cmd_commit(args: &[String]) -> Result<i32, String> {
    let f = Flags::parse(args, &["out", "local-entropy", "secret-out"])?;
    f.reject_unknown(&["out", "local-entropy", "secret-out"], &[], 0)?;
    if f.help() {
        print!("{}", USAGE);
        return Ok(0);
    }
    let supplied = f.value("local-entropy").map(parse_seed_hex).transpose()?;
    let local = match supplied {
        Some(l) => l,
        None => birth::os_entropy()?,
    };
    let c = birth::commitment(&local);
    let out = f.value("out").unwrap_or("commitment.json");

    // A caller who passed its own entropy already has it, so there is nothing
    // to keep. Otherwise the secret goes to its own file, mode 0600.
    let secret_path = if supplied.is_some() {
        None
    } else {
        let path = f
            .value("secret-out")
            .map(|p| p.to_string())
            .unwrap_or_else(|| format!("{}.secret.json", out.trim_end_matches(".json")));
        let secret = Json::obj(vec![
            ("commitment", Json::str(&c)),
            ("local_entropy", Json::str(hex(&local))),
            (
                "note",
                Json::str("do not publish this file before the beacon round"),
            ),
        ]);
        write_secret(&path, &secret)?;
        Some(path)
    };

    write_new(out, &commitment_record(&c, secret_path.as_deref()))?;
    println!("commitment {} written to {}", c, out);
    match &secret_path {
        Some(p) => println!("entropy kept in {} (mode 0600, do not publish)", p),
        None => println!("entropy was supplied by you, so nothing was written twice"),
    }
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_commitment_never_carries_its_pre_image() {
        let rec = commitment_record("abc123", Some("commitment.secret.json"));
        let text = rec.pretty();
        assert!(text.contains("abc123"));
        assert!(!text.contains("local_entropy"));
        assert!(!text.contains("pre-image"));
        assert!(text.contains("commitment.secret.json"));
        let supplied = commitment_record("abc123", None).pretty();
        assert!(!supplied.contains("local_entropy"));
        assert!(supplied.contains("you supplied"));
    }

    #[test]
    fn the_secret_file_is_private() {
        let dir = std::env::temp_dir().join(format!("exoself-secret-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("commitment.secret.json");
        let p = path.to_str().unwrap();
        write_secret(p, &Json::obj(vec![("local_entropy", Json::str("00"))])).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(p).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "secret file is readable by others");
        }
        // A second commitment must not overwrite the first secret.
        assert!(write_secret(p, &Json::obj(vec![])).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn iso8601_matches_known_timestamps() {
        assert_eq!(iso8601(0), "1970-01-01T00:00:00Z");
        assert_eq!(iso8601(1_000_000_000), "2001-09-09T01:46:40Z");
        assert_eq!(iso8601(1_767_225_600), "2026-01-01T00:00:00Z");
        assert_eq!(iso8601(1_800_000_000), "2027-01-15T08:00:00Z");
    }

    #[test]
    fn flags_parse_short_and_long() {
        let a: Vec<String> = ["show", "b.json", "--format=soul", "--desire", "-o", "x"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let f = Flags::parse(&a[1..], &["format", "out"]).unwrap();
        assert_eq!(f.positional, vec!["b.json"]);
        assert_eq!(f.value("format"), Some("soul"));
        assert!(f.has("desire"));
        assert_eq!(f.value("out"), Some("x"));
    }

    fn flags(args: &[&str], values: &[&str]) -> Flags {
        let a: Vec<String> = args.iter().map(|s| s.to_string()).collect();
        Flags::parse(&a, values).unwrap()
    }

    #[test]
    fn a_typo_is_never_silently_ignored() {
        // `--becon` would otherwise draw a level 0 seed for an operator who
        // asked for a beacon-bound one. That is the failure this guards.
        let f = flags(&["-o", "b.json", "--becon", "drand:x"], &["out"]);
        let e = f
            .reject_unknown(&["out", "local-entropy", "beacon", "beacon-value"], &[], 0)
            .unwrap_err();
        assert!(e.contains("--becon"), "{}", e);

        let f = flags(&["b.json", "--formt", "soul"], &["format"]);
        let e = f
            .reject_unknown(&["state", "format", "registry"], &["desire"], 1)
            .unwrap_err();
        assert!(e.contains("--formt"), "{}", e);
    }

    #[test]
    fn an_extra_positional_is_an_error() {
        // `--satisfied teach make` is the Python spelling; Rust takes the flag
        // once per drive. Silently dropping `make` would change the history.
        let f = flags(
            &[
                "c.json",
                "s.json",
                "--event",
                "x",
                "--satisfied",
                "teach",
                "make",
            ],
            &["event", "satisfied"],
        );
        let e = f
            .reject_unknown(&["event", "satisfied", "registry"], &[], 2)
            .unwrap_err();
        assert!(e.contains("make"), "{}", e);
    }

    #[test]
    fn known_flags_and_help_pass() {
        let f = flags(&["b.json", "--format", "soul", "--desire"], &["format"]);
        assert!(f
            .reject_unknown(&["state", "format", "registry"], &["desire"], 1)
            .is_ok());
        let f = flags(&["--help"], &[]);
        assert!(f.help());
        assert!(f.reject_unknown(&[], &[], 0).is_ok());
    }
}
