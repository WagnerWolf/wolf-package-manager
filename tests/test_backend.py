import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wolfpkg import backend as b

class BackendTests(unittest.TestCase):
    def test_update_parse(self):
        self.assertEqual([(u.name,u.available) for u in b.parse_updates('foo 1.0 -> 2.0\nbar 2 -> 3\n','AUR')],
                         [('foo','2.0'),('bar','3')])
    def test_preferences_backward_compat(self):
        with tempfile.TemporaryDirectory() as t:
            old=Path(t)/'prefs.json'
            old.write_text('{"interval_minutes":30,"notifications":false}')
            prefs=b.Preferences.load(old)
            self.assertEqual(prefs.interval_minutes,30)
            self.assertFalse(prefs.notifications)
            self.assertTrue(prefs.aur_automatic)
            prefs.save(old)
            self.assertEqual(old.stat().st_mode&0o777,0o600)
            self.assertEqual(b.Preferences.load(old),prefs)
    def test_default_interval(self):
        self.assertEqual(b.Preferences.load(Path('/file-that-does-not-exist')).interval_minutes,120)
    def test_clamps(self):
        with tempfile.TemporaryDirectory() as t:
            f=Path(t)/'f';f.write_text('{"interval_minutes":2}')
            self.assertEqual(b.Preferences.load(f).interval_minutes,15)
    def test_parse_repo_and_chaotic(self):
        text='''extra/firefox 145-1 [installed]
    The browser
chaotic-aur/something 1.2-2
    third party build
'''
        got=b.parse_pacman_search(text)
        self.assertEqual(len(got),2)
        self.assertEqual((got[0].source,got[0].repo,got[0].name,got[0].installed),
                         ('Repositório','extra','firefox',True))
        self.assertEqual(got[1].description,'third party build')
    def test_parse_aur(self):
        text='''aur/extra-good-git 2.4-1 (+30 0.55)
    description here
'''
        got=b.parse_yay_search(text)
        self.assertEqual((got[0].source,got[0].name,got[0].description),('AUR','extra-good-git','description here'))
    @patch('wolfpkg.backend._query')
    def test_repo_search_command(self,q):
        q.return_value=subprocess.CompletedProcess([],0,'extra/foo 1\n    Foo!\n','')
        got=b.search_repositories('foo')
        self.assertEqual(got[0].repo,'extra')
        self.assertEqual(q.call_args.args[0],['pacman','-Ss','--','foo'])
    @patch('wolfpkg.backend._query')
    def test_aur_search_command(self,q):
        q.return_value=subprocess.CompletedProcess([],0,'aur/foo 1\n    Foo!\n','')
        got=b.search_aur('foo')
        self.assertEqual(got[0].source,'AUR')
        self.assertIn('--aur',q.call_args.args[0])
    def test_query_limits(self):
        for s in ('','a','\nfoo','b'*91):
            with self.assertRaises(ValueError): b.search_aur(s)
    def test_repo_package_avoids_partial_upgrade(self):
        p=b.Package('Repositório','chaotic-aur','app','1','')
        cmds=b.installation_commands(p)
        self.assertEqual(cmds,(('sudo','-A','pacman','-Syu','--needed','--noconfirm','chaotic-aur/app'),))
    def test_aur_install_as_user_and_sequential(self):
        p=b.Package('AUR','aur','foo-bin','1','')
        cmds=b.installation_commands(p)
        self.assertEqual(cmds[0][:4],('sudo','-A','pacman','-Syu'))
        self.assertEqual(cmds[1][:4],('yay','-S','--aur','--needed'))
        self.assertNotIn('sudo',cmds[1])
        self.assertIn('--sudoflags=-A',cmds[1])
    def test_aur_automatic_config(self):
        cmds=b.upgrade_commands(True)
        self.assertEqual(cmds[0][:4],('sudo','-A','pacman','-Syu'))
        self.assertIn('--noconfirm',cmds[0]);self.assertIn('--noconfirm',cmds[1])
        self.assertIn('--answerdiff',cmds[1])
        self.assertNotIn('--noconfirm',b.upgrade_commands(False)[1])
    def test_package_injection_refused(self):
        for name in (';rm -rf /','--overwrite','a b','../../etc','$(echo 1)'):
            with self.assertRaises(ValueError):
                b.installation_commands(b.Package('AUR','aur',name,'1',''))
    def test_aur_invalid_repo_refused(self):
        with self.assertRaises(ValueError):
            b.installation_commands(b.Package('AUR','evil','hello','1',''))
    @patch('wolfpkg.backend._query')
    def test_checkupdates_code_2_none(self,q):
        q.side_effect=[subprocess.CompletedProcess([],2,'',''),
                       subprocess.CompletedProcess([],0,'','')]
        self.assertEqual(b.check_repository_updates(),[])
        self.assertEqual(q.call_count,2)
    @patch('wolfpkg.backend._query')
    def test_aur_updates_error(self,q):
        q.return_value=subprocess.CompletedProcess([],1,'','timeout')
        with self.assertRaises(b.CheckError):b.check_aur_updates()
    def test_space_snapshot(self):
        self.assertIn('GiB',b.space_snapshot())
    def test_askpass_script_is_executable(self):
        p=Path(__file__).resolve().parents[1]/'wolfpkg/askpass.sh'
        self.assertTrue(p.stat().st_mode&0o100)
        self.assertIn('askpass.py',p.read_text())

if __name__=='__main__':unittest.main()
