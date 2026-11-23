"""Run the real build entry until its compiler invocation; no hardware or ESP-IDF needed."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

BUILD = Path(__file__).resolve().parents[1] / 'build.sh'

class BuildOriginTest(unittest.TestCase):
    def invoke(self, args, *, with_git=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / 'software/firmware'
            (firmware/'tools').mkdir(parents=True)
            shutil.copy2(BUILD, firmware/'tools/build.sh')
            (firmware/'version.txt').write_text('0.3.0-alpha.1')
            if with_git:
                subprocess.run(['git','init','-q',str(root)],check=True)
                subprocess.run(['git','-C',str(root),'add','.'],check=True)
                subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.test','commit','-qm','fixture'],check=True)
            bin_dir=root/'bin';bin_dir.mkdir()
            idf_tools=root/'fake-idf/tools';idf_tools.mkdir(parents=True)
            # The script enters the staged directory before invoking idf.py.
            (bin_dir/'rsync').write_text('#!/bin/sh\nfor arg do last=$arg; done\nmkdir -p "$last"\n')
            (idf_tools/'idf.py').write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 37\n')
            real_git=shutil.which('git')
            (bin_dir/'git').write_text(
                '#!/bin/sh\n'
                'if [ "$3" = apply ] && [ "$4" = --reverse ] && [ "$5" = --check ]; then exit 0; fi\n'
                f'exec "{real_git}" "$@"\n')
            for p in [*bin_dir.iterdir(), idf_tools/'idf.py']: p.chmod(0o755)
            result=subprocess.run(['sh',str(firmware/'tools/build.sh'),*args],
                                  env={**os.environ,'IDF_PATH':str(idf_tools.parent),
                                       'PATH':str(idf_tools)+os.pathsep+str(bin_dir)+os.pathsep+os.environ['PATH']},
                                  capture_output=True,text=True)
            self.assertEqual(result.returncode,37,result.stderr)
            return result.stdout

    def test_default_build_is_custom(self):
        self.assertIn('WMP_BUILD_ID=custom-local-', self.invoke([]))

    def test_named_custom_build(self):
        self.assertIn('WMP_BUILD_ID=custom-mykeys-', self.invoke(['--custom-name','mykeys']))

    def test_official_build_requires_explicit_selection(self):
        output=self.invoke(['--official-build'])
        self.assertIn('WMP_BUILD_ID=', output)
        self.assertNotIn('WMP_BUILD_ID=custom-',output)

    def test_source_zip_without_git_still_builds_custom(self):
        output = self.invoke([], with_git=False)
        self.assertIn('WMP_BUILD_ID=custom-local-', output)
        self.assertIn('-nogit', output)
