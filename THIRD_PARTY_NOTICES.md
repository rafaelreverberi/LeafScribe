# LeafScribe licenses and third-party acknowledgements

LeafScribe’s independently authored code is governed by the Personal Use License in LICENSE. Its restrictions do not apply to the separately licensed third-party components below.

LeafScribe is independent and is not affiliated with Beook, its publishers, or its developers. Book content, covers, and annotations remain the property of their respective rights holders.

## beook-export 0.1.27 — MIT

Copyright (c) 2026 JoGe07
https://github.com/JoGe07/beook-export
Pinned revision: 198577f47b0d2d0af6f728c70caa9e6e41c247e5 (full revision in Engine/vendor/UPSTREAM_REVISION).

Reused unchanged: every Python module in Engine/vendor/beook_export: __init__, __main__, cli, database, discovery, epub, extractor, inspect, language, naming, paths. Upstream tests are preserved unchanged in Tests/Upstream. Scripts/inspect_upstream_epub.py is the upstream inspection wrapper with its local import path adapted. No upstream extraction module has been modified. This mature extraction implementation was written by upstream, not independently by LeafScribe.

Independently implemented: Engine/main.py, Engine/leafscribe, Core, Apps/macOS, packaging scripts, LeafScribe tests and documentation. The adapter adds read-only database access, managed DRM-free imports, archive validation, JSON messaging, and configurable annotation inclusion. Swift implements the native library, WebKit/PDFKit export, preview, and Vision OCR for PDF-to-EPUB.

The complete upstream MIT license follows and is also in Licenses/beook-export-MIT.txt.

MIT License

Copyright (c) 2026 JoGe07

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Bundled Python runtime

CPython 3.12, Copyright (c) Python Software Foundation and contributors, distributed under the PSF License Agreement and historical licenses. Full terms are in Licenses/Python-LICENSE.txt; incorporated third-party notices (including libffi, zlib, expat, and decimal) are in Licenses/Python-THIRD-PARTY.txt. SQLite is in the public domain. The build copies these notices into the application and makes them accessible in Settings → About → Open Source Licenses.

## OpenSSL 3

The bundled CPython hash extension links to OpenSSL libcrypto, licensed under Apache-2.0. Full terms are in Licenses/OpenSSL-LICENSE.txt.
https://www.openssl.org/

## PyInstaller 6.16.0

Copyright (c) 2010–2023 PyInstaller Development Team; Copyright (c) 2005–2009 Giovanni Bajo; based on work Copyright (c) 2002 McMillan Enterprises, Inc.
https://pyinstaller.org/

The bundled bootloader is distributed under GPL-2.0-or-later with the PyInstaller bootloader exception, permitting distribution of the combined application without imposing that license on LeafScribe. Runtime hooks use Apache-2.0. Full licensing terms and exception are in Licenses/PyInstaller-COPYING.txt. PyInstaller is a build tool, not an application dependency the user must install.

## Apple system frameworks

SwiftUI, AppKit, Foundation, PDFKit, WebKit, Vision, and CoreText are system frameworks and are not vendored in this repository.

## Original sample

Apps/macOS/Resources/Welcome.html is original LeafScribe demonstration content, dedicated to the public domain under CC0-1.0. No publisher book content is included.
