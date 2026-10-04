from pathlib import Path
import json,sys
sys.path.insert(0,'scripts');import run_full_dts_v16_1 as m
from arelle.api.Session import Session
from arelle.RuntimeOptions import RuntimeOptions
root=Path('/dev/shm/wikigraph_v15/external/full_dts_authored_session');root.mkdir(exist_ok=False)
(root/'control.xsd').write_text('''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:t="urn:wikigraph:authored-control" targetNamespace="urn:wikigraph:authored-control" elementFormDefault="qualified"><xs:import namespace="http://www.xbrl.org/2003/instance" schemaLocation="http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"/><xs:element name="TestAmount" id="TestAmount" type="xbrli:monetaryItemType" substitutionGroup="xbrli:item" xbrli:periodType="instant" nillable="true"/></xs:schema>''')
(root/'control.htm').write_text('''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:t="urn:wikigraph:authored-control" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:ixt-sec="http://www.sec.gov/inlineXBRL/transformation/2015-08-31"><head><title>Authored control</title></head><body><div style="display:none"><ix:header><ix:references><link:schemaRef xlink:type="simple" xlink:href="control.xsd"/></ix:references><ix:resources><xbrli:context id="c"><xbrli:entity><xbrli:identifier scheme="urn:wikigraph:fictional">AuthoredEntity</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context><xbrli:unit id="u"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit></ix:resources></ix:header></div><div><ix:nonFraction name="t:TestAmount" contextRef="c" unitRef="u" decimals="INF" format="ixt-sec:numwordsen">twenty one</ix:nonFraction></div></body></html>''')
p=m.jread('data/taxonomy_v16/full_dts_protocol_v16_1.json')
with Session() as session:
 ok=session.run(RuntimeOptions(entrypointFile=str(root/'control.htm'),cacheDirectory=str(root/'cache'),plugins=p['sec_transform_directory'],**p['runtime_options']))
 models=session.get_models();facts=[f for model in models for f in model.factsInInstance]
 logs=session.get_logs('json');(root/'logs.json').write_text(logs)
 pins={str(Path(b['path']).resolve()):b['sha256'] for b in p['runtime_files']+p['control_modules']+p['bindings']}
 modules=m.dependency_modules();new=[b for b in modules if pins.get(b['path'])!=b['sha256']]
 result={'session_return':ok,'model_count':len(models),'fact_count':len(facts),'authored_expected':'21','observed_values':[str(getattr(f,'xValue',None)) for f in facts],'model_errors':[str(e) for model in models for e in model.errors],'unbound_modules':new,'modules':modules,'source_natural_objects':0,'fixtures':[m.binding(root/'control.xsd'),m.binding(root/'control.htm')],'logs':m.binding(root/'logs.json')}
 m.jwrite(root/'result.json',result,exclusive=True)
 print(json.dumps({k:v for k,v in result.items() if k not in ['modules','unbound_modules','fixtures','logs']}));print('unbound_modules',len(new))
