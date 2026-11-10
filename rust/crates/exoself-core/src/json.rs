//! A small JSON value, parser and printer.
//!
//! In-crate to keep the derivation dependency-free, and because part of
//! conformance is byte-identical output: `pretty` reproduces Python's
//! `json.dumps(value, indent=2)` exactly, which is what the reference
//! implementation writes. Objects keep insertion order.

use std::fmt::Write as _;

#[derive(Debug, Clone, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Array(Vec<Json>),
    Object(Vec<(String, Json)>),
}

impl Json {
    pub fn obj(pairs: Vec<(&str, Json)>) -> Json {
        Json::Object(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
    }

    pub fn str<S: AsRef<str>>(s: S) -> Json {
        Json::Str(s.as_ref().to_string())
    }

    pub fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Object(pairs) => pairs.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Json::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Json::Int(i) => Some(*i),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&[Json]> {
        match self {
            Json::Array(a) => Some(a),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Json::Bool(b) => Some(*b),
            _ => None,
        }
    }

    /// Total order over JSON values, used where the spec says "ties broken by
    /// ascending id". Python compares strings by code point; `str` ordering in
    /// Rust is byte order, which agrees for ASCII ids.
    pub fn as_u8_span(&self) -> Option<&str> {
        self.as_str()
    }

    pub fn pretty(&self) -> String {
        let mut out = String::new();
        write_value(&mut out, self, 0, true);
        out
    }

    pub fn compact(&self) -> String {
        let mut out = String::new();
        write_value(&mut out, self, 0, false);
        out
    }
}

fn write_value(out: &mut String, v: &Json, depth: usize, pretty: bool) {
    match v {
        Json::Null => out.push_str("null"),
        Json::Bool(true) => out.push_str("true"),
        Json::Bool(false) => out.push_str("false"),
        Json::Int(i) => {
            let _ = write!(out, "{}", i);
        }
        Json::Str(s) => write_string(out, s),
        Json::Array(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                    if !pretty {
                        // no space, matching json.dumps(separators=(",", ":"))
                    }
                }
                if pretty {
                    out.push('\n');
                    indent(out, depth + 1);
                }
                write_value(out, item, depth + 1, pretty);
            }
            if pretty {
                out.push('\n');
                indent(out, depth);
            }
            out.push(']');
        }
        Json::Object(pairs) => {
            if pairs.is_empty() {
                out.push_str("{}");
                return;
            }
            out.push('{');
            for (i, (k, val)) in pairs.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                if pretty {
                    out.push('\n');
                    indent(out, depth + 1);
                }
                write_string(out, k);
                out.push(':');
                if pretty {
                    out.push(' ');
                }
                write_value(out, val, depth + 1, pretty);
            }
            if pretty {
                out.push('\n');
                indent(out, depth);
            }
            out.push('}');
        }
    }
}

fn indent(out: &mut String, depth: usize) {
    for _ in 0..depth {
        out.push_str("  ");
    }
}

fn write_string(out: &mut String, s: &str) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    let _ = write!(out, "\\u{:04x}", unit);
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

pub fn parse(text: &str) -> Result<Json, String> {
    let bytes = text.as_bytes();
    let mut p = Parser { b: bytes, i: 0 };
    let v = p.value()?;
    p.ws();
    if p.i != bytes.len() {
        return Err(format!("trailing input at byte {}", p.i));
    }
    Ok(v)
}

struct Parser<'a> {
    b: &'a [u8],
    i: usize,
}

