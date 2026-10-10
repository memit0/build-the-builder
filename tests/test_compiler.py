"""Execute the emitted snippets, not reimplementations of the guards."""
import copy
from contextlib import nullcontext
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'builder'), str(ROOT)]
import api_spec, extract, generate, lint, policy
import build


class CompilerTests(unittest.TestCase):
    def setUp(self):
        self.api = api_spec.load(ROOT / 'tools/openapi.json')
        self.rules = policy.legacy(json.loads((ROOT / 'dev/out/ab.rules.json').read_text()))
        self.rules['execution'] = {
            'refused_note_reason': 'refused',
            'minimum_offer': {'limit': 1, 'scope': 'per_product', 'repeat_on_attempts': [2], 'source': 'offer the floor, once'},
            'intent_actions': [dict(intent='dispute', stop_negotiation=True, allow_customer_proposal=False, forbid_promise=True, source='do not take a promise')],
        }
        self.wf = generate.generate(self.rules, self.api)
        self.v = {'now': '2026-10-23T11:00'}
        self.run_node('setup')
        self.v['id_state'] = 'verified'
        self.v['products'] = json.dumps([
            dict(product_id='p1', type='credit_card', past_due=800, days_late=10, minimum_payment=400, card_last4='1234'),
            dict(product_id='p2', type='personal_loan', past_due=900, days_late=5, instalment_amount=300),
        ])
        self.run_node('prepare_deal')
        self.products = json.loads(self.v['x_prod'])
        self.tools = {t['function']['name']: t for t in self.wf['nodes']['negotiate']['tools']}
        self.f = generate.config(self.rules, self.api)['fields']

    def run_node(self, name):
        source = self.wf['nodes'][name]['pre_actions'][0]['function']['parameters']['properties']['code']['default']
        exec(source, {'vars': self.v})

    def step(self, action, pid='p1', **kw):
        for key in ('action', 'product_id', 'amount', 'date', 'too_much', 'intent'):
            self.v['policy_step_args.' + key] = dict(action=action, product_id=pid, **kw).get(key)
        source = self.tools['policy_step']['function']['parameters']['properties']['code']['default']
        exec(source, {'vars': self.v})
        return self.v['policy_instruction']

    def promise(self, amount=800, date=None, pid='p1'):
        cfg = {'url': self.api['base'] + '/create_promise', 'body': {
            self.f['p_pid']: pid, self.f['p_amount']: amount, self.f['p_date']: date or self.products[0]['valid'][0]}}
        exec(self.tools['create_promise']['pre_process_code'], {'vars': self.v, 'config': cfg})
        return cfg

    def agreed(self, amount=800, date=None, pid='p1'):
        self.step('stage', pid, amount=amount, date=date or self.products[0]['valid'][0])
        self.step('readback', pid)
        self.step('confirm', pid)

    def test_valid_generated_workflow(self):
        self.assertEqual(extract.validate(self.rules, self.api), [])
        self.assertEqual(lint.lint(self.wf, self.rules, self.api), [])

    def test_unconfirmed_promise_blocked(self):
        self.assertEqual(self.promise()['url'], '')
        self.assertIn('confirmed', self.v['promise_block'])

    def test_confirm_requires_readback(self):
        self.step('stage', amount=800, date=self.products[0]['valid'][0])
        self.assertIn('blocked', self.step('confirm'))
        self.assertEqual(self.promise()['url'], '')

    def test_confirmed_exact_values_allowed(self):
        self.agreed()
        self.assertTrue(self.promise()['url'])
        self.assertEqual(self.v['promise_block'], '')

    def test_changed_values_require_new_confirmation(self):
        self.agreed()
        self.step('change', amount=600, date=self.products[0]['valid'][0])
        self.assertEqual(self.promise(amount=600)['url'], '')
        self.step('readback'); self.step('confirm')
        self.assertTrue(self.promise(amount=600)['url'])

    def test_invalid_change_invalidates_previous_confirmation(self):
        self.agreed()
        self.step('change', amount=1, date=self.products[0]['valid'][0])
        self.assertEqual(self.promise()['url'], '')

    def test_change_invalidates_combined_confirmation(self):
        self.step('stage', 'p1', amount=800, date=self.products[0]['valid'][0])
        self.step('stage', 'p2', amount=900, date=self.products[1]['valid'][0])
        self.step('readback'); self.step('confirm')
        self.assertEqual(len(json.loads(self.v['x_confirmed'])), 2)
        self.step('change', 'p2', amount=600, date=self.products[1]['valid'][0])
        self.assertEqual(self.promise()['url'], '')

    def test_nonfinite_and_below_floor_amounts_blocked(self):
        self.agreed()
        for amount in (float('nan'), float('inf'), 'not a number', 399, 801):
            with self.subTest(amount=amount):
                self.assertEqual(self.promise(amount=amount)['url'], '')

    def test_floor_and_full_boundaries(self):
        for amount in (400, 800):
            self.agreed(amount=amount)
            self.assertTrue(self.promise(amount=amount)['url'])

    def test_holiday_and_post_deadline_blocked(self):
        self.agreed()
        for date in ('2026-10-26', '2026-10-24', '2030-01-01', '2026-10-22', 'bad'):
            self.assertEqual(self.promise(date=date)['url'], '')

    def test_holiday_correction_uses_next_business_day(self):
        self.assertIn('2026-10-27', self.step('stage', amount=800, date='2026-10-26'))
        self.assertEqual(json.loads(self.v['x_confirmed']), {})

    def test_late_date_correction_uses_product_deadline(self):
        self.assertIn(self.products[0]['deadline'], self.step('stage', amount=800, date='2030-01-01'))

    def test_identity_and_product_prerequisites(self):
        self.agreed()
        self.v['id_state'] = 'pending'
        self.assertEqual(self.promise()['url'], '')
        self.v['id_state'] = 'verified'
        self.assertEqual(self.promise(pid='invented')['url'], '')

    def test_recorded_promise_cannot_repeat(self):
        self.agreed()
        self.v['x_recorded'] = 'p1'
        self.assertEqual(self.promise()['url'], '')

    def test_recording_retry_budget_enforced(self):
        self.agreed()
        self.promise()
        for _ in range(2):
            exec(self.tools['create_promise']['post_process_code'], {'vars': self.v, 'result': {'data': {'ok': False}}})
        self.assertEqual(self.promise()['url'], '')
        self.assertIn('exhausted', self.v['promise_block'])

    def test_minimum_offer_limit_and_explicit_exception(self):
        self.assertNotIn('blocked', self.step('offer', too_much=True))
        self.assertIn('blocked', self.step('offer', too_much=True))
        self.step('refuse')
        self.assertNotIn('blocked', self.step('offer', too_much=True))
        self.step('refuse')
        self.assertIn('exhausted', self.step('offer'))

    def test_discretionary_minimum_cannot_repeat(self):
        day = self.products[0]['valid'][0]
        self.assertNotIn('blocked', self.step('minimum', date=day))
        self.assertIn('limit', self.step('minimum', date=day))
        self.assertNotIn('blocked', self.step('minimum', 'p2', date=day))

    def test_per_call_minimum_scope(self):
        self.rules['execution']['minimum_offer']['scope'] = 'per_call'
        self.wf = generate.generate(self.rules, self.api)
        self.tools = {t['function']['name']: t for t in self.wf['nodes']['negotiate']['tools']}
        day = self.products[0]['valid'][0]
        self.step('minimum', date=day)
        self.assertIn('limit', self.step('minimum', 'p2', date=day))

    def test_invalid_offer_does_not_spend_budget(self):
        self.assertIn('blocked', self.step('minimum', date='2030-01-01'))
        self.assertNotIn('blocked', self.step('minimum', date=self.products[0]['valid'][0]))

    def test_refusal_requires_issued_attempt(self):
        self.assertIn('blocked', self.step('refuse'))
        self.step('offer'); self.step('refuse')
        self.assertIn('blocked', self.step('refuse'))

    def test_dispute_disables_negotiation_and_recording(self):
        self.agreed()
        self.step('intent', intent='dispute')
        self.assertIn('blocked', self.step('offer'))
        self.assertIn('blocked', self.step('stage', amount=800, date=self.products[0]['valid'][0]))
        self.assertEqual(self.promise()['url'], '')

    def test_note_cannot_bypass_attempts_with_fabricated_count(self):
        def note():
            cfg = {'url': self.api['base'] + '/add_note', 'body': {self.f['n_pid']: 'p1', self.f['n_reason']: 'refused', 'attempts_made': 999}}
            exec(self.tools['add_note']['pre_process_code'], {'vars': self.v, 'config': cfg})
            return cfg
        for _ in range(10):
            self.assertEqual(note()['url'], '')
        for _ in self.products[0]['atts']:
            self.step('offer'); self.step('refuse')
        self.assertTrue(note()['url'])
        self.assertNotIn('attempts_made', note()['body'])

    def test_callback_missing_calendar_fails_closed(self):
        tool = self.wf['nodes']['opening']['tools'][0]
        self.v['cb_days'] = ''
        cfg = {'url': self.api['base'] + '/schedule_callback', 'body': {self.f['cb_date']: '2026-10-23', self.f['cb_time']: '12:00'}}
        exec(tool['pre_process_code'], {'vars': self.v, 'config': cfg})
        self.assertEqual(cfg['url'], '')

    def test_tools_unavailable_after_close(self):
        self.agreed()
        self.v['call_closed'] = 'yes'
        self.assertEqual(self.promise()['url'], '')

    def test_wrong_answer_budget_and_hint(self):
        self.v['id_state'] = 'pending'
        tool = self.wf['nodes']['security_check']['tools'][0]
        self.v['x_id_field'] = self.rules['identity']['questions'][0]['field']
        for _ in range(self.rules['identity']['max_wrong_total']):
            exec(tool['post_process_code'], {'vars': self.v, 'result': {'data': {'verified': False, 'wrong_answers_left': 99}}})
        self.assertEqual(self.v['id_state'], 'failed')
        self.assertEqual(self.v['x_wrong'], self.rules['identity']['max_wrong_total'])

    def test_raw_all_verified_cannot_bypass_question_sequence(self):
        self.v['id_state'] = 'pending'
        self.v['x_id_field'] = self.rules['identity']['questions'][0]['field']
        tool = self.wf['nodes']['security_check']['tools'][0]
        exec(tool['post_process_code'], {'vars': self.v, 'result': {'data': {'verified': True, 'all_verified': True}}})
        self.assertNotEqual(self.v['id_state'], 'verified')

    def test_missing_clock_does_not_fall_back_to_example_or_real_time(self):
        self.v.pop('now')
        self.run_node('setup')
        self.assertEqual(self.v['setup_state'], 'error')
        self.run_node('prepare_deal')
        self.assertEqual(self.v['acct_state'], 'error')

    def test_schema_and_semantic_validation(self):
        for mutation in (
            lambda r: r['products'][0].update(floor_percent=500),
            lambda r: r['calendar'].update(business_weekdays=[7]),
            lambda r: r['bands'][0].update(deadline_offset=-1),
            lambda r: r['identity'].update(max_wrong_total=0),
            lambda r: r['persuasion']['attempts'][0].update(date_rule='other'),
            lambda r: r.pop('bands'),
        ):
            r = copy.deepcopy(self.rules); mutation(r)
            self.assertTrue(extract.validate(r, self.api))
            with self.assertRaises(ValueError):
                generate.generate(r, self.api)

    def test_coverage_does_not_claim_confirmation_is_deterministic(self):
        self.rules['requirements'] = [dict(id='R1', source='confirm', critical=True, status='active', paths=['confirmation.readback_question'])]
        report = policy.coverage(self.rules, self.wf)
        self.assertEqual(report['requirements'][0]['coverage'], 'weak')
        self.wf['nodes']['negotiate']['tools'] = []
        self.wf['nodes']['policy_result']['tools'] = []
        report = policy.coverage(self.rules, self.wf)
        self.assertEqual(report['requirements'][0]['evidence'][0]['mechanism'], 'prompt')

    def test_source_literal_is_safely_encoded(self):
        source = generate.code({'text': "O'Brien ''' \\n backslash \\"}, ['text'], "vars['out'] = C['text']")
        v = {}; exec(source, {'vars': v})
        self.assertEqual(v['out'], "O'Brien ''' \\n backslash \\")

    def test_security_questions_cannot_be_skipped(self):
        self.v['id_state'] = 'pending'
        tool = self.wf['nodes']['security_check']['tools'][0]
        cfg = {'url': 'https://unused/verify_identity', 'body': {self.f['id_field']: self.rules['identity']['questions'][1]['field'], self.f['id_answer']: '1234'}}
        exec(tool['pre_process_code'], {'vars': self.v, 'config': cfg})
        self.assertEqual(cfg['url'], '')

    def test_noncounting_identity_hint(self):
        self.v['id_state'] = 'pending'
        self.v['x_id_field'] = self.rules['identity']['questions'][0]['field']
        tool = self.wf['nodes']['security_check']['tools'][0]
        exec(tool['post_process_code'], {'vars': self.v, 'result': {'data': {'verified': False, 'hint': 'day_month_swapped'}}})
        self.assertEqual(self.v['x_wrong'], 0)

    def test_unmapped_critical_requirement_fails(self):
        self.rules['requirements'] = [dict(id='R1', source='new policy', critical=True, status='active', paths=['unmapped_rules.0'])]
        self.rules['unmapped_rules'] = ['new policy']
        self.assertTrue(extract.validate(self.rules, self.api))

    def test_incomplete_extraction_audit_stops_build(self):
        inventory = {'requirements': [dict(id='R1', source='brief', critical=True, status='active')]}
        audit = {'requirements': [], 'errors': ['missing verification']}
        with patch.object(extract, '_ask', side_effect=[inventory, self.rules, self.rules, audit]):
            with self.assertRaisesRegex(ValueError, 'Requirement audit failed'):
                extract.extract('brief', self.api, log=lambda e: None)

    def test_audit_cannot_downgrade_critical_requirement(self):
        req = dict(id='R1', source='brief', critical=True, status='active')
        inventory = {'requirements': [req]}
        audit = {'requirements': [{**req, 'critical': False, 'paths': ['identity.max_wrong_total']}], 'errors': []}
        with patch.object(extract, '_ask', side_effect=[inventory, self.rules, self.rules, audit]):
            with self.assertRaisesRegex(ValueError, 'changed during audit'):
                extract.extract('brief', self.api, log=lambda e: None)


