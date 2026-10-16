import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wolfpkg import backend as b

class UpdateConsistencyTests(unittest.TestCase):
    def test_repo_zero_and_local_zero(self):
        with patch.object(b, '_query', side_effect=[
            subprocess.CompletedProcess([], 2, '', ''),
            subprocess.CompletedProcess([], 0, '', '')]) as q:
            self.assertEqual(b.check_repository_updates(), [])
            self.assertEqual(q.call_args_list[0].args[0][0], 'checkupdates')
            self.assertEqual(q.call_args_list[1].args[0], ['pacman', '-Qu'])

    def test_repo_zero_but_local_reports_new_package(self):
        with patch.object(b, '_query', side_effect=[
            subprocess.CompletedProcess([], 2, '', ''),
            subprocess.CompletedProcess([], 0, 'linux 1 -> 2\n', '')]):
            with self.assertRaisesRegex(b.CheckError, 'divergentes'):
                b.check_repository_updates()

    def test_has_updates_skips_extra_query(self):
        with patch.object(b, '_query', return_value=subprocess.CompletedProcess([],0, 'linux 1 -> 2\n','')) as q:
            self.assertEqual(len(b.check_repository_updates()), 1)
            self.assertEqual(q.call_count, 1)

    def test_lock_blocks_check(self):
        calls=[]
        def f(): calls.append('called'); return []
        results, errors = b.check_all_updates(snapshot=lambda:(True,None,None),repo_check=f,aur_check=f)
        self.assertEqual(results, {})
        self.assertIn('Transação', errors)
        self.assertEqual(calls, [])

    def test_pacman_log_change_invalidates_late_notifications(self):
        snaps=iter([(False,1,100),(False,2,220)])
        r,errors=b.check_all_updates(snapshot=lambda:next(snaps),
             repo_check=lambda:[b.Update('Repositório','linux','1','2')],aur_check=lambda:[])
        self.assertEqual(r,{})
        self.assertIn('Transação',errors)

    def test_stable_snapshot_keeps_updates(self):
        r,errors=b.check_all_updates(snapshot=lambda:(False,1,100),
             repo_check=lambda:[b.Update('Repositório','linux','1','2')],aur_check=lambda:[])
        self.assertFalse(errors)
        self.assertEqual(r['Repositório'][0].name,'linux')

    def test_partial_failure_not_falsely_all_updated(self):
        def broken(): raise b.CheckError('mirror sem resposta')
        r,errors=b.check_all_updates(snapshot=lambda:(False,1,100),repo_check=broken,aur_check=lambda:[])
        self.assertIn('Repositório',errors)
        self.assertEqual(r['AUR'],[])

    def test_transaction_snapshot_no_write(self):
        with tempfile.TemporaryDirectory() as t:
            log=Path(t)/'pacman.log';lock=Path(t)/'db.lck';log.write_text('original')
            a=b.transaction_snapshot(log,lock);self.assertFalse(a[0]);self.assertEqual(a[2],8)
            lock.touch();self.assertTrue(b.transaction_snapshot(log,lock)[0])
            self.assertEqual(log.read_text(),'original')

if __name__=='__main__': unittest.main()
