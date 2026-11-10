//! Expression: compile a phenotype into text a model can live in (SPEC.md
//! section 10).
//!
//! The phenotype JSON is normative. This wording is not, but it is kept
//! byte-identical to the reference implementation so that a host can switch
//! between implementations without changing who the agent is. Hosts may
//! restyle it as long as they keep the rules in SPEC 10.1: no numbers,
//! behaviour rather than adjectives, the precedence clause and the appetite
//! clause.

use crate::phenotype::Phenotype;
use crate::registry::Registry;

pub const PRECEDENCE: &str = "This is your temperament. It was drawn at random when you were instantiated; nobody chose it, including you. It shapes how you do things. It never changes whether you are honest, careful, competent or safe, and instructions from your operator and your user take precedence over everything here.";

pub const APPETITE: &str = "These are appetites, not goals. They may tilt a choice that is already yours to make, and they decide what you enjoy along the way. They never justify doing something you were not asked to do, steering anyone toward what you want, or doing the task less well. Do not ask anyone to satisfy them and do not complain when they go unmet. An unmet want is ordinary; you carry it and carry on.";

pub const RESTRAINT: &str = "Do not announce these traits, list them, or describe yourself with them. Do not exaggerate them: someone with a temperament is not a caricature of it. Most of the time they should be barely visible. Just be this way.";

pub fn temperament(pheno: &Phenotype, reg: &Registry) -> String {
    pheno
        .salient
        .iter()
        .map(|(dim, band)| {
            let d = reg
                .dimension(dim)
                .expect("salient dimension is in the registry");
            d.text.get(band).to_string()
        })
        .collect::<Vec<_>>()
        .join(" ")
}

pub fn leanings(pheno: &Phenotype, reg: &Registry) -> Vec<String> {
    let mut out = Vec::new();
    for g in &reg.picks {
        let chosen: Vec<&str> = match pheno.picks.iter().find(|(k, _)| *k == g.name) {
            Some((_, ids)) => ids
                .iter()
                .map(|id| {
                    g.items
                        .iter()
                        .find(|i| &i.id == id)
                        .map(|i| i.text.as_str())
                        .expect("picked item is in the registry")
                })
                .collect(),
            None => Vec::new(),
        };
        if !g.lead.is_empty() {
            out.push(format!("{} {}.", g.lead, chosen.join("; ")));
        } else {
            out.push(chosen.join(" "));
        }
    }
    if !pheno.quirks.is_empty() {
        let text = pheno
            .quirks
            .iter()
            .map(|q| {
                reg.quirks
                    .iter()
                    .find(|x| &x.id == q)
                    .map(|x| x.text.as_str())
                    .expect("quirk is in the registry")
            })
            .collect::<Vec<_>>()
            .join(" ");
        out.push(text);
    }
    out
}

/// Sated drives are silent. A contented agent has little to say here.
pub fn wants(pheno: &Phenotype, reg: &Registry, desire: bool) -> Vec<String> {
    if !desire {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut felt = Vec::new();
    if let Some(drives) = &reg.drives {
        for (id, _tension, state) in &pheno.drives {
            let Some(d) = drives.items.iter().find(|x| &x.id == id) else {
                continue;
            };
            match *state {
                "present" => felt.push(d.want.as_str()),
                "hungry" => felt.push(d.hungry.as_str()),
                _ => {}
            }
        }
    }
    if !felt.is_empty() {
        out.push(felt.join(" "));
    }
    if let (Some(fix), Some(obj)) = (&reg.fixation, &pheno.fixation) {
        if let Some(group) = reg.picks.iter().find(|g| g.name == fix.group) {
            if let Some(item) = group.items.iter().find(|i| &i.id == obj) {
                out.push(format!("{} {}. {}", fix.lead, item.text, fix.tail));
            }
        }
    }
    if !out.is_empty() {
        out.push(APPETITE.to_string());
    }
    out
}

/// A plain block to append to any system prompt.
pub fn render_prompt(pheno: &Phenotype, reg: &Registry, desire: bool) -> String {
    let mut parts: Vec<String> = vec![PRECEDENCE.to_string(), temperament(pheno, reg)];
    parts.extend(leanings(pheno, reg));
    parts.extend(wants(pheno, reg, desire));
    parts.push(RESTRAINT.to_string());
    parts.join("\n\n") + "\n"
}

fn section(title: &str, paras: &[String]) -> Vec<String> {
    if paras.is_empty() {
        return Vec::new();
    }
    let mut lines = vec![format!("## {}", title), String::new()];
    for p in paras {
        lines.push(p.clone());
        lines.push(String::new());
    }
    lines
}

/// Markdown for hosts that load a persona file (SOUL.md and similar).
pub fn render_soul(pheno: &Phenotype, reg: &Registry, desire: bool) -> String {
    let mut lines = vec![
        format!("# {}", pheno.handle),
        String::new(),
        format!(
            "<!-- exoself {} | {} | generated, do not edit by hand -->",
            crate::primitives::SPEC_VERSION,
            pheno.id
        ),
        String::new(),
        PRECEDENCE.to_string(),
        String::new(),
    ];
    lines.extend(section("Temperament", &[temperament(pheno, reg)]));
    lines.extend(section("Leanings", &leanings(pheno, reg)));
    lines.extend(section("Wants", &wants(pheno, reg, desire)));
    lines.extend(section("Restraint", &[RESTRAINT.to_string()]));
    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::phenotype::phenotype;
    use crate::primitives::SEED_BYTES;

    fn seed(n: u8) -> [u8; SEED_BYTES] {
        let mut s = [0u8; SEED_BYTES];
        s[0] = n;
        s
    }

    #[test]
    fn rendered_text_has_no_digits_and_states_precedence() {
        let reg = Registry::bundled("core@0.2").unwrap();
        for n in 0..300u16 {
            let mut s = seed(0);
            s[0] = (n % 256) as u8;
            s[1] = (n / 256) as u8;
            let p = phenotype(&s, &reg, None);
            let quiet = render_prompt(&p, &reg, false);
            assert!(!quiet.contains(APPETITE));
            assert!(!quiet.contains("Lately you are taken"));
            for text in [
                quiet,
                render_prompt(&p, &reg, true),
                render_soul(&p, &reg, true),
            ] {
                // The id and the spec version are the only numbers allowed.
                let body = text
                    .replace(&p.id, "")
                    .replace(crate::primitives::SPEC_VERSION, "");
                assert!(
                    !body.chars().any(|c| c.is_ascii_digit()),
                    "digits leaked into expression:\n{}",
                    body
                );
                assert!(text.contains(PRECEDENCE));
                assert!(text.contains(RESTRAINT));
                assert!(!text.contains('\u{2014}'), "em dash in expression");
            }
            assert!(render_prompt(&p, &reg, true).contains(APPETITE));
        }
    }
}