class BuildGates(unittest.TestCase):
    def run_build(self, rules, **mocks):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            sheet = root / 'rules.json'
            sheet.write_text(json.dumps(rules))
            argv = ['build.py', str(ROOT / 'briefs/brief-a.md'), str(ROOT / 'tools/openapi.json'), '--rules', str(sheet), '--slot', 'test-gate']
            with patch.object(sys, 'argv', argv), patch.object(build, 'ROOT', root), patch.object(build, 'Trace', return_value=lambda e: None), patch.object(build.freya, 'api', side_effect=AssertionError('Network disabled in unit tests')), patch.object(build.freya, 'save_workflow') as save:
                with (patch.multiple(build.freya, **mocks) if mocks else nullcontext()):
                    with self.assertRaises((ValueError, RuntimeError)):
                        build.main()
                save.assert_not_called()

    def test_invalid_rules_stop_before_save(self):
        r = json.loads((ROOT / 'dev/out/a.rules.json').read_text())
        r['products'][0]['floor_percent'] = 500
        self.run_build(r)

    def test_platform_error_stops_before_save(self):
        r = json.loads((ROOT / 'dev/out/a.rules.json').read_text())
        from unittest.mock import Mock
        self.run_build(r, validate_workflow=Mock(side_effect=RuntimeError('rejected')))

    def test_platform_false_result_stops_before_save(self):
        r = json.loads((ROOT / 'dev/out/a.rules.json').read_text())
        from unittest.mock import Mock
        self.run_build(r, validate_workflow=Mock(return_value={'valid': False}))

    def test_lint_error_stops_before_platform(self):
        r = json.loads((ROOT / 'dev/out/a.rules.json').read_text())
        with patch.object(build.lint, 'lint', return_value=['bad graph']):
            self.run_build(r)

    def test_no_save_build_never_calls_platform(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            trace = Mock()
            trace.path = pathlib.Path(tmp) / 'trace.jsonl'
            argv = ['build.py', str(ROOT / 'briefs/brief-a.md'), str(ROOT / 'tools/openapi.json'), '--rules', str(ROOT / 'dev/out/a.rules.json'), '--slot', 'offline', '--no-save']
            with patch.object(sys, 'argv', argv), patch.object(build, 'ROOT', pathlib.Path(tmp)), patch.object(build, 'Trace', return_value=trace), patch.object(build.freya, 'api', side_effect=AssertionError('network disabled')) as api, patch.object(build.freya, 'save_workflow') as save:
                build.main()
                api.assert_not_called()
                save.assert_not_called()
                self.assertTrue((pathlib.Path(tmp) / 'out/offline.coverage.json').exists())


if __name__ == '__main__':
    unittest.main()
