import json
import tempfile
import unittest
from pathlib import Path
from harness.request_scan import git, scan, pending, RequestScanError

class RequestScanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        git(self.root,'init'); git(self.root,'config','user.name','test');git(self.root,'config','user.email','test@example.invalid')
        self.write('README.md','start');self.before=self.save()
    def write(self,path,value):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value)
    def save(self):
        git(self.root,'add','-A');git(self.root,'commit','-m','change');return git(self.root,'rev-parse','HEAD').decode().strip()
    def read(self,after,before=None):return scan(self.root,after=after,before=before,kinds=['worker_wake'])
    def test_earlier_commit_and_multiple_requests(self):
        for name in ['a','b']:self.write('requests/worker-wake/'+name+'.json',json.dumps({'wake_id':name}))
        self.save();self.write('README.md','unrelated tip');after=self.save()
        records=self.read(after,self.before)
        self.assertEqual(len(records),2);self.assertTrue(all(r['valid'] for r in records))
    def test_missing_event_tree_scan_and_new_branch(self):
        self.write('requests/worker-wake/a.json','{}');after=self.save()
        self.assertEqual(self.read(after),self.read(after,'0'*40))
    def test_mutation_does_not_reuse_receipt(self):
        self.write('requests/worker-wake/a.json','{}');first=self.read(self.save())[0]
        receipts={first['request_key']:{**first,'phase':'DONE'}}
        self.assertEqual(pending([first],receipts),[])
        self.write('requests/worker-wake/a.json','{"x":1}');second=self.read(self.save())[0]
        self.assertNotEqual(first['request_key'],second['request_key']);self.assertEqual(pending([second],receipts),[second])
    def test_unknown_claim_not_blindly_replayed(self):
        self.write('requests/worker-wake/a.json','{}');r=self.read(self.save())[0]
        for phase in ['CLAIMED','UNKNOWN','NEEDS_RECONCILE']:
            self.assertEqual(pending([r],{r['request_key']:{**r,'phase':phase}}),[])
    def test_bad_record_does_not_hide_good(self):
        self.write('requests/worker-wake/a.json','{broken');self.write('requests/worker-wake/b.json','{}')
        rs=self.read(self.save());self.assertEqual([r['valid'] for r in rs],[False,True])
    def test_symlink_never_dereferenced(self):
        # Exercise a real mode-120000 Git object without Windows symlink privileges.
        oid=git(self.root,'hash-object','-w','--stdin',input=b'../../README.md').decode().strip()
        git(self.root,'update-index','--add','--cacheinfo',f'120000,{oid},requests/worker-wake/link.json')
        git(self.root,'commit','-m','symlink fixture')
        head=git(self.root,'rev-parse','HEAD').decode().strip()
        r=self.read(head)[0];self.assertEqual(r['error'],'REQUEST_NOT_REGULAR_FILE')
    def test_deletion_and_nested_paths_not_dispatched(self):
        self.write('requests/worker-wake/a.json','{}');before=self.save()
        (self.root/'requests/worker-wake/a.json').unlink();self.write('requests/worker-wake/nested/b.json','{}')
        self.assertEqual(self.read(self.save(),before),[])
    def test_unrelated_tip_does_not_change_blob_identity(self):
        self.write('requests/worker-wake/a.json','{}');a=self.read(self.save())[0]
        self.write('README.md','unrelated');b=self.read(self.save())[0]
        self.assertEqual(a['request_key'],b['request_key']);self.assertNotEqual(a['source_commit'],b['source_commit'])
    def test_require_exact_commits(self):
        with self.assertRaises(RequestScanError):self.read('main')
    def test_large_or_nonobject_payload_rejected(self):
        self.write('requests/worker-wake/a.json','[]');self.assertFalse(self.read(self.save())[0]['valid'])
        self.write('requests/worker-wake/a.json','{"blob":"'+('x'*1024)+'"}');after=self.save()
        self.assertEqual(scan(self.root,after=after,kinds=['worker_wake'],max_bytes=20)[0]['error'],'REQUEST_TOO_LARGE')
if __name__=='__main__':unittest.main()
