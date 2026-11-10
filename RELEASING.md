# Releasing

Two packages, one version. The version lives in `pyproject.toml`,
`rust/Cargo.toml` (`[workspace.package]`) and `src/exoself/cli.py`
(`VERSION`); they must agree, and `tests/test_exoself.py` does not check that
for you, so check it by hand.

## Before anything

The gates in `AGENTS.md` ("Definition of done") must be green, and
`./tools/build_site.sh` must have been run and committed if the derivation, the
registry or the wasm crate changed.

    cargo test --manifest-path rust/Cargo.toml
    pytest
    python3 tools/parity.py
    python3 tools/mcp_selftest.py
    cargo fmt --all --manifest-path rust/Cargo.toml --check
    (cd rust && cargo clippy --all-targets)

## crates.io

Order matters: the CLI depends on the core, so the core goes first.

    cargo publish --manifest-path rust/Cargo.toml -p exoself-core
    cargo publish --manifest-path rust/Cargo.toml -p exoself

`exoself-wasm` is `publish = false` on purpose: it is the page's build
artefact, not a library.

Check the package without publishing:

    cargo package --manifest-path rust/Cargo.toml -p exoself-core --no-verify

## PyPI

    python3 -m build
    python3 -m twine upload dist/exoself-0.3.0*

The wheel must contain `exoself/registry/core-0.1.json`, `core-0.2.json`,
`core-0.3.json`, `py.typed` and the licence. Check before uploading:

    python3 -c "import zipfile,glob; print(zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist())"

## GitHub

    git tag -a v0.3.0 -m "exoself 0.3"
    git push origin v0.3.0
    gh release create v0.3.0 --title "exoself 0.3" --notes-file <notes>

The release notes are the "what changed" table at the top of `AGENTS.md`, not a
fresh summary.

## Do not

- Publish a version whose `spec/vectors.json` is not the one in the tag. The
  vectors are what other implementations are judged by.
- Publish with a dirty tree, or with `docs/` behind `spec/`.
- Announce a measurement as replication when it is the same protocol at the
  same model size.