impl<'a> Parser<'a> {
    fn ws(&mut self) {
        while self.i < self.b.len() && matches!(self.b[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }

    fn peek(&self) -> Option<u8> {
        self.b.get(self.i).copied()
    }

    fn expect(&mut self, c: u8) -> Result<(), String> {
        if self.peek() == Some(c) {
            self.i += 1;
            Ok(())
        } else {
            Err(format!("expected {:?} at byte {}", c as char, self.i))
        }
    }

    fn lit(&mut self, s: &str, v: Json) -> Result<Json, String> {
        if self.b[self.i..].starts_with(s.as_bytes()) {
            self.i += s.len();
            Ok(v)
        } else {
            Err(format!("bad literal at byte {}", self.i))
        }
    }

    fn value(&mut self) -> Result<Json, String> {
        self.ws();
        match self.peek() {
            None => Err("unexpected end of input".into()),
            Some(b'{') => self.object(),
            Some(b'[') => self.array(),
            Some(b'"') => Ok(Json::Str(self.string()?)),
            Some(b't') => self.lit("true", Json::Bool(true)),
            Some(b'f') => self.lit("false", Json::Bool(false)),
            Some(b'n') => self.lit("null", Json::Null),
            Some(c) if c == b'-' || c.is_ascii_digit() => self.number(),
            Some(c) => Err(format!("unexpected byte {:?} at {}", c as char, self.i)),
        }
    }

    fn object(&mut self) -> Result<Json, String> {
        self.expect(b'{')?;
        let mut pairs = Vec::new();
        self.ws();
        if self.peek() == Some(b'}') {
            self.i += 1;
            return Ok(Json::Object(pairs));
        }
        loop {
            self.ws();
            let k = self.string()?;
            self.ws();
            self.expect(b':')?;
            let v = self.value()?;
            pairs.push((k, v));
            self.ws();
            match self.peek() {
                Some(b',') => {
                    self.i += 1;
                }
                Some(b'}') => {
                    self.i += 1;
                    return Ok(Json::Object(pairs));
                }
                _ => return Err(format!("expected , or }} at byte {}", self.i)),
            }
        }
    }

    fn array(&mut self) -> Result<Json, String> {
        self.expect(b'[')?;
        let mut items = Vec::new();
        self.ws();
        if self.peek() == Some(b']') {
            self.i += 1;
            return Ok(Json::Array(items));
        }
        loop {
            items.push(self.value()?);
            self.ws();
            match self.peek() {
                Some(b',') => {
                    self.i += 1;
                }
                Some(b']') => {
                    self.i += 1;
                    return Ok(Json::Array(items));
                }
                _ => return Err(format!("expected , or ] at byte {}", self.i)),
            }
        }
    }

    fn string(&mut self) -> Result<String, String> {
        self.expect(b'"')?;
        let mut s = String::new();
        loop {
            let c = self.peek().ok_or("unterminated string")?;
            self.i += 1;
            match c {
                b'"' => return Ok(s),
                b'\\' => {
                    let e = self.peek().ok_or("unterminated escape")?;
                    self.i += 1;
                    match e {
                        b'"' => s.push('"'),
                        b'\\' => s.push('\\'),
                        b'/' => s.push('/'),
                        b'b' => s.push('\u{08}'),
                        b'f' => s.push('\u{0c}'),
                        b'n' => s.push('\n'),
                        b'r' => s.push('\r'),
                        b't' => s.push('\t'),
                        b'u' => {
                            let hi = self.hex4()?;
                            let cp: u32 = if (0xd800..0xdc00).contains(&hi) {
                                // High surrogate: consume the low half if present.
                                if self.peek() == Some(b'\\')
                                    && self.b.get(self.i + 1) == Some(&b'u')
                                {
                                    self.i += 2;
                                    let lo = self.hex4()? as u32;
                                    0x10000
                                        + (((hi as u32) - 0xd800) << 10)
                                        + (lo.wrapping_sub(0xdc00) & 0x3ff)
                                } else {
                                    hi as u32
                                }
                            } else {
                                hi as u32
                            };
                            s.push(
                                char::from_u32(cp)
                                    .ok_or_else(|| format!("bad \\u escape {:#06x}", cp))?,
                            );
                        }
                        other => return Err(format!("bad escape \\{}", other as char)),
                    }
                }
                c if c < 0x20 => return Err("control character in string".into()),
                c if c < 0x80 => s.push(c as char),
                _ => {
                    // Re-decode the UTF-8 sequence from the source bytes.
                    let start = self.i - 1;
                    let mut end = self.i;
                    while end < self.b.len() && (self.b[end] & 0xc0) == 0x80 {
                        end += 1;
                    }
                    let chunk = std::str::from_utf8(&self.b[start..end])
                        .map_err(|_| "invalid utf-8 in string".to_string())?;
                    s.push_str(chunk);
                    self.i = end;
                }
            }
        }
    }

    fn hex4(&mut self) -> Result<u16, String> {
        if self.i + 4 > self.b.len() {
            return Err("truncated \\u escape".into());
        }
        let s = std::str::from_utf8(&self.b[self.i..self.i + 4]).map_err(|_| "bad \\u escape")?;
        let v = u16::from_str_radix(s, 16).map_err(|_| format!("bad \\u escape {:?}", s))?;
        self.i += 4;
        Ok(v)
    }

    fn number(&mut self) -> Result<Json, String> {
        let start = self.i;
        if self.peek() == Some(b'-') {
            self.i += 1;
        }
        while matches!(self.peek(), Some(c) if c.is_ascii_digit()) {
            self.i += 1;
        }
        if matches!(self.peek(), Some(b'.') | Some(b'e') | Some(b'E')) {
            return Err(
                "fractional numbers are not supported; the registry and certificates are integers"
                    .into(),
            );
        }
        let s = std::str::from_utf8(&self.b[start..self.i]).map_err(|_| "bad number")?;
        s.parse::<i64>()
            .map(Json::Int)
            .map_err(|_| format!("bad integer {:?}", s))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pretty_matches_python_json_dumps_indent_2() {
        let v = Json::obj(vec![
            ("a", Json::Int(1)),
            ("b", Json::Array(vec![Json::Int(1), Json::Int(2)])),
            ("c", Json::Object(vec![])),
            ("d", Json::str("x\"y\\z\n")),
            ("e", Json::Null),
        ]);
        let want = "{\n  \"a\": 1,\n  \"b\": [\n    1,\n    2\n  ],\n  \"c\": {},\n  \"d\": \"x\\\"y\\\\z\\n\",\n  \"e\": null\n}";
        assert_eq!(v.pretty(), want);
    }

    #[test]
    fn roundtrip_and_order() {
        let src = r#"{"z":1,"a":[true,false,null,"\u00e9"],"m":-7}"#;
        let v = parse(src).unwrap();
        assert_eq!(v.compact(), src);
        let keys: Vec<&str> = match &v {
            Json::Object(p) => p.iter().map(|(k, _)| k.as_str()).collect(),
            _ => panic!(),
        };
        assert_eq!(keys, vec!["z", "a", "m"]);
    }

    #[test]
    fn rejects_floats_and_junk() {
        assert!(parse("1.5").is_err());
        assert!(parse("{\"a\":1} x").is_err());
        assert!(parse("").is_err());
    }

    #[test]
    fn escapes_are_python_compatible() {
        assert_eq!(Json::str("é").compact(), "\"\\u00e9\"");
        assert_eq!(Json::str("\u{1}").compact(), "\"\\u0001\"");
        assert_eq!(Json::str("😀").compact(), "\"\\ud83d\\ude00\"");
    }
}
