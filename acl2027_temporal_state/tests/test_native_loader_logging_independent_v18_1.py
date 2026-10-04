"""Independent logging-only review controls: no freeze, native run or resource poll."""
import ast
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import native_loader_v18 as parent
import native_loader_v18_1 as child


class LoggingIndependentControls(unittest.TestCase):
    def test_complete_module_ast_has_only_disclosed_changes(self):
        before=ast.parse((ROOT/'scripts/native_loader_v18.py').read_text())
        after=ast.parse((ROOT/'scripts/native_loader_v18_1.py').read_text())
        def without_recipe(tree):
            tree.body=[n for n in tree.body if not (isinstance(n,ast.Assign) and
                       any(isinstance(t,ast.Name) and t.id=='RECIPE' for t in n.targets))]
            return tree
        class Normalize(ast.NodeTransformer):
            command_functions=0
            command_calls=0
            added_checks=0
            def visit_FunctionDef(self,node):
                if node.name=='command':
                    expected=ast.parse("def command(c):\n    return native.command(c) + ['--log-verbosity', '4']\n").body[0]
                    if ast.dump(node)!=ast.dump(expected):raise AssertionError('unexpected_command_function')
                    self.command_functions+=1
                    return None
                return self.generic_visit(node)
            def visit_Expr(self,node):
                expected=ast.parse("require(p['native_log_verbosity'] == 4, 'logging_observation_recipe_changed')").body[0]
                if ast.dump(node)==ast.dump(expected):
                    self.added_checks+=1
                    return None
                return self.generic_visit(node)
            def visit_Call(self,node):
                if isinstance(node.func,ast.Name) and node.func.id=='command':
                    node.func=ast.Attribute(value=ast.Name(id='native',ctx=ast.Load()),attr='command',ctx=ast.Load())
                    self.command_calls+=1
                return self.generic_visit(node)
            def visit_Constant(self,node):
                replacements={
                    'configs/native_loader_v18_1.json':'configs/native_loader_v18.json',
                    'native_loader_profile_v18_1':'native_loader_profile_v18',
                    'native_loader_frozen_v18_1':'native_loader_frozen_v18',
                    'native_loader_receipt_v18_1':'native_loader_receipt_v18'}
                if isinstance(node.value,str) and node.value in replacements:node.value=replacements[node.value]
                return node
        normalizer=Normalize();after=normalizer.visit(without_recipe(after))
        self.assertEqual((normalizer.command_functions,normalizer.command_calls,normalizer.added_checks),(1,1,1))
        self.assertEqual(ast.dump(after),ast.dump(without_recipe(before)))

    def test_recipe_retains_all_parent_pins_with_exact_declared_additions(self):
        additions={'scripts/native_loader_v18_1.py','tests/test_native_loader_v18_1.py',
                   'configs/native_loader_v18_1.json','results/native_loader_attempt_v18.json',
                   'results/native_loader_terminal_review_v18.json','results/native_rollback_observation_diagnosis_v18.json',
                   'docs/native_rollback_observation_diagnosis_v18.txt','docs/native_loader_logging_amendment_v18_1.txt',
                   'results/native_loader_logging_controls_v18_1.json','results/native_loader_independent_review_v18_1.json'}
        self.assertEqual(set(child.RECIPE)-set(parent.RECIPE),additions)
        self.assertEqual([p for p in child.RECIPE if p not in additions],parent.RECIPE)
        self.assertEqual(len(child.RECIPE),len(set(child.RECIPE)))

    def test_profile_changes_only_version_destination_ancestry_and_logging_metadata(self):
        before=json.loads(parent.PROFILE.read_text());after=json.loads(child.PROFILE.read_text())
        changed={k for k in before if before[k]!=after.get(k)}
        self.assertEqual(changed,{'schema_version','attempt_external_path','public_receipt_path',
                                  'parent_failure_sha256','parent_frozen_protocol_sha256'})
        self.assertEqual(set(after)-set(before),{'native_log_verbosity','logging_diagnosis_sha256','resource_policy_unchanged_from_v18'})
        self.assertEqual(after['native_log_verbosity'],4)
        self.assertIs(after['resource_policy_unchanged_from_v18'],True)
        self.assertEqual(after['parent_failure_sha256'],hashlib.sha256((ROOT/'results/native_loader_attempt_v18.json').read_bytes()).hexdigest())
        self.assertEqual(after['logging_diagnosis_sha256'],hashlib.sha256((ROOT/'results/native_rollback_observation_diagnosis_v18.json').read_bytes()).hexdigest())
        self.assertEqual(after['parent_frozen_protocol_sha256'],json.loads((ROOT/'results/native_loader_attempt_v18.json').read_text())['protocol_sha256'])

    def test_effective_argv_adds_exactly_two_arguments_and_keeps_native_config(self):
        c=json.loads((ROOT/'configs/binding_reader_v16.json').read_text());original=deepcopy(c)
        inherited=parent.native.command(c)
        actual=child.command(c)
        self.assertEqual(actual[:-2],inherited)
        self.assertEqual(actual[-2:],['--log-verbosity','4'])
        self.assertEqual(actual.count('--log-verbosity'),1)
        self.assertEqual(c,original)
        self.assertEqual(hashlib.sha256((ROOT/'configs/binding_reader_v16.json').read_bytes()).hexdigest(),
                         '0171d13fcc30f444380c5f6a8a1eed31cd709eb86713222c21198b197f808038')

    def test_exact_eight_authored_request_bytes_unchanged_without_freezing(self):
        fixture=json.loads(child.CONTROLS.read_text())
        requests=[{'request_id':x['request_id'],'messages':child.native.request_messages(x)}
                  for x in child.native.authored_requests(fixture)]
        raw=(json.dumps(requests,indent=2)+'\n').encode()
        self.assertEqual(len(requests),8)
        self.assertTrue(all(set(r)=={'request_id','messages'} for r in requests))
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         '5440df6b81b9dcb070096d9702c11039b34c0f05b747e62813342095c2a27a34')

    def test_observed_zero_gate_still_rejects_missing_nonzero_or_mixed_values(self):
        tree=ast.parse(inspect.getsource(child.run))
        assignments=[n for n in ast.walk(tree) if isinstance(n,ast.Assign)
                     and any(isinstance(t,ast.Name) and t.id=='rollback' for t in n.targets)]
        checks=[n for n in ast.walk(tree) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call)
                and any(isinstance(a,ast.Constant) and a.value=='native_rollback_state_not_zero_or_unobserved'
                        for a in n.value.args)]
        self.assertEqual((len(assignments),len(checks)),(1,1))
        module=ast.Module(body=[assignments[0],checks[0]],type_ignores=[])
        code=compile(ast.fix_missing_locations(module),'<extracted unchanged observation gate>','exec')
        for text,passes in [('n_rs_seq = 0\n',True),('n_rs_seq = 0\nn_rs_seq=0',True),
                            ('no observation',False),('n_rs_seq=3',False),('n_rs_seq=0\nn_rs_seq=1',False)]:
            scope={'re':re,'require':child.require,'startup_log':text}
            with self.subTest(text=text):
                if passes:exec(code,scope)
                else:
                    with self.assertRaisesRegex(ValueError,'native_rollback_state_not_zero_or_unobserved'):exec(code,scope)

    def test_guards_cleanup_api_and_required_identity_logic_are_unchanged(self):
        for name in ('preflight_reason','owned_reason','signal_inner','watchdog','cleanup','api'):
            self.assertEqual(inspect.getsource(getattr(parent,name)),inspect.getsource(getattr(child,name)))
        self.assertIs(child.pressure,parent.pressure)
        self.assertIs(child.owned,parent.owned)
        self.assertEqual(child.ALLOWED,{('GET','health'),('GET','props'),('POST','apply-template'),('POST','tokenize')})
        with patch.object(child.urllib.request,'urlopen') as network:
            for endpoint in ('completion','completions','v1/chat/completions','embedding'):
                with self.assertRaisesRegex(ValueError,'endpoint_forbidden'):
                    child.api(0,'POST',endpoint,{},Path('/unused'),[])
            network.assert_not_called()

    def test_diagnosis_exact_source_hashes_and_relevant_logging_threshold(self):
        diagnosis=json.loads((ROOT/'results/native_rollback_observation_diagnosis_v18.json').read_text())
        sources={r['path']:r for r in diagnosis['primary_source_files']}
        for key in ('common/log.cpp','common/log.h','common/arg.cpp'):
            row=sources[key];raw=Path(row['captured_path']).read_bytes()
            self.assertEqual(len(raw),row['bytes']);self.assertEqual(hashlib.sha256(raw).hexdigest(),row['sha256'])
        cpp=Path(sources['common/log.cpp']['captured_path']).read_text()
        header=Path(sources['common/log.h']['captured_path']).read_text()
        args=Path(sources['common/arg.cpp']['captured_path']).read_text()
        self.assertIn('case GGML_LOG_LEVEL_INFO:  return LOG_LEVEL_TRACE;',cpp)
        self.assertIn('if (verbosity <= common_log_verbosity_thold)',cpp)
        self.assertIn('#define LOG_LEVEL_TRACE  4',header)
        self.assertIn('#define LOG_LEVEL_INFO   3',header)
        self.assertIn('{"-lv", "--verbosity", "--log-verbosity"}',args)


if __name__=='__main__':unittest.main()
