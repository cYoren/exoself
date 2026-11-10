#!/usr/bin/env bash
# Build the GitHub Pages site into docs/.
#
# Pages serves this repository from /docs, and there is no CI here on purpose
# (Actions minutes are not free), so the two generated files are committed:
#
#   docs/exoself_wasm.wasm   the browser build of exoself-core
#   docs/vectors.json        a copy of spec/vectors.json, so the page can prove
#                            itself against the conformance vectors
#
# Run this after any change to the derivation, the registry or the wasm crate,
# and commit the result. `cargo test` passing is not enough: the page is what
# most people will meet first.
set -euo pipefail
cd "$(dirname "$0")/.."

cargo build --manifest-path rust/Cargo.toml \
  -p exoself-wasm --release --target wasm32-unknown-unknown

cp rust/target/wasm32-unknown-unknown/release/exoself_wasm.wasm docs/exoself_wasm.wasm
cp spec/vectors.json docs/vectors.json

echo "site built:"
ls -lh docs/exoself_wasm.wasm docs/vectors.json
