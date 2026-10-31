#!/usr/bin/env python3
"""Run unchanged upstream tests without depending on someone's installed book editions.
Use upstream's original runner separately for the edition-specific live suite.
"""
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Engine/vendor'))
LIVE_METHODS = {'test_pdf2html_merge_includes_superscripts', 'test_resolve_course_product_reference_for_series_chapters'}
def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite): yield from flatten(test)
        else: yield test
suite = unittest.defaultTestLoader.discover(str(ROOT / 'Tests/Upstream'))
for test in flatten(suite):
    if test.__class__.__name__ in ('TestLiveExtraction', 'TestInspectEpub') and test._testMethodName != 'test_consolidates_glossary_pages' or test._testMethodName in LIVE_METHODS:
        # Inspection malformed-file tests do not need a live database despite the upstream setUp.
        if test.__class__.__name__ == 'TestInspectEpub' and not test._testMethodName.startswith('test_live_'):
            test.setUp = lambda: None
        else:
            def skip(): raise unittest.SkipTest('Requires upstream author’s specific local book editions; run live suite explicitly')
            test.setUp = skip
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(not result.wasSuccessful())
