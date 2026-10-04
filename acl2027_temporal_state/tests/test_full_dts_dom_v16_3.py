"""Real Arelle authored nested-markup regression; no filing data or QA."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts';sys.path.insert(0,str(SCRIPTS))
import run_full_dts_v16_2 as old
import run_full_dts_v16_3 as amended
from arelle.api.Session import Session
from arelle.RuntimeOptions import RuntimeOptions

SCHEMA='''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xbrli="http://www.xbrl.org/2003/instance" targetNamespace="urn:authored-dom" elementFormDefault="qualified"><xs:import namespace="http://www.xbrl.org/2003/instance" schemaLocation="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"/><xs:element name="Amount" type="xbrli:monetaryItemType" substitutionGroup="xbrli:item" xbrli:periodType="instant" nillable="true"/><xs:element name="Text" type="xbrli:stringItemType" substitutionGroup="xbrli:item" xbrli:periodType="instant" nillable="true"/></xs:schema>'''
INLINE='''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:t="urn:authored-dom" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"><head><title>Fictional nested control</title></head><body><div style="display:none"><ix:header><ix:references><link:schemaRef xlink:type="simple" xlink:href="control.xsd"/></ix:references><ix:resources><xbrli:context id="c"><xbrli:entity><xbrli:identifier scheme="urn:fictional">Authored</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context><xbrli:unit id="u"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit></ix:resources></ix:header></div><ix:nonNumeric name="t:Text" contextRef="c"><div><span>Fictional text</span><!-- a comment does not count --><ix:nonFraction name="t:Amount" contextRef="c" unitRef="u" decimals="INF">21</ix:nonFraction><span><ix:nonFraction name="t:Amount" contextRef="c" unitRef="u" decimals="INF">3</ix:nonFraction></span></div></ix:nonNumeric></body></html>'''

class ArellePhysicalDomControls(unittest.TestCase):
 def test_physical_markup_paths_preserve_fact_objects_and_values(self):
  with tempfile.TemporaryDirectory(dir='/dev/shm') as directory:
   root=Path(directory);(root/'control.xsd').write_text(SCHEMA);(root/'control.htm').write_text(INLINE)
   with Session() as session:
    ok=session.run(RuntimeOptions(entrypointFile=str(root/'control.htm'),cacheDirectory=str(root/'cache'),validate=True,internetConnectivity='offline',disablePersistentConfig=True,keepOpen=True,logFile='logToBuffer',validateEFM=False,calcs='none',utrValidate=False,formulaAction='none',baseTaxonomyValidationMode='all',abortOnMajorError=False))
    self.assertTrue(ok);models=session.get_models();self.assertEqual(len(models),1)
    xml=models[0].modelDocument.xmlRootElement
    # The former function mixed physical root.iter with InlineFact.__iter__,
    # which yields logical tuple children and omits the nested markup.
    with self.assertRaises(KeyError):old.dom_paths(xml)
    paths=amended.dom_paths(xml)
    facts=list(amended.etree._Element.iter(xml,amended.IX))
    self.assertEqual(len(facts),2)
    h='{http://www.w3.org/1999/xhtml}';ix='{http://www.xbrl.org/2013/inlineXBRL}'
    prefix='/'+h+'html[1]/'+h+'body[1]/'+ix+'nonNumeric[1]/'+h+'div[1]/'
    self.assertEqual([paths[f] for f in facts],[prefix+ix+'nonFraction[1]',prefix+h+'span[2]/'+ix+'nonFraction[1]'])
    self.assertEqual([amended.canonical_decimal(f.xValue) for f in facts],['21','3'])
    self.assertTrue(all(f in models[0].factsInInstance for f in facts))
    plain=amended.etree.fromstring(INLINE.encode())
    expected=[amended.dom_paths(plain)[f] for f in plain.iter(amended.IX)]
    self.assertEqual([paths[f] for f in facts],expected)

if __name__=='__main__':unittest.main()
