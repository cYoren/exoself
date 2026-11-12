#!/usr/bin/env bash
# Build the GitHub Pages site into docs/.
#
# The wasm is NOT committed: `.github/workflows/site.yml` runs this script on
# every change to the derivation, attests the resulting binary with
# `actions/attest-build-provenance`, and publishes docs/ as the Pages artifact.
# Run it locally to preview the site; `docs/exoself_wasm.wasm` is gitignored.
#
#   docs/exoself_wasm.wasm   the browser build of exoself-core (gitignored)
#   docs/vectors.json        a copy of spec/vectors.json, so the page can prove
#                            itself against the conformance vectors (committed)
set -euo pipefail
cd "$(dirname "$0")/.."

cargo build --manifest-path rust/Cargo.toml \
  -p exoself-wasm --release --target wasm32-unknown-unknown

cp rust/target/wasm32-unknown-unknown/release/exoself_wasm.wasm docs/exoself_wasm.wasm
cp spec/vectors.json docs/vectors.json

echo "site built:"
ls -lh docs/exoself_wasm.wasm docs/vectors.json
