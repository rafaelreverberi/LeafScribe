# Contributing

LeafScribe’s independently authored code uses the Personal Use License in LICENSE. Contributions to that code must be provided under the same terms; do not submit code you cannot license that way. Vendored components keep their upstream licenses.

Build with `Scripts/build_app.sh` and run the checks in README.md. Keep changes focused and document source-to-output behavior and validation boundaries.

Do not commit local Beook databases, downloaded books, private notes, covers, exported publisher content, or credentials. Use original fixtures and keep generated verification artifacts in ignored `output/`.

The `Engine/vendor/beook_export` modules and `Tests/Upstream` are a pinned, unchanged upstream snapshot. Prefer adapter changes in `Engine/leafscribe`; if updating upstream, record the exact revision, retain its MIT copyright notice, rerun its tests, update attribution, and explain changed behavior. Edition-specific live tests are opt-in and cannot establish general compatibility with every publisher book.

Swift owns native interaction, layout, PDF assembly and OCR. Keep platform-specific code out of the JSON document protocol. Preserve v1 compatibility or explicitly version breaking changes. Source import must not mutate originals. Test missing resources, malformed archives, Unicode, navigation, cancellation, and export failures when changing those paths.

For public releases, verify an actual bundled build in a logged-in macOS session, inspect PDF pages and EPUB structure, include all runtime licenses, and document the actual signing/notarization status. Use Developer ID signing and notarization when those credentials are available. Do not present the local ad-hoc build as notarized or as tested on systems that were not used.
