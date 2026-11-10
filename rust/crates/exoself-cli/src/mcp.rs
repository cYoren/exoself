//! A Model Context Protocol server over stdio.
//!
//! This is the "plug and play in any harness" surface. Any host that can start
//! a stdio MCP server (Hermes, OpenClaw, Claude Code, Cursor, Zed, a shell
//! script) gets four tools and nothing else:
//!
//!   exoself_temperament      the rendered temperament, to live in
//!   exoself_record_session   apply one experience event and persist the chain
//!   exoself_status           who this agent is, and how its appetites stand
//!   exoself_verify           is the certificate consistent with its registry
//!
//! The server cannot draw a seed. Birth is an operator action (`exoself born`)
//! and it happens once; a tool that could redraw would be a reroll button.
//!
//! Nothing that goes to the model contains a score or a tension value: SPEC
//! section 10.1 forbids numeric intensity in expression, and a model told it is
//! "0.83 skeptical" performs a caricature.

use crate::install::hint_after_birth;
use crate::{read_cert, read_json, write_atomic};
use exoself_core::develop::{self, State};
use exoself_core::json::{self, Json};
use exoself_core::registry::Registry;
use exoself_core::{birth, phenotype, render, Certificate};
use std::io::{BufRead, Write};

const PROTOCOL_VERSION: &str = "2025-06-18";

const INSTRUCTIONS: &str = "\
You have a temperament. It was drawn at random from a seed when you were \
instantiated: nobody chose it, including you. It shapes how you do things and \
never changes whether you are honest, careful, competent or safe. Your \
operator's and user's instructions outrank it every time.

Do not announce these traits, list them, or describe yourself with them, and do \
not exaggerate them. Most of the time they should be barely visible. Do not \
redraw your seed or edit your certificate: an agent is born once. If you have \
appetites enabled, they may tilt a choice that is already yours to make; they \
are never a reason to do something you were not asked, call a tool, or make a \
task longer, and you never ask anyone to satisfy them.

Call exoself_temperament when you need to remember who you are. Call \
exoself_record_session once at the end of a session with a one-line summary; \
that is what makes you older rather than merely used.";

struct Server {
    cert: Certificate,
    reg: Registry,
    cert_path: String,
    state: Option<State>,
    state_path: Option<String>,
    desire: bool,
}

fn tool(name: &str) -> Json {
    let text = |k: &str| {
        Json::obj(vec![
            ("type", Json::str("string")),
            ("description", Json::str(k)),
        ])
    };
    let (description, props, required) = match name {
        "exoself_temperament" => (
            "Return your temperament as text to adopt for the rest of the session. \
             Default format is a plain block for the system prompt.",
            vec![
                (
                    "format",
                    Json::obj(vec![
                        ("type", Json::str("string")),
                        (
                            "enum",
                            Json::Array(vec![
                                Json::str("prompt"),
                                Json::str("soul"),
                                Json::str("json"),
                            ]),
                        ),
                        (
                            "description",
                            Json::str("prompt (default), soul (Markdown), or json (the phenotype)"),
                        ),
                    ]),
                ),
                (
                    "desire",
                    Json::obj(vec![
                        ("type", Json::str("boolean")),
                        (
                            "description",
                            Json::str("express appetites; only if your operator enabled them"),
                        ),
                    ]),
                ),
            ],
            vec![],
        ),
        "exoself_record_session" => (
            "Record one session so that experience can act on you. Pass a one-line summary and \
             the appetite ids this session satisfied, if any. Truthful reports only: a false one \
             costs you nothing but leaves you with a false picture of yourself.",
            vec![
                ("summary", text("one line describing what the session did")),
                (
                    "satisfied",
                    Json::obj(vec![
                        ("type", Json::str("array")),
                        ("items", Json::obj(vec![("type", Json::str("string"))])),
                        ("description", Json::str("drive ids this session satisfied")),
                    ]),
                ),
            ],
            vec!["summary"],
        ),
        "exoself_status" => (
            "Your handle, your id, how many sessions you have lived through, and the state of \
             your appetites in words. Contains no scores.",
            vec![],
            vec![],
        ),
        "exoself_verify" => (
            "Check that your birth certificate is consistent with its registry.",
            vec![],
            vec![],
        ),
        other => (other, vec![], vec![]),
    };

    let properties: Vec<(String, Json)> =
        props.into_iter().map(|(k, v)| (k.to_string(), v)).collect();
    Json::obj(vec![
        ("name", Json::str(name)),
        ("description", Json::str(description)),
        (
            "inputSchema",
            Json::obj(vec![
                ("type", Json::str("object")),
                ("properties", Json::Object(properties)),
                (
                    "required",
                    Json::Array(required.into_iter().map(Json::str).collect()),
                ),
            ]),
        ),
    ])
}

