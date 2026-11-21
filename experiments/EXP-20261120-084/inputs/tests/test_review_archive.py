import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

class ReviewArchiveTests(unittest.TestCase):
    def test_duplicate_names_rejected_before_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.txt';source.write_text('review')
            out=root/'out';out.mkdir();ref={'name':'same.txt','path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
            cfg=root/'cfg.json';cfg.write_text(json.dumps({'review_files':[ref,ref]}))
            result=subprocess.run([sys.executable,str(ROOT/'scripts/archive_review.py'),'--config',str(cfg),'--output',str(out)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((out/'review_sources').exists())

    def test_unique_review_sources_archived_as_agent_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.txt';source.write_text('review')
            out=root/'out';out.mkdir();ref={'name':'one.txt','path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
            cfg=root/'cfg.json';cfg.write_text(json.dumps({'review_files':[ref]}))
            result=subprocess.run([sys.executable,str(ROOT/'scripts/archive_review.py'),'--config',str(cfg),'--output',str(out)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            report=json.loads((out/'review_artifacts.json').read_text());self.assertFalse(report['training_performed'])
            self.assertEqual((out/'review_sources/one.txt').read_text(),'review')

if __name__=='__main__':unittest.main()
