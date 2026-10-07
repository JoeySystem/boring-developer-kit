import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from provisioner import official_recovery as official
from provisioner.authority_update_server import AuthorityUpdateState

class RegistrationTests(unittest.TestCase):
    def test_actual_bundled_signature(self):
        self.assertEqual(official.verified_manifest()['build_id'], '20261007.01-gae3b06fb-dirty')

    def test_wrong_runtime_build_never_writes(self):
        from unittest.mock import Mock
        run = Mock()
        with self.assertRaisesRegex(ValueError, '回读版本'):
            official.register_with_console('other-build',run)
        run.assert_not_called()

    def test_preserves_other_records_and_replaces_exact_build(self):
        manifest=official.verified_manifest()
        records=[{'build_id':'old-build'},dict(manifest,release_notes='old')]
        def run(args,**kw):
            if args[1]=='read':return SimpleNamespace(returncode=0,stdout=json.dumps(records),stderr='')
            self.assertEqual(args[1:5],['write',official.DOMAIN,official.KEY,'-string'])
            records[:]=json.loads(args[5])
            return SimpleNamespace(returncode=0,stdout='',stderr='')
        with patch.object(official.sys,'platform','darwin'):
            official.register_with_console(manifest['build_id'],run)
        self.assertEqual(records,[{'build_id':'old-build'},manifest])

    def test_missing_preferences_can_register(self):
        manifest=official.verified_manifest();records=None
        def run(args,**kw):
            nonlocal records
            if args[1]=='read':
                return SimpleNamespace(returncode=1,stderr='The domain/default pair does not exist',stdout='') if records is None else SimpleNamespace(returncode=0,stdout=records,stderr='')
            records=args[5];return SimpleNamespace(returncode=0,stdout='',stderr='')
        with patch.object(official.sys,'platform','darwin'):
            official.register_with_console(manifest['build_id'],run)
        self.assertIn(manifest,json.loads(records))

    def test_invalid_existing_records_not_overwritten(self):
        calls=[]
        def run(args,**kw):
            calls.append(args);return SimpleNamespace(returncode=0,stdout='not json',stderr='')
        with self.assertRaises(ValueError):official.register_with_console(official.verified_manifest()['build_id'],run)
        self.assertEqual(len(calls),1)

    def test_only_bundled_recovery_used(self):
        state=AuthorityUpdateState()
        self.assertEqual(state.authority()['build_id'],official.verified_manifest()['build_id'])
        self.assertEqual(state._authority_path(),official.resource_root()/'releases/recovery')

    def test_no_port_reports_waiting_without_requiring_lit_screen(self):
        with patch('provisioner.authority_update_server.detect_devices',return_value=[]):
            scan=AuthorityUpdateState().scan()
        self.assertEqual(scan['readiness'],'waiting')
        self.assertIn('屏幕不亮',scan['message'])

class OutcomeTests(unittest.TestCase):
    def run_outcome(self, success, registration_error=None):
        from provisioner.release import ReleasePackage
        from provisioner.models import RuntimeVerification
        state=AuthorityUpdateState()
        state.jobs['test']={'progress':0}
        runtime=RuntimeVerification('/dev/test','BORING','303A','8360','wired-macro-pad-v1',
            'WMP-S3-MATRIX12-POWER-V2',official.verified_manifest()['build_id'],'CP01-TEST',33,'ota_1',False)
        batch=SimpleNamespace(success=success,value=SimpleNamespace(runtime=runtime,receipt_path=Path('/tmp/test.json')),
                              error='HELLO timeout',action_required=False)
        with patch('provisioner.authority_update_server.Provisioner'), \
             patch('provisioner.authority_update_server.SequentialBatch') as Batch, \
             patch('provisioner.authority_update_server.register_with_console',side_effect=registration_error) as register:
            Batch.return_value.run.return_value=[batch]
            state._run_job('test',ReleasePackage.open(state._authority_path()),SimpleNamespace(device='/dev/test'))
            return state.jobs['test'],register.call_count

    def test_unresponsive_device_never_registers_official_record(self):
        job,calls=self.run_outcome(False)
        self.assertEqual(job['status'],'failed')
        self.assertEqual(calls,0)
        self.assertIn('HELLO timeout',job['message'])

    def test_registration_failure_is_not_reported_as_complete(self):
        job,calls=self.run_outcome(True,ValueError('cannot write preferences'))
        self.assertEqual(job['status'],'action-required')
        self.assertEqual(calls,1)
        self.assertTrue(job['results'][0]['firmware_recovered'])

if __name__=='__main__':unittest.main()