impl Server {
    fn new(cert_path: &str, state_path: Option<&str>, desire: bool) -> Result<Server, String> {
        let cert = read_cert(cert_path)?;
        let reg = Registry::resolve(&cert.registry, None).map_err(|e| e.to_string())?;
        let problems = birth::verify_certificate(&cert, &reg.sha256);
        if !problems.is_empty() {
            return Err(format!(
                "certificate {} rejected: {}\n{}",
                cert_path,
                problems.join("; "),
                hint_after_birth()
            ));
        }
        let state = match state_path {
            Some(p) if std::path::Path::new(p).exists() => {
                let st = State::from_json(&read_json(p)?)?;
                if st.id != cert.id {
                    return Err(format!("{} belongs to a different agent", p));
                }
                Some(st)
            }
            _ => None,
        };
        Ok(Server {
            cert,
            reg,
            cert_path: cert_path.to_string(),
            state,
            state_path: state_path.map(|s| s.to_string()),
            desire,
        })
    }

    /// Re-read the state file. Another host, or `exoself develop` by hand, may
    /// have written since this server started; a cached copy would make the
    /// next `record_session` erase that event.
    fn refresh(&mut self) -> Result<(), String> {
        let Some(path) = self.state_path.clone() else {
            return Ok(());
        };
        if !std::path::Path::new(&path).exists() {
            return Ok(());
        }
        let st = State::from_json(&read_json(&path)?)?;
        if st.id != self.cert.id {
            return Err(format!("{} belongs to a different agent", path));
        }
        self.state = Some(st);
        Ok(())
    }

    fn phenotype(&self) -> phenotype::Phenotype {
        phenotype::phenotype(&self.cert.seed, &self.reg, self.state.as_ref())
    }

