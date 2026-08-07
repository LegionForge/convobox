Vendored from https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/ (2026-08-07).

License: BSD-3-Clause (see `LICENSE` in this directory).

Files: `highlight.min.js` (core, no languages built in as of v10+) plus
one grammar file per language under `languages/` and two themes under
`styles/` (`github.min.css` light, `github-dark.min.css` dark, matching
`index.html`'s existing `prefers-color-scheme` split).

Not installed via npm/a bundler on purpose -- this frontend has no build
step (`docs/ARTIFACT-PANE-SCOPE.md`'s own documented constraint: "No
build step ... plain HTML/JS, no React/Vite"). To update the version,
re-run the same per-file `curl` against a newer cdnjs version tag and
update the version number here and in this NOTICE.
