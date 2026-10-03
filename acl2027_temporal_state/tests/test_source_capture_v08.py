"""Focused controls for content-loss and HTTP200 blocker risks observed in v0.8."""
import importlib.util
from pathlib import Path
import unittest

P=Path(__file__).resolve().parents[1]/'scripts/capture_source_stream_v08.py'
spec=importlib.util.spec_from_file_location('capture_source_stream_v08',P)
capture=importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class CaptureContractTests(unittest.TestCase):
    def extract(self,value):
        p=capture.DocumentText()
        p.feed(value)
        p.close()
        return p.text()

    def test_aspnet_form_preserves_article(self):
        self.assertEqual(self.extract('<html><body><form><article><h1>Chief executive</h1><p>Appointed today.</p></article></form></body></html>'),
                         'Chief executive\nAppointed today.\n')

    def test_nonvisible_and_navigation_text_omitted_but_banner_retained(self):
        self.assertEqual(self.extract('<html><body><nav>Nav</nav><script>unsafe</script><style>x</style><div>Current banner</div><p>Article</p><footer>footer</footer></body></html>'),
                         'Current banner\nArticle\n')

    def test_http200_unavailable_rejected_even_when_verbose(self):
        result=capture.usability(200,'text/html','Site Unavailable','Expected title '+('unavailable '*120),'Expected title')
        self.assertFalse(result['provisional_content_usable'])
        self.assertIn('blocking_or_error_page_title',result['rejection_reasons'])

    def test_http403_never_admitted_by_body_length(self):
        result=capture.usability(403,'text/html','Expected title','Expected title '+('body '*120),'Expected title')
        self.assertFalse(result['provisional_content_usable'])
        self.assertIn('non_200_http_status',result['rejection_reasons'])

    def test_normalization_is_deterministic_unicode(self):
        self.assertEqual(self.extract('<body><p>Cafe\u0301 &amp; tea\n  now.</p></body>'), 'Café & tea\nnow.\n')


if __name__=='__main__':
    unittest.main()
