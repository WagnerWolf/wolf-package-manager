"""Backend regressions for 0.3; never changes host packages or deletes locks."""
from pathlib import Path
import os, subprocess, sys, tempfile, time, unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wolfpkg import backend as b

class Version03(unittest.TestCase):
    def test_file_search_machine_format(self):
        content='extra\0openbsd-netcat\01.23\0usr/bin/nc\nextra\0something\01\0usr/share/man/nc\n'
        # Explicit version field to avoid Python's octal-escape ambiguity.
        content='extra\x00openbsd-netcat\x001.23\x00usr/bin/nc\nextra\x00something\x001\x00usr/share/man/nc\n'
        p=b.parse_file_matches(content,'nc')
        self.assertEqual([(x.repo,x.name) for x in p],[('extra','openbsd-netcat')])
        self.assertIn('Fornece o comando nc',p[0].description)

    @patch('wolfpkg.backend._query')
    def test_exact_command_search(self,query):
        query.return_value=subprocess.CompletedProcess([],0,'extra\x00openbsd-netcat\x001.23\x00usr/bin/nc\n','')
        packages=b.search_command('nc')
        self.assertEqual(packages[0].name,'openbsd-netcat')
        self.assertEqual(query.call_args.args[0],['pacman','-F','--machinereadable','--','nc'])

    @patch('wolfpkg.backend._query')
    def test_missing_files_index_instructions(self,query):
        query.return_value=subprocess.CompletedProcess([],1,'','no files database found')
        with self.assertRaisesRegex(b.CheckError,'pacman -Fy'):
            b.search_command('nc')

    def test_bad_command_rejected(self):
        for value in ('-command','$(malware)','rm -rf','../bad','a','nc;ls'):
            with self.assertRaises(ValueError):b.search_command(value)

    def test_batch_mixed_repo_aur(self):
        packages=[b.Package('AUR','aur','aurtool','1',''),
                  b.Package('Repositório','extra','firefox','1',''),
                  b.Package('Repositório','chaotic-aur','app','1',''),
                  b.Package('AUR','aur','aurtool','1','')]
        cmds=b.installation_batch_commands(packages)
        self.assertEqual(len(cmds),2)
        self.assertEqual(cmds[0][:4],('sudo','-A','pacman','-Syu'))
        self.assertIn('extra/firefox',cmds[0]);self.assertIn('chaotic-aur/app',cmds[0])
        self.assertEqual(cmds[1].count('aurtool'),1)
        self.assertNotIn('sudo',cmds[1])

    def test_batch_repo_only(self):
        commands=b.installation_batch_commands([b.Package('Repositório','extra','foo','1','')])
        self.assertEqual(len(commands),1)

    def test_batch_aur_only(self):
        commands=b.installation_batch_commands([b.Package('AUR','aur','foo','1','')])
        self.assertEqual(len(commands),2)
        self.assertEqual(commands[0][:4],('sudo','-A','pacman','-Syu'))

    def test_batch_invalid_blocked(self):
        with self.assertRaises(ValueError):b.installation_batch_commands([])
        with self.assertRaises(ValueError):b.installation_batch_commands([b.Package('AUR','aur','--danger','1','')])

    def test_lock_absent(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(b.lock_inspection(Path(t)/'db.lck')[0],'livre')

    @patch('wolfpkg.backend.shutil.which',return_value=None)
    @patch('wolfpkg.backend.subprocess.run')
    def test_lock_without_fuser_is_not_safe(self,run,which):
        run.return_value=subprocess.CompletedProcess([],0,'', '')
        with tempfile.TemporaryDirectory() as t:
            lock=Path(t)/'db.lck';lock.touch()
            self.assertEqual(b.lock_inspection(lock)[0],'indeterminado')

    @patch('wolfpkg.backend.subprocess.run')
    def test_active_pacman_lock_detected(self,run):
        run.return_value=subprocess.CompletedProcess([],0,'123 pacman\n','')
        with tempfile.TemporaryDirectory() as t:
            lock=Path(t)/'db.lck';lock.touch()
            self.assertEqual(b.lock_inspection(lock)[0],'ocupado')

    def test_autostart_has_tray_flag_and_launcher_regular(self):
        code=(Path(__file__).resolve().parents[1]/'install-local.sh').read_text()
        self.assertIn('Exec=wolf-package-manager --tray',code)
        self.assertIn('Exec=wolf-package-manager\n',code)
        gui=(Path(__file__).resolve().parents[1]/'wolfpkg/app.py').read_text()
        self.assertIn("client.write(b'PING",gui)
        self.assertIn("if message == b'SHOW'",gui)

    def test_gui_multiselect_statically_present(self):
        gui=(Path(__file__).resolve().parents[1]/'wolfpkg/app.py').read_text()
        self.assertIn('ItemIsUserCheckable',gui)
        self.assertIn('installation_batch_commands(packages',gui)
        self.assertIn('show_lock_diagnostics',gui)
        self.assertIn('diagnostic_report()',gui)

if __name__=='__main__':unittest.main()
