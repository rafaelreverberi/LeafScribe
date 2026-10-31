import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'Engine'), str(ROOT / 'Engine/vendor')]
from leafscribe.documents import import_document, prepare, safe_path, normalize_html, unpack_epub, discover, epub_from_pages


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def html(self, text='<h1>Test &amp; Notes</h1><p>Searchable café text.</p>'):
        path = self.root / 'source.html'
        path.write_text(text)
        return path
    def test_html_import_and_epub_roundtrip(self):
        source = self.html()
        book = import_document(str(source), str(self.root / 'library'))
        source.unlink()
        result = prepare(book, str(self.root / 'job'))
        self.assertEqual(book['title'], 'Test & Notes')
        self.assertIn('café', Path(result['chapters'][0]['path']).read_text())
        with zipfile.ZipFile(result['epubPath']) as z:
            self.assertEqual(z.infolist()[0].filename, 'mimetype')
            self.assertEqual(z.infolist()[0].compress_type, zipfile.ZIP_STORED)
            for name in z.namelist():
                if name.endswith(('.opf', '.ncx', '.xhtml')):
                    ET.fromstring(z.read(name))
            opf = ET.fromstring(z.read('OEBPS/content.opf'))
            ns = {'o':'http://www.idpf.org/2007/opf'}
            self.assertTrue(any(x.attrib['media-type'] == 'application/xhtml+xml' for x in opf.findall('o:manifest/o:item', ns) if x.attrib['href'] == 'source.html'))
        imported = import_document(result['epubPath'], str(self.root / 'library'))
        roundtrip = prepare(imported, str(self.root / 'roundtrip'))
        self.assertEqual(Path(roundtrip['epubPath']).read_bytes(), Path(result['epubPath']).read_bytes())
        self.assertGreaterEqual(len(roundtrip['chapters']), 1)
    def epub_folder(self):
        archive=self.root/'original.epub'
        epub_from_pages([{'title':'First','html':'<p>FIRST_CHAPTER café.</p>'},
                         {'title':'Second','html':'<p>LAST_CHAPTER complete.</p>'}], 'Folder book',str(archive))
        folder=self.root/'Book.epub'
        with zipfile.ZipFile(archive) as z: z.extractall(folder)
        return folder

    def test_epub_directory_import_is_immutable_and_survives_source_removal(self):
        source=self.epub_folder()
        original={p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()}
        book=import_document(str(source),str(self.root/'library'))
        self.assertTrue(Path(book['path']).is_file())
        self.assertEqual(book['chapterCount'],3)
        self.assertEqual(original,{p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()})
        with zipfile.ZipFile(book['path']) as z:
            self.assertEqual(z.infolist()[0].filename,'mimetype')
            self.assertEqual(z.infolist()[0].compress_type,zipfile.ZIP_STORED)
            for name,data in original.items():self.assertEqual(z.read(name),data)
        import shutil
        shutil.rmtree(source)
        prepared=prepare(book,str(self.root/'job'))
        self.assertEqual(len(prepared['chapters']),3)
        self.assertIn('FIRST_CHAPTER café',Path(prepared['chapters'][1]['path']).read_text())
        self.assertIn('LAST_CHAPTER complete',Path(prepared['chapters'][2]['path']).read_text())
        self.assertEqual(Path(prepared['epubPath']).read_bytes(),Path(book['path']).read_bytes())

    def test_epub_directory_rejects_symlinks_and_rolls_back(self):
        source=self.epub_folder()
        (source/'outside').symlink_to(self.html())
        library=self.root/'library'
        with self.assertRaisesRegex(ValueError,'symlinks'):
            import_document(str(source),str(library))
        self.assertEqual(list(library.iterdir()),[])

    def test_epub_directory_limits_and_invalid_packages_roll_back(self):
        source=self.epub_folder();library=self.root/'library'
        with patch('leafscribe.documents.MAX_ARCHIVE_BYTES',20):
            with self.assertRaisesRegex(ValueError,'limit'):
                import_document(str(source),str(library))
        self.assertEqual(list(library.iterdir()),[])
        (source/'META-INF/container.xml').unlink()
        with self.assertRaisesRegex(ValueError,'container.xml'):
            import_document(str(source),str(library))
        self.assertEqual(list(library.iterdir()),[])

    def test_epub_directory_rejects_missing_spine_and_nested_destination(self):
        source=self.epub_folder();library=self.root/'library'
        with self.assertRaisesRegex(ValueError,'outside'):
            import_document(str(source),str(source/'nested-library'))
        self.assertFalse((source/'nested-library').exists())
        for p in source.rglob('*.xhtml'):p.unlink()
        with self.assertRaisesRegex(ValueError,'missing'):
            import_document(str(source),str(library))
        self.assertEqual(list(library.iterdir()),[])

    def cover_epub(self, mode):
        folder=self.epub_folder()
        ns='http://www.idpf.org/2007/opf'
        opf=folder/'OEBPS/content.opf';tree=ET.parse(opf);manifest=tree.find('{'+ns+'}manifest')
        images=folder/'OEBPS/images';images.mkdir();(images/'Cover Art.png').write_bytes(b'original image bytes')
        image=ET.SubElement(manifest,'{'+ns+'}item',{'id':'art','href':'images/Cover%20Art.png','media-type':'image/png'})
        metadata=tree.find('{'+ns+'}metadata')
        if mode=='epub3':image.set('properties','cover-image')
        if mode=='epub2':ET.SubElement(metadata,'{'+ns+'}meta',{'name':'cover','content':'art'})
        if mode in ('guide','unsafe','remote'):
            page=folder/'OEBPS/cover.xhtml'
            href={'guide':'images/Cover%20Art.png','unsafe':'../../outside.png','remote':'https://example.com/cover.png'}[mode]
            page.write_text('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:xlink="http://www.w3.org/1999/xlink"><body><svg xmlns="http://www.w3.org/2000/svg"><image xlink:href="'+href+'"/></svg></body></html>')
            ET.SubElement(manifest,'{'+ns+'}item',{'id':'coverpage','href':'cover.xhtml','media-type':'application/xhtml+xml'})
            guide=ET.SubElement(tree.getroot(),'{'+ns+'}guide')
            ET.SubElement(guide,'{'+ns+'}reference',{'type':'cover','href':'cover.xhtml'})
        tree.write(opf,encoding='utf-8',xml_declaration=True)
        return folder

    def test_epub_cover_metadata_and_guide_resolve_local_image(self):
        for mode in ('epub3','epub2','guide'):
            with self.subTest(mode=mode):
                folder=self.cover_epub(mode)
                book=import_document(str(folder),str(self.root/'library'))
                self.assertEqual(Path(book['coverPath']).read_bytes(),b'original image bytes')
                self.assertTrue(Path(book['coverPath']).is_relative_to((self.root/'library').resolve()))
                import shutil
                shutil.rmtree(folder)

    def test_epub_without_cover_and_unsafe_guides_keep_placeholder(self):
        for mode in ('none','unsafe','remote'):
            with self.subTest(mode=mode):
                folder=self.cover_epub(mode)
                book=import_document(str(folder),str(self.root/'library'))
                self.assertIsNone(book['coverPath'])
                import shutil
                shutil.rmtree(folder)

    def test_html_copies_nested_css_images_and_fonts(self):
        (self.root / 'assets').mkdir()
        (self.root / 'assets/style.css').write_text("@font-face{src:url('font.woff')}p{background:url('picture.png')}")
        (self.root / 'assets/font.woff').write_bytes(b'fake-font')
        (self.root / 'assets/picture.png').write_bytes(b'fake-image')
        source = self.html('<link rel="stylesheet" href="assets/style.css"><h1>Resources</h1>')
        book = import_document(str(source), str(self.root / 'library'))
        result = prepare(book, str(self.root / 'job'))
        with zipfile.ZipFile(result['epubPath']) as z:
            self.assertIn('OEBPS/assets/font.woff', z.namelist())
            self.assertIn('OEBPS/assets/picture.png', z.namelist())
    def test_path_traversal_and_absolute_paths_rejected(self):
        for name in ('../escape', '/etc/passwd', '..\\escape'):
            with self.assertRaises(ValueError): safe_path(self.root, name)
    def test_epub_archive_traversal_rejected_before_extraction(self):
        path = self.root / 'bad.epub'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('mimetype', 'application/epub+zip')
            z.writestr('../escape', 'bad')
        with self.assertRaises(ValueError): unpack_epub(path, self.root / 'unpack')
        self.assertFalse((self.root / 'escape').exists())
    def test_encrypted_resources_rejected(self):
        path = self.root / 'encrypted.epub'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('mimetype', 'application/epub+zip')
            z.writestr('META-INF/encryption.xml', '<encryption><EncryptedData/></encryption>')
        with self.assertRaisesRegex(ValueError, 'Encrypted'): unpack_epub(path, self.root / 'unpack')
    def test_html_missing_asset_is_actionable_and_rolls_back(self):
        source = self.html('<img src="missing.png">')
        library = self.root / 'library'
        with self.assertRaisesRegex(ValueError, 'Missing local HTML resource'):
            import_document(str(source), str(library))
        self.assertEqual(list(library.iterdir()), [])
    def test_html_outside_folder_rejected(self):
        folder = self.root / 'nested'; folder.mkdir()
        source = folder / 'test.html'; source.write_text('<img src="../private.png">')
        (self.root / 'private.png').write_bytes(b'private')
        with self.assertRaises(ValueError): import_document(str(source), str(self.root / 'library'))
    def test_html_normalization_keeps_tables_unicode_and_svg(self):
        raw = '<h1>Café</h1><p>A&nbsp;B<br>Line<script>alert(1)</script><img src="x" onerror="bad()"><svg viewBox="0 0 1 1"><path d="M0 0"/></svg>'
        data = normalize_html(raw, 'Title')
        tree = ET.fromstring(data)
        self.assertNotIn(b'alert', data)
        self.assertNotIn(b'onerror', data)
        self.assertIn('Café', data.decode())
        svg = tree.find('.//{http://www.w3.org/2000/svg}svg')
        self.assertIsNotNone(svg)
        self.assertEqual(svg.attrib['viewBox'], '0 0 1 1')
    def test_no_beook_is_normal_empty_state(self):
        result = discover(str(self.root / 'absent'))
        self.assertEqual(result['books'], [])
        self.assertTrue(result['warnings'])
    def test_pdf_text_epub_preserves_every_page(self):
        output = self.root / 'text.epub'
        epub_from_pages([{'title':'Page 1','html':'<p>Alpha</p>'}, {'title':'Page 2','html':'<p>Omega</p>'}], 'Test', str(output))
        result = unpack_epub(output, self.root / 'pages')
        self.assertTrue(any('Omega' in Path(c['path']).read_text() for c in result['chapters']))
    def test_beook_annotation_option_controls_appendix(self):
        from beook_export.extractor import BookContent
        from contextlib import nullcontext
        appendix = 'OEBPS/beook-export-annotations.xhtml'
        page = 'OEBPS/chapter.xhtml'
        content = BookContent(reference='test', title='Test', language='en', spine_filenames=[page, appendix],
                              files={page: normalize_html('<h1>Chapter</h1><p>Book text</p>', 'Chapter'), appendix: normalize_html('<h1>Annotations</h1><p>Personal note</p>', 'Annotations')},
                              media_types={page:'application/xhtml+xml', appendix:'application/xhtml+xml'}, pk_to_filename={})
        book = {'title':'Test', 'kind':'beook', 'path':'unused', 'root':str(self.root), 'reference':'test'}
        with patch('leafscribe.documents.readonly', return_value=nullcontext(MagicMock())), patch('leafscribe.documents.extract_book', return_value=content), patch('leafscribe.documents.read_annotations', return_value=[dict(id='1',title='',text='Personal note',type='N',color='',date='',issueID='chapter',issueTitle='Chapter',topic='1',resourcePK=1,rangePath='')]):
            included = prepare(book, str(self.root / 'with-notes'), True)
        with patch('leafscribe.documents.readonly', return_value=nullcontext(MagicMock())), patch('leafscribe.documents.extract_book', return_value=content), patch('leafscribe.documents.read_annotations', return_value=[dict(id='1',title='',text='Personal note',type='N',color='',date='',issueID='chapter',issueTitle='Chapter',topic='1',resourcePK=1,rangePath='')]):
            excluded = prepare(book, str(self.root / 'without-notes'), False)
        self.assertEqual(len(included['chapters']), 2)
        self.assertEqual(len(excluded['chapters']), 1)
        with zipfile.ZipFile(excluded['epubPath']) as z:
            self.assertNotIn(appendix, z.namelist())
        with zipfile.ZipFile(included['epubPath']) as z:
            self.assertIn(appendix, z.namelist())

    def test_protocol_has_structured_errors_and_version(self):
        for request in ({'version':2, 'action':'health'}, {'version':1, 'action':'missing'}):
            response = subprocess.run([sys.executable, str(ROOT / 'Engine/main.py')], input=json.dumps(request)+'\n', text=True, capture_output=True)
            event = json.loads(response.stdout.splitlines()[-1])
            self.assertEqual(event['version'], 1)
            self.assertEqual(event['event'], 'error')
            self.assertEqual(event['code'], 'INVALID_DOCUMENT')
            self.assertEqual(response.returncode, 1)

if __name__ == '__main__': unittest.main()
