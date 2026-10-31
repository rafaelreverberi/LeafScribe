# LeafScribe development

- Read README.md and docs/ARCHITECTURE.md before structural changes.
- Preserve attribution and the unchanged pinned upstream modules. Put adaptations in Engine/leafscribe.
- Never commit Beook data, paid content, user annotations, or generated exports. output/, dist/, and .build/ are intentionally ignored.
- Swift checks: swift test. Engine checks: python3 -m unittest discover -s Tests/Engine -v. Upstream isolated checks: python3 Scripts/test_upstream.py.
- Build the bundle: Scripts/build_app.sh. Validate it in a graphical session with Scripts/verify_bundle.sh.
- python3 Scripts/verify_live.py is opt-in and reads installed books; report only aggregate results.
- Keep original files immutable, SQL connections read-only, subprocess arguments fixed, and exports staged until successful completion.
- Use the versioned JSON interface; do not parse human-readable CLI output.