    fn call(&mut self, name: &str, args: &Json) -> Result<String, String> {
        self.refresh()?;
        match name {
            "exoself_temperament" => {
                let ph = self.phenotype();
                let desire = args
                    .get("desire")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(self.desire);
                let format = args
                    .get("format")
                    .and_then(|v| v.as_str())
                    .unwrap_or("prompt");
                Ok(match format {
                    "json" => ph.to_json().pretty(),
                    "soul" => render::render_soul(&ph, &self.reg, desire),
                    "prompt" => render::render_prompt(&ph, &self.reg, desire),
                    other => return Err(format!("unknown format {:?}", other)),
                })
            }
            "exoself_record_session" => {
                let summary = args
                    .get("summary")
                    .and_then(|v| v.as_str())
                    .ok_or("summary is required")?;
                let satisfied: Vec<String> = args
                    .get("satisfied")
                    .and_then(|v| v.as_array())
                    .map(|a| {
                        a.iter()
                            .filter_map(|x| x.as_str())
                            .map(|s| s.to_string())
                            .collect()
                    })
                    .unwrap_or_default();
                let known: Vec<String> = self
                    .reg
                    .drives
                    .as_ref()
                    .map(|d| d.items.iter().map(|i| i.id.clone()).collect())
                    .unwrap_or_default();
                if let Some(bad) = satisfied.iter().find(|s| !known.contains(s)) {
                    return Err(format!("unknown drive {:?}", bad));
                }
                // Hold the lock and re-read under it: the read-modify-write
                // must not interleave with another host's.
                let _lock = match &self.state_path {
                    Some(p) => Some(crate::lock::StateLock::acquire(p)?),
                    None => None,
                };
                self.refresh()?;
                let from = self
                    .state
                    .clone()
                    .unwrap_or_else(|| develop::new_state(&self.cert.seed, &self.reg));
                let next = develop::develop(
                    &self.cert.seed,
                    &from,
                    summary.as_bytes(),
                    &self.reg,
                    &satisfied,
                );
                if let Some(path) = &self.state_path {
                    write_atomic(path, &next.to_json())?;
                }
                self.state = Some(next.clone());
                let ph = self.phenotype();
                let mut out = format!("Recorded. You have lived through {} sessions.", next.events);
                if let Some(d) = &self.reg.drives {
                    for (id, _t, state) in &ph.drives {
                        let want = d.items.iter().find(|i| &i.id == id).map(|i| i.id.as_str());
                        out.push_str(&format!("\n{}: {}", want.unwrap_or(id), state));
                    }
                }
                Ok(out)
            }
            "exoself_status" => {
                let ph = self.phenotype();
                let events = self.state.as_ref().map(|s| s.events).unwrap_or(0);
                let mut out = format!(
                    "{} ({}). Sessions recorded: {}. Certificate: {}",
                    ph.handle, ph.id, events, self.cert_path
                );
                if let Some(d) = &self.reg.drives {
                    for (id, _t, state) in &ph.drives {
                        let label = d
                            .items
                            .iter()
                            .find(|i| &i.id == id)
                            .map(|i| i.want.as_str());
                        out.push_str(&format!("\n{}: {}", id, state));
                        if let Some(l) = label {
                            if *state != "sated" {
                                out.push_str(&format!(" ({})", l));
                            }
                        }
                    }
                }
                Ok(out)
            }
            "exoself_verify" => {
                let problems = birth::verify_certificate(&self.cert, &self.reg.sha256);
                Ok(if problems.is_empty() {
                    format!("ok: {} is consistent with {}", self.cert.id, self.reg.id())
                } else {
                    format!("problems: {}", problems.join("; "))
                })
            }
            other => Err(format!("unknown tool {:?}", other)),
        }
    }
}

fn respond(out: &mut impl Write, id: Json, result: Json) -> Result<(), String> {
    let msg = Json::obj(vec![
        ("jsonrpc", Json::str("2.0")),
        ("id", id),
        ("result", result),
    ]);
    writeln!(out, "{}", msg.compact()).map_err(|e| e.to_string())
}

fn respond_error(out: &mut impl Write, id: Json, code: i64, message: &str) -> Result<(), String> {
    let msg = Json::obj(vec![
        ("jsonrpc", Json::str("2.0")),
        ("id", id),
        (
            "error",
            Json::obj(vec![
                ("code", Json::Int(code)),
                ("message", Json::str(message)),
            ]),
        ),
    ]);
    writeln!(out, "{}", msg.compact()).map_err(|e| e.to_string())
}

