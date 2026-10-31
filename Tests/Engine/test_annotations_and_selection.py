import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from xml.etree import ElementTree as ET
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'Engine'),str(ROOT/'Engine/vendor')]
from leafscribe.annotations import read_annotations, mark_quote, add_annotations, APPENDIX, annotate_source, AnnotationSource, note_text
from leafscribe.documents import normalize_html, prepare, import_document, epub_from_pages, unpack_epub
from beook_export.extractor import BookContent


def note(**values):
    return dict(dict(id='1', title='', text='Important phrase', type='H', color='', date='',issueID='chapter',issueTitle='Chapter',topic='1',resourcePK=1,rangePath=''), **values)


class AnnotationSelectionTests(unittest.TestCase):
    def test_read_keeps_empty_highlights_and_excludes_deleted(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.executescript('''CREATE TABLE ZILPNOTE (Z_PK, ZTITLE, ZTEXT, ZTYPE, ZCOLOR, ZDATE, ZACCESSPATH, ZTOPIC, ZNOTEDELETED);
            CREATE TABLE ZILPTOPIC (Z_PK, ZISSUE, ZACCESSPATH, ZRESOURCE, ZORDER);
            CREATE TABLE ZILPISSUEDEF (Z_PK, ZISSUEID, ZTITLE, ZORDER);
            INSERT INTO ZILPISSUEDEF VALUES(1,'test','Chapter',1);
            INSERT INTO ZILPTOPIC VALUES(1,1,'1',1,1);
            INSERT INTO ZILPNOTE VALUES(1,NULL,NULL,'H',NULL,NULL,'position',1,0);
            INSERT INTO ZILPNOTE VALUES(2,'Note','<p>A &amp; B</p>','N',NULL,NULL,'position',1,0);
            INSERT INTO ZILPNOTE VALUES(3,'Deleted','No','H',NULL,NULL,'position',1,1);''')
        with patch('leafscribe.annotations.get_issues_for_reference',return_value=[SimpleNamespace(pk=1)]):
            result=read_annotations(c,'test')
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['text'],'')
        self.assertEqual(result[1]['text'],'A & B')
        c.close()
    def test_cross_span_highlight_keeps_source_text(self):
        source=normalize_html('<h1>Heading</h1><p>One Important <em>phrase</em> remains.</p>','Title')
        marked,found=mark_quote(source,'Important phrase','n1')
        self.assertTrue(found)
        self.assertEqual(''.join(ET.fromstring(source).itertext()),''.join(ET.fromstring(marked).itertext()))
        self.assertEqual(marked.count(b'data-leafscribe-annotation='),2)
    def test_ambiguous_quote_is_not_highlighted_at_wrong_location(self):
        source=normalize_html('<p>Same phrase. Same phrase.</p>','Title')
        marked,found=mark_quote(source,'Same phrase','n1')
        self.assertFalse(found)
        self.assertEqual(marked,source)
    def test_dom_ranges_resolve_before_inserting_overlapping_marks(self):
        source=b'<html><body><div><p>Same phrase. Same phrase.</p></div></body></html>'
        path='html/body[1]/div[1]/p[1]/text()[1]'
        result=annotate_source(source,[note(text='Same phrase',rangePath=f'{path}|13|{path}|24',colorIndex=4),
                                     note(id='2',text='phrase',rangePath=f'{path}|18|{path}|24',colorIndex=2)])
        tree=ET.fromstring(result)
        self.assertEqual(''.join(tree.itertext()),'Same phrase. Same phrase.')
        paragraph=tree.find('./body/div/p')
        self.assertEqual(paragraph.text,'Same phrase. ')
        self.assertIn(b'rgba(233,163,191,0.5)',result)
        self.assertIn(b'rgba(103,194,213,0.5)',result)
        self.assertEqual(''.join(e.text or '' for e in tree.iter() if e.get('data-leafscribe-annotation')=='2'),'phrase')

    def test_utf16_and_element_child_boundaries(self):
        source='<html><body><p>A😀 <em>word</em> tail.</p></body></html>'.encode()
        path='html/body[1]/p[1]'
        result=annotate_source(source,[note(text='😀 word',rangePath=f'{path}/text()[1]|1|{path}|2')])
        self.assertIn(b'data-leafscribe-annotation=',result)
        self.assertEqual(''.join(ET.fromstring(result).itertext()),'A😀 word tail.')

    def test_empty_quote_uses_position_and_zero_length_cursor_is_not_invented(self):
        source=b'<html><body><p>abcdef</p></body></html>'
        path='html/body[1]/p[1]/text()[1]'
        result=annotate_source(source,[note(text='',rangePath=f'{path}|1|{path}|4'),
                                     note(id='2',text='',rangePath=f'{path}|4|{path}|4')])
        root=ET.fromstring(result)
        self.assertEqual(root.find('.//mark').text,'bcd')
        self.assertNotIn(b'annotation="2"',result)

    def test_stale_range_uses_unique_quote_instead_of_wrong_text(self):
        source=b'<html><body><p>Wrong. Correct.</p></body></html>'
        path='html/body[1]/p[1]/text()[1]'
        result=annotate_source(source,[note(text='Correct',rangePath=f'{path}|0|{path}|5')])
        self.assertEqual(ET.fromstring(result).find('.//mark').text,'Correct')
        unchanged=annotate_source(source,[note(text='Missing',rangePath=f'{path}|0|{path}|5')])
        self.assertEqual(unchanged,source)

    def test_stale_highlight_wrapper_indexes_use_paragraph_context(self):
        source=b'<html><body><p>the <span>concept</span> remains.</p><p>the concept again.</p></body></html>'
        path='html/body[1]/p[1]'
        n=note(text='the concept',rangePath=f'{path}/text()[2]|205|{path}/span[2]/text()[1]|7')
        result=annotate_source(source,[n]);root=ET.fromstring(result)
        self.assertEqual(''.join(root.find('./body/p').itertext()),'the concept remains.')
        self.assertEqual(''.join(e.text or '' for e in root.iter('mark')),'the concept')
        self.assertEqual(len(list(root.findall('./body/p')[1].iter('mark'))),0)

    def test_note_is_after_anchor_with_color_width_and_paragraphs(self):
        source=b'<html><body><div><p>Before <span>anchor</span>.</p><p>After.</p></div></body></html>'
        n=note(type='N',title='Study',text='First.\n\nSecond.',colorIndex=4,
               rangePath='html/body[1]/div[1]/p[1]/span[1]',presentation='{"pinned":true,"width":489,"height":212}')
        result=annotate_source(source,[n]);root=ET.fromstring(result)
        children=list(root.find('./body/div'))
        self.assertEqual([c.tag for c in children],['p','aside','p'])
        self.assertIn('width:489px',children[1].get('style'))
        self.assertIn('#e9a3bf',children[1].get('style'))
        self.assertIn('First.\n\nSecond.',''.join(children[1].itertext()))
        self.assertNotIn('height:212px',children[1].get('style')) # Growing text must not be clipped.
        self.assertEqual(note_text('<p>First.</p><p>Second.</p>'),'First.\n\nSecond.')

    def test_resource_view_never_changes_database_and_survives_upstream_rewrite(self):
        from beook_export.extractor import _rewrite_xhtml
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.execute('CREATE TABLE ZILPRESOURCE(Z_PK,ZDATA,ZMEDIATYPE,ZPLAINDATA)')
        original=b'<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body><p>Important phrase.</p></body></html>'
        c.execute('INSERT INTO ZILPRESOURCE VALUES(1,?,?,NULL)',(original,'application/xhtml+xml'))
        c.execute('PRAGMA query_only=ON')
        path='html/body[1]/p[1]/text()[1]'
        view=AnnotationSource(c,[note(rangePath=f'{path}|0|{path}|16')])
        row=view.execute('SELECT Z_PK AS pk,ZDATA AS data,ZMEDIATYPE AS media_type,ZPLAINDATA AS plain_data FROM ZILPRESOURCE').fetchall()[0]
        rewritten=_rewrite_xhtml(row['data'],{},{},{},{})
        self.assertIn(b'data-leafscribe-annotation=',rewritten)
        self.assertEqual(c.execute('SELECT ZDATA FROM ZILPRESOURCE').fetchone()[0],original)
        c.close()

    def test_source_markers_route_consolidated_pages_and_selection(self):
        one='OEBPS/chapter-one.xhtml';two='OEBPS/chapter-two.xhtml'
        n=note(resourcePK=99,rangePath='html/body[1]/p[1]/text()[1]|0|html/body[1]/p[1]/text()[1]|16')
        source=normalize_html('<p>Important phrase.</p>','Title')
        marked=annotate_source(source,[n])
        content=BookContent(reference='test',title='Test',language='en',spine_filenames=[one,two],
            files={one:source,two:marked},media_types={one:'application/xhtml+xml',two:'application/xhtml+xml'},pk_to_filename={99:'absorbed.xhtml'})
        selected,count,inline=add_annotations(content,[n],{two})
        self.assertEqual((count,inline),(1,1))
        self.assertNotIn(one,selected.files)
        selected,count,inline=add_annotations(content,[n],{one})
        self.assertEqual((count,inline),(0,0))
        self.assertNotIn(APPENDIX,selected.files)

    def test_subset_epub_does_not_embed_unselected_book_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);out=root/'original.epub'
            epub_from_pages([{'title':'First','html':'<p>ALPHA_ONLY</p>'},{'title':'Second','html':'<p>OMEGA_ONLY</p>'}],'Book',str(out))
            book=import_document(str(out),str(root/'library'))
            all_pages=prepare(book,str(root/'all'))
            last=next(c for c in all_pages['chapters'] if 'OMEGA_ONLY' in Path(c['path']).read_text())
            selected=prepare(book,str(root/'selected'),selected_chapters=[last['id']])
            self.assertEqual(len(selected['chapters']),1)
            with zipfile.ZipFile(selected['epubPath']) as z:
                text=b''.join(z.read(n) for n in z.namelist() if n.endswith(('.html','.xhtml')))
            self.assertIn(b'OMEGA_ONLY',text)
            self.assertNotIn(b'ALPHA_ONLY',text)
            parsed=unpack_epub(Path(selected['epubPath']),root/'verify')
            self.assertGreaterEqual(len(parsed['chapters']),1)
    def test_beook_subset_and_annotation_routing(self):
        one='OEBPS/chapter-one.xhtml';two='OEBPS/chapter-two.xhtml'
        content=BookContent(reference='test',title='Test',language='en',spine_filenames=[one,two],
            files={one:normalize_html('<h1>One</h1><p>Important phrase</p><a href="chapter-two.xhtml">Next</a>','One'),two:normalize_html('<h1>Two</h1><p>OTHER_CHAPTER_SECRET</p>','Two')},
            media_types={one:'application/xhtml+xml',two:'application/xhtml+xml'},pk_to_filename={1:'chapter-one.xhtml',2:'chapter-two.xhtml'})
        filtered,count,inline=add_annotations(content,[note(),note(id='2',resourcePK=2,text='OTHER_CHAPTER_SECRET')],{one})
        self.assertEqual(count,1);self.assertEqual(inline,1)
        self.assertNotIn(two,filtered.files)
        self.assertIn(APPENDIX,filtered.spine_filenames)
        self.assertNotIn(b'href="chapter-two.xhtml"',filtered.files[one])
        self.assertIn(b'<mark',filtered.files[one])
        self.assertIn(b'1 Anmerkungen',filtered.files[APPENDIX])
    def test_text_note_is_visible_in_its_section_and_appendix(self):
        path='OEBPS/chapter.xhtml'
        content=BookContent(reference='test',title='Test',language='en',spine_filenames=[path],
            files={path:normalize_html('<h1>Chapter</h1><p>Book text.</p>','Chapter')},
            media_types={path:'application/xhtml+xml'},pk_to_filename={1:'chapter.xhtml'})
        annotated,count,_=add_annotations(content,[note(type='N',text='My study note')])
        self.assertEqual(count,1)
        self.assertIn(b'data-leafscribe-note=',annotated.files[path])
        self.assertIn(b'My study note',annotated.files[path])
        self.assertIn(b'My study note',annotated.files[APPENDIX])

    def test_empty_selection_is_an_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);html=root/'test.html';html.write_text('<h1>Title</h1>')
            book=import_document(str(html),str(root/'library'))
            with self.assertRaisesRegex(ValueError,'Select at least one'):
                prepare(book,str(root/'selected'),selected_chapters=[])

if __name__=='__main__':unittest.main()
