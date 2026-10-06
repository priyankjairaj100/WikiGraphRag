#!/usr/bin/env python3
"""Replay blind assistant-authored source references for eight frozen questions.

Selections are source-reviewed locators, not a retrieval algorithm. Raw filings
stay external; the result contains paraphrases, byte locators and source hashes.
No retrieval ranking or prediction is read by this script.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from temporal_state.typed_reader_v15_1 import _parse

PROTOCOL_SHA256 = '214d0be8ba347c59a77a0a5b3c1f983d822ce9998999c7aae0ecfda654f21ae2'


def digest(value):
    return hashlib.sha256(value).hexdigest()


def build(external):
    protocol_bytes = (ROOT / 'data/task_probe_v24/protocol.json').read_bytes()
    assert digest(protocol_bytes) == PROTOCOL_SHA256
    protocol = json.loads(protocol_bytes)
    source_metadata = [m for m in protocol['sources'] if m['issuer_id'] in ('PFE', 'LLY')]
    sources, nodes, flat = {}, {}, {}
    for metadata in source_metadata:
        name = metadata['external_filename']
        raw = (external / name).read_bytes()
        assert digest(raw) == metadata['expected_sha256']
        assert len(raw) == metadata['expected_bytes']
        sources[name] = raw
        parsed = _parse(raw)
        nodes[name] = {node.start: node for node in parsed}
        flat[name] = ' '.join(parsed[0].text().split())

    def anchor(name, start, stop=None):
        stop = nodes[name][start].stop if stop is None else stop
        assert 0 <= start < stop <= len(sources[name])
        return {'source_file': name, 'byte_start': start, 'byte_stop': stop,
                'span_sha256': digest(sources[name][start:stop])}

    def phrase(name, start, prefix, suffix=None):
        """A minimal sentence range inside one already-reviewed XML node."""
        node = nodes[name][start]
        raw = sources[name][node.start:node.stop]
        begin = raw.index(prefix.encode())
        if suffix is None:
            end = raw.index(b'.', begin) + 1
        else:
            marker = suffix.encode()
            end = raw.index(marker, begin) + len(marker)
        return anchor(name, node.start + begin, node.start + end)

    def c(cid, statement, *witness_sets, role='required'):
        assert witness_sets and all(witness_sets)
        return {'claim_id': cid, 'statement': statement, 'role': role,
                'witness_sets': list(witness_sets)}

    p = 'PFE_2024.html'
    l = 'LLY_2024.html'
    claims = {}
    growth = phrase(p, 1314808, 'Total revenues</span>', 'or approximately 1%.')
    claims['PFE', 'currency_growth'] = [
        c('reported_and_operational_growth',
          'On the 2024 report comparative basis, Pfizer reports total revenue increasing 7% ($4.1 billion), from $59.6 billion in 2023 to $63.6 billion in 2024; operational growth is also reported as 7% ($4.4 billion).', [growth]),
        c('operational_definition',
          'Operational variances are period-to-period changes excluding foreign-exchange-rate effects; this definition does not remove acquisitions or make the measure organic growth.',
          [phrase(p, 1290355, 'References to operational variances', 'foreign exchange rates.')]),
        c('currency_effect',
          'The company explicitly reports an unfavorable foreign-exchange effect of $349 million, approximately 1%; subtracting the two rounded 7% growth figures would incorrectly imply no currency effect.', [growth]),
        c('comparative_revenue_scope',
          'In first-quarter 2024 royalty income was moved from Other income/deductions into a separate Royalty revenues line within Total revenues, and comparative periods were recast to that presentation.', [anchor(p, 1290950)]),
    ]
    claims['PFE', 'segment_recast'] = [
        c('earlier_segment_basis',
          'The 2023 report identifies Biopharma and Business Innovation as two operating segments, with Biopharma the only reportable segment.',
          [anchor('PFE_2023.html', 2560214)], [anchor('PFE_2023.html', 1410997)]),
        c('later_segment_basis',
          'The 2024 report identifies Biopharma, PC1, and Pfizer Ignite as three operating segments while retaining Biopharma as the only reportable segment. PC1 and Pfizer Ignite had been managed together within Business Innovation before June 2024.',
          [phrase(p, 4752912, 'We manage our commercial operations', 'Biopharma, PC1 and Pfizer Ignite.'),
           phrase(p, 4752912, 'Prior to June 2024', 'Biopharma is the only reportable segment.')]),
        c('earnings_basis_and_comparative_reclassification',
          'The segment earnings basis changed from January 1, 2024: Biopharma earnings include manufacturing/supply, sales/marketing, R&D, and medical/safety costs associated with its products. Manufacturing overhead and the described global ORD/PRD R&D and medical/safety costs were previously in Other business activities. Pfizer explicitly reclassified prior-period segment information to conform to the current presentation.',
          [anchor(p, 4760626)]),
    ]
    # The table columns are reviewed individually; no complete table is required.
    award_specs = [
        ('TSRU', 'total shareholder return units', 270, '2.1', 4408826, 4410536, 4442018, 4449637),
        ('RSU', 'restricted stock units', 214, '1.8', 4409121, 4411399, 4443489, 4451207),
        ('PPS', 'portfolio performance shares', 107, '1.9', 4409415, 4412262, 4444960, 4452777),
        ('PSA', 'performance share awards', 40, '1.7', 4409709, 4413125, 4446430, 4454347),
        ('options', 'stock options', 4, '1.7', 4410003, 4413988, 4447898, 4455917),
    ]
    shared = [4408419, 4410250, 4441675, 4449237]
    claims['PFE', 'stock_compensation'] = [
        c(f'{award}_unrecognized',
          f'At 2024 year end, unrecognized pre-tax compensation cost for nonvested {award + "s" if award != "options" else "stock options"} is ${amount} million, with an expected weighted-average recognition period of {years} years.',
          [anchor(p, pos) for pos in shared + [award_header, year_header, amount_cell, period_cell]])
        for award, name, amount, years, award_header, year_header, amount_cell, period_cell in award_specs
    ]
    claims['PFE', 'stock_compensation'].append(c('BPA_absence',
        'No BPAs were outstanding at December 31, 2024.',
        [phrase(p, 4362996, 'No</ix:nonFraction> BPAs were granted', 'December 31, 2024.')]))

    claims['LLY', 'currency_growth'] = [
        c('reported_growth',
          'Lilly reports consolidated revenue growth of 32% for 2024 versus 2023.',
          [anchor(l, 1038235), anchor(l, 1038582), anchor(l, 1042874)],
          [anchor(l, 1046023), anchor(l, 1046317), anchor(l, 1052103)]),
        c('reported_decomposition_and_precision',
          'Its consolidated revenue-change decomposition reports volume at 27%, price at 5%, and foreign exchange as a dash (zero at displayed precision); the source warns that figures may not add because of rounding. This is not an explicitly labeled constant-currency or operational-growth measure, and the dash does not establish an exactly zero currency effect.',
          [anchor(l, 1045334), anchor(l, 1046023), anchor(l, 1046317),
           anchor(l, 1047074), anchor(l, 1048876), anchor(l, 1050480), anchor(l, 1054093)]),
    ]
    claims['LLY', 'segment_recast'] = [
        c('earlier_single_segment',
          'The 2023 report describes one operating segment covering worldwide discovery, development, manufacturing, marketing, and sales of pharmaceutical products.',
          [phrase('LLY_2023.html', 1515160, 'We operate as a single operating segment', 'pharmaceutical products worldwide.')]),
        c('later_single_segment',
          'The 2024 report retains one reportable segment covering the same described worldwide pharmaceutical activities.',
          [phrase(l, 3031445, 'We operate as a single reportable segment', 'pharmaceutical products worldwide.')]),
        c('disclosure_change_and_prior_period_rule',
          'Lilly states in 2024 that it adopted ASU 2023-07 effective January 1, 2024 and now provides the required single-segment disclosures. Its 2023 report had described the amendments as requiring retrospective application to all presented prior periods. These statements support a disclosure expansion, not an inferred reorganization into different segments or a claim that comparative amounts were substantively restated.',
          [anchor(l, 1518421), anchor('LLY_2023.html', 1525917)]),
    ]
    claims['LLY', 'stock_compensation'] = []
    for award, name, amount, months, node_start in [
        ('RSU', 'restricted stock units', '485.1', 23, 2371450),
        ('SVA', 'shareholder value awards', '61.4', 21, 2386751),
        ('RVA', 'relative value awards', '27.5', 22, 2402861),
    ]:
        claims['LLY', 'stock_compensation'].append(c(f'{award}_unrecognized',
            f'At December 31, 2024, estimated unrecognized compensation for nonvested {award}s is ${amount} million, amortized over a weighted-average remaining requisite service period of {months} months.',
            [phrase(l, node_start, 'As of December&#160;31, 2024', 'months.')]))
    claims['LLY', 'stock_compensation'].append(c('PA_zero_remaining',
        'At December 31, 2024 there was no remaining unrecognized compensation cost for PAs because Lilly discontinued that program; no nonzero aggregate or aggregate recognition period is inferred.',
        [phrase(l, 2389223, 'As of December 31, 2024', 'discontinued the program.')]))

    summaries = {
        ('PFE', 'currency_growth'): ('supported', 'Reported and operational revenue growth are both rounded to 7%, but the source separately identifies a $349 million unfavorable FX effect. Operational means excluding FX; the 2024 report uses recast royalty-inclusive comparative revenues.'),
        ('PFE', 'segment_recast'): ('supported', 'Biopharma remains the only reportable segment, but its segment-earnings cost allocation changed in 2024 and prior-period segment information was explicitly reclassified. Separately, PC1 and Pfizer Ignite became separate operating segments.'),
        ('PFE', 'free_cash_flow'): ('unresolved', 'No issuer-defined 2024 free-cash-flow measure or reconciliation was located in the bounded report pair. Operating cash flow and property/equipment purchases are disclosed, but subtracting them would invent an issuer definition. This search outcome is not proof of global absence.'),
        ('PFE', 'stock_compensation'): ('supported', 'Unrecognized pre-tax costs/recognition years: TSRUs $270m/2.1; RSUs $214m/1.8; PPSs $107m/1.9; PSAs $40m/1.7; options $4m/1.7. No BPAs outstanding. Preserve award distinctions; no aggregate computed.'),
        ('LLY', 'currency_growth'): ('partial', 'Reported 2024 growth is 32%. The company provides volume, price and FX components with rounding caveats; no explicitly defined constant-currency or operational-growth metric was located in this report pair. Do not rename the volume-plus-price sum as a disclosed measure.'),
        ('LLY', 'segment_recast'): ('supported', 'The single worldwide pharmaceutical segment persists. The 2024 report adopts expanded segment disclosure rules, with retrospective application described prospectively in the 2023 report. No reorganization or comparative remeasurement is inferred.'),
        ('LLY', 'free_cash_flow'): ('unresolved', 'No issuer-defined 2024 free-cash-flow measure or reconciliation was located in the bounded report pair. Ordinary liquidity discussion reports operating cash flow and capital expenditures separately; an analyst subtraction is not an issuer-disclosed FCF measure.'),
        ('LLY', 'stock_compensation'): ('supported', 'At year end: nonvested RSUs $485.1m over 23 months; SVAs $61.4m over 21 months; RVAs $27.5m over 22 months. PAs have no remaining unrecognized cost after program discontinuation. No aggregate constructed.'),
    }
    searches = {
        'currency_growth': [r'constant[ -]currency', r'operational', r'foreign exchange', r'change in revenue', r'Total revenues'],
        'segment_recast': [r'reportable segment', r'operating segment', r'\brecast', r'\breclassif', r'retrospect', r'2023-07'],
        'free_cash_flow': [r'free[ -]cash[ -]flow', r'\bFCF\b', r'net cash provided', r'capital expenditures', r'cash flow'],
        'stock_compensation': [r'unrecognized', r'not yet recognized', r'weighted.average', r'share.based', r'stock.based'],
    }
    review = {
        ('PFE', 'currency_growth'): '2024 MD&A definitions, total-revenue overview, royalty-reclassification paragraph, and 2024-versus-2023 worldwide operational/FX bridge.',
        ('PFE', 'segment_recast'): 'Both reports: business and organization summary, Note 1 organization, Note 17 operating segments, earnings-cost allocations, and comparative presentation statements.',
        ('PFE', 'free_cash_flow'): 'Both reports full XML-character-data keyword search; 2024 MD&A non-GAAP definitions, cash-flow analysis (1962167:1976072), liquidity discussion and consolidated cash-flow statement reviewed.',
        ('PFE', 'stock_compensation'): '2024 Note 13 award definitions, nonvested-unrecognized-cost row, recognition-period row, award headers and 2024 columns; checked BPA statement.',
        ('LLY', 'currency_growth'): 'Both reports full XML-character-data keyword search; 2024 results-of-operations revenue table, change-components table, rounding note and FX-risk explanation reviewed.',
        ('LLY', 'segment_recast'): 'Both reports organization paragraphs and accounting-standard adoption discussion; 2024 Note 19 segment description and comparative column headers reviewed.',
        ('LLY', 'free_cash_flow'): 'Both reports full XML-character-data keyword search; 2024 MD&A financial-condition/liquidity section (1133059:1142525) and consolidated cash-flow statement reviewed.',
        ('LLY', 'stock_compensation'): '2024 Note 12 stock-based compensation program descriptions and terminal unrecognized-cost/remaining-period sentences for RSUs, SVAs, PAs and RVAs.',
    }
    records = []
    for item in protocol['items']:
        key = item['history_id'], item['family']
        if key not in summaries:
            continue
        status, summary = summaries[key]
        limitations = ['Assistant-authored source inspection; not independent human gold, model prediction, held-out evidence, or a retrieval advantage.',
                       'Observed alternative witness sets are recorded where found, but are not an exhaustive semantic enumeration.']
        if item['family'] == 'free_cash_flow':
            limitations.append('Search failure does not authorize an insufficient-evidence gold label or an external coverage-clearance decision; no FCF answer claim admitted.')
        if item['family'] == 'currency_growth' and item['history_id'] == 'LLY':
            limitations.append('Reported-growth/decomposition claims are supported, but the requested issuer-defined constant-currency measure remains unresolved.')
        if item['family'] == 'stock_compensation':
            limitations.append('Typed control: amounts and period values occur in Inline XBRL; query/parser failure alone would not establish a new semantic retrieval task.')
        records.append({k: item[k] for k in ('item_id', 'history_id', 'family', 'question')} | {
            'status': status, 'answer_summary': summary,
            'claims': claims.get(key, []), 'review_scope': review[key],
            'search_log': [{'source_file': name, 'query_regex': pattern,
                            'scope': 'normalized complete XML character data; includes tagged and hidden data',
                            'match_count': len(re.findall(pattern, flat[name], re.I))}
                           for name in item['source_files'] for pattern in searches[item['family']]],
            'limitations': limitations})
    assert len(records) == 8
    return {'schema': 'source_questions_pfe_lly_v24', 'protocol_sha256': PROTOCOL_SHA256,
            'sources': [{'filename': name, 'sha256': digest(raw)} for name, raw in sources.items()],
            'records': records, 'model_assisted_review': True, 'human_gold': False,
            'retrieval_outcomes_viewed_before_reference': False,
            'source_inspection_note': 'Frozen source-reviewed locators; no predictions, scores or automatic rankings read. References require a separate source review before admission.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'results/source_questions_pfe_lly_v24.json')
    args = parser.parse_args()
    result = build(args.external_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'records': len(result['records']), 'statuses': {status: sum(r['status'] == status for r in result['records']) for status in ('supported', 'partial', 'unresolved', 'not_applicable')}, 'output_sha256': digest(args.output.read_bytes())}))