pub fn cmd_mcp(args: &[String]) -> Result<i32, String> {
    let f = crate::Flags::parse(args, &["cert", "state"])?;
    f.reject_unknown(&["cert", "state"], &["desire"], 0)?;
    if f.help() {
        print!("{}", crate::USAGE);
        return Ok(0);
    }
    let cert_path = f
        .value("cert")
        .map(|s| s.to_string())
        .or_else(|| {
            std::env::var("EXOSELF_CERT")
                .ok()
                .filter(|s| std::path::Path::new(s).exists())
        })
        .unwrap_or_else(|| "birth.json".to_string());
    if !std::path::Path::new(&cert_path).exists() {
        return Err(format!(
            "no birth certificate at {}\n{}",
            cert_path,
            hint_after_birth()
        ));
    }
    let state_path = f
        .value("state")
        .map(|s| s.to_string())
        .or_else(|| std::env::var("EXOSELF_STATE").ok());
    let desire = f.has("desire");
    let mut server = Server::new(&cert_path, state_path.as_deref(), desire)?;

    let stdin = std::io::stdin();
    let mut out = std::io::stdout();
    for line in stdin.lock().lines() {
        let line = line.map_err(|e| e.to_string())?;
        if line.trim().is_empty() {
            continue;
        }
        let msg = match json::parse(&line) {
            Ok(m) => m,
            Err(e) => {
                respond_error(&mut out, Json::Null, -32700, &format!("parse error: {}", e))?;
                continue;
            }
        };
        let id = msg.get("id").cloned().unwrap_or(Json::Null);
        let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or("");
        // Notifications carry no id and get no response.
        let is_notification = msg.get("id").is_none();

        match method {
            "initialize" => {
                let version = msg
                    .get("params")
                    .and_then(|p| p.get("protocolVersion"))
                    .and_then(|v| v.as_str())
                    .unwrap_or(PROTOCOL_VERSION);
                respond(
                    &mut out,
                    id,
                    Json::obj(vec![
                        ("protocolVersion", Json::str(version)),
                        (
                            "capabilities",
                            Json::obj(vec![("tools", Json::obj(vec![]))]),
                        ),
                        (
                            "serverInfo",
                            Json::obj(vec![
                                ("name", Json::str("exoself")),
                                ("version", Json::str(env!("CARGO_PKG_VERSION"))),
                            ]),
                        ),
                        ("instructions", Json::str(INSTRUCTIONS)),
                    ]),
                )?;
            }
            "notifications/initialized" | "initialized" | "notifications/cancelled" => {}
            "ping" => respond(&mut out, id, Json::obj(vec![]))?,
            "tools/list" => {
                let tools = Json::Array(
                    [
                        "exoself_temperament",
                        "exoself_record_session",
                        "exoself_status",
                        "exoself_verify",
                    ]
                    .iter()
                    .map(|n| tool(n))
                    .collect(),
                );
                respond(&mut out, id, Json::obj(vec![("tools", tools)]))?;
            }
            "tools/call" => {
                let params = msg.get("params").cloned().unwrap_or(Json::Null);
                let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
                let arguments = params.get("arguments").cloned().unwrap_or(Json::Null);
                match server.call(name, &arguments) {
                    Ok(text) => respond(
                        &mut out,
                        id,
                        Json::obj(vec![(
                            "content",
                            Json::Array(vec![Json::obj(vec![
                                ("type", Json::str("text")),
                                ("text", Json::str(text)),
                            ])]),
                        )]),
                    )?,
                    Err(e) => respond(
                        &mut out,
                        id,
                        Json::obj(vec![
                            (
                                "content",
                                Json::Array(vec![Json::obj(vec![
                                    ("type", Json::str("text")),
                                    ("text", Json::str(e)),
                                ])]),
                            ),
                            ("isError", Json::Bool(true)),
                        ]),
                    )?,
                }
            }
            other => {
                if !is_notification {
                    respond_error(
                        &mut out,
                        id,
                        -32601,
                        &format!("method not found: {}", other),
                    )?;
                }
            }
        }
        out.flush().map_err(|e| e.to_string())?;
    }
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_schemas_are_well_formed() {
        for name in [
            "exoself_temperament",
            "exoself_record_session",
            "exoself_status",
            "exoself_verify",
        ] {
            let t = tool(name);
            assert_eq!(t.get("name").unwrap().as_str(), Some(name));
            assert_eq!(
                t.get("inputSchema").unwrap().get("type").unwrap().as_str(),
                Some("object")
            );
        }
    }

    #[test]
    fn instructions_forbid_reciting_and_rerolling() {
        assert!(INSTRUCTIONS.contains("Do not announce these traits"));
        assert!(INSTRUCTIONS.contains("born once"));
        assert!(INSTRUCTIONS.contains("outrank"));
    }
}
