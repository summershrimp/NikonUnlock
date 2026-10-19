"""Offline failure-injection and firmware-evidence tests; no device access."""
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import unlock_hdmi_raw_ii as r

ROOT = Path(__file__).resolve().parents[1]


def identity(model="Z 6II", version="1.70", manufacturer="Nikon Corporation"):
    def s(text):
        return bytes([len(text)+1])+(text+'\0').encode('utf-16le')
    return bytes(8)+s('')+bytes(2)+bytes(20)+b''.join(s(x) for x in (manufacturer,model,version,'123456'))


class Camera:
    def __init__(self, model="Z 6II", version="1.70"):
        self.identity_data = identity(model, version)
        self.data = bytearray(r.ITEM8_SIZE)
        self.data[:4] = bytes.fromhex('1fba1f10')
        self.data[16] = 7  # unrelated language flag must stay unchanged
        self.events = []
        self.fail = None
        self.outer_fail = False
        self.short_read = False
        self.invalid_mask = False
        self.corrupt = False
        self.corrupt_commit = False
        self.bad_state = False
        self.commit_timeout = False
        self.exit_fail = False
    def send(self, op, args):
        self.events.append((op, args[:]))
        if op == 0x944B:
            return (0x2001 if self.data[5] == 0x6A else 0x2005), []
        cmd=args[0]
        assert len(args)==5
        assert op == (0x94FE if cmd==0xF002 else 0x94FD)
        if cmd==self.fail or (cmd==0xF002 and self.exit_fail):
            return (0x2005,[]) if self.outer_fail else (0x2001,[0xA001])
        if cmd==0xC102:
            assert args==[0xC102,0x28CD7EE8,0,0,0]
            self.data[5]=0x6A
        elif cmd==0xC052:
            assert args==[0xC052,1,0,0,0]
            self.data[4]=0xB2
            if self.corrupt:self.data[16]^=1
        elif cmd in (0xC101,0xC051):
            if self.bad_state:return 0x2001,[0x2001]
            return 0x2001,[0x2001,int(self.data[5]==0x6A) if cmd==0xC101 else int(self.data[4]==0xB2)]
        elif cmd==0xF030:
            if self.commit_timeout:raise TimeoutError('lost commit response')
            if self.corrupt_commit:self.data[20]^=1
        return 0x2001,[0x2001]
    def read(self,op,args):
        self.events.append((op,args[:]))
        if op==0x1001:return self.identity_data,(0x2001,[])
        assert op==0x94FF and args==[0xF02D,0,0x1FFFC,0,0]
        data=bytearray(self.data)
        data[4]=data[5]=0
        if self.invalid_mask:data[5]=1
        return bytes(data[:-1] if self.short_read else data),(0x2001,[0x2001])


class UnlockTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.keys=self.base/'keys'
        r.prepare_keys(self.keys)
        self.folder=self.base/'backup'
    def run_cam(self,cam,action='unlock',**kwargs):
        return r.run(cam,action,self.folder,self.keys,**kwargs)
    def cmds(self,cam):return [p[0] for op,p in cam.events if p]
    def result(self):return json.loads((self.folder/'result.json').read_text(encoding='utf-8'))
    def test_success_both_models(self):
        for model in ('Z 6II','NIKON Z 7II','NIKON Z 6_2','NIKON Z 7_2'):
            with self.subTest(model=model):
                cam=Camera(model)
                folder=self.base/model.replace(' ','')
                result=r.run(cam,'unlock',folder,self.keys)
                self.assertTrue(result['success'])
                self.assertTrue(result['commit_acknowledged'])
                self.assertFalse(result['cold_boot_verified'])
                self.assertEqual(cam.data[4:6],bytes([0xB2,0x6A]))
                self.assertEqual(cam.data[16],7)
                cmds=self.cmds(cam)
                self.assertLess(cmds.index(0xC101),cmds.index(0xC102))
                self.assertLess(cmds.index(0xC051),cmds.index(0xC102))
                self.assertEqual(cmds.count(0xF030),1)
                self.assertFalse(set(cmds)&{0xF004,0xF02E,0xF02F,0xF006})
                self.assertEqual(cam.events[-2],(0x94FE,[0xF002,0,0,0,0]))
                meta=json.loads((folder/'snapshot.json').read_text(encoding='utf-8'))
                self.assertEqual(meta['masked_offsets'],[4,5])
                self.assertEqual(len((folder/'item8_masked.bin').read_bytes()),0x1FFFC)
    def test_wrong_model_or_version_stops_before_service(self):
        for model,version in [('Z 6','1.70'),('Z 7','1.70'),('Z6III','1.70'),('Z 6II','1.62'),('Z 7II','1.71')]:
            with self.subTest(model=model,version=version):
                cam=Camera(model,version)
                with self.assertRaises(RuntimeError):r.run(cam,'unlock',self.base/(model+version),self.keys)
                self.assertEqual(cam.events,[(0x1001,[])])
    def test_allowed_version_spellings(self):
        for version in ('1.70','V1.70','01.70','1.70.00','01.70.02'):
            cam=Camera('Z 7II',version)
            self.assertEqual(r.validate_camera(cam)['version'],version)
    def test_missing_local_key_prevents_device_access(self):
        (self.keys/'SRF_FUNC.KEY').unlink()
        cam=Camera()
        with self.assertRaises(RuntimeError):self.run_cam(cam)
        self.assertEqual(cam.events,[])
    def test_bad_existing_key_not_overwritten(self):
        p=self.keys/'HDMIARAW.KEY';p.write_bytes(b'old-key')
        with self.assertRaises(RuntimeError):r.prepare_keys(self.keys)
        self.assertEqual(p.read_bytes(),b'old-key')
    def test_bad_key_on_card_stops_before_either_write(self):
        for command in (0xC101,0xC051):
            cam=Camera();cam.fail=command
            with self.assertRaises(RuntimeError):r.run(cam,'unlock',self.base/str(command),self.keys)
            self.assertFalse(set(self.cmds(cam))&{0xC102,0xC052,0xF030})
            self.assertEqual(self.cmds(cam)[-1],0xF002)
    def test_short_or_wrong_mask_backup_stops_before_write(self):
        for fault in ('short_read','invalid_mask'):
            cam=Camera();setattr(cam,fault,True)
            with self.assertRaises(RuntimeError):r.run(cam,'unlock',self.base/fault,self.keys)
            self.assertNotIn(0xC102,self.cmds(cam))
    def test_missing_state_stops_before_write(self):
        cam=Camera();cam.bad_state=True
        with self.assertRaises(RuntimeError):self.run_cam(cam)
        self.assertNotIn(0xC102,self.cmds(cam))
    def test_second_write_failure_is_logged_without_commit_or_retry(self):
        cam=Camera();cam.fail=0xC052
        with self.assertRaises(RuntimeError):self.run_cam(cam)
        self.assertNotIn(0xF030,self.cmds(cam))
        self.assertEqual(self.cmds(cam).count(0xC052),1)
        self.assertTrue(self.result()['activation_attempted'])
        self.assertFalse(self.result()['commit_attempted'])
        self.assertEqual(self.cmds(cam)[-1],0xF002)
    def test_unexpected_config_change_prevents_commit(self):
        cam=Camera();cam.corrupt=True
        with self.assertRaises(RuntimeError):self.run_cam(cam)
        self.assertNotIn(0xF030,self.cmds(cam))
        self.assertTrue((self.folder/'unexpected_before_commit.bin').exists())
    def test_commit_vendor_failure_never_claims_success(self):
        cam=Camera();cam.fail=0xF030
        with self.assertRaises(RuntimeError):self.run_cam(cam)
        self.assertTrue(self.result()['commit_attempted'])
        self.assertFalse(self.result()['commit_acknowledged'])
        self.assertFalse(self.result()['success'])
    def test_lost_commit_response_is_ambiguous_not_retried(self):
        cam=Camera();cam.commit_timeout=True
        with self.assertRaises(TimeoutError):self.run_cam(cam)
        self.assertEqual(self.cmds(cam).count(0xF030),1)
        self.assertTrue(self.result()['commit_attempted'])
        self.assertFalse(self.result()['commit_acknowledged'])
    def test_post_commit_mismatch_preserves_acknowledged_status(self):
        cam=Camera();cam.corrupt_commit=True
        with self.assertRaises(RuntimeError):self.run_cam(cam)
        self.assertTrue(self.result()['commit_acknowledged'])
        self.assertFalse(self.result()['success'])
    def test_exit_failure_not_reported_as_success(self):
        cam=Camera();cam.exit_fail=True
        with self.assertRaises(RuntimeError):self.run_cam(cam)
        self.assertFalse(self.result()['success'])
        self.assertTrue(self.result()['commit_acknowledged'])
    def test_original_failure_not_masked_by_exit_failure(self):
        cam=Camera();cam.fail=0xC052;cam.exit_fail=True
        with self.assertRaises(RuntimeError) as err:self.run_cam(cam)
        self.assertTrue(err.exception.__notes__)
        self.assertTrue(self.result()['error_notes'])
    def test_status_needs_no_keys_or_service_mode(self):
        cam=Camera()
        result=r.run(cam,'status',self.folder,self.base/'absent')
        self.assertFalse(result['raw_authorized_now'])
        self.assertEqual(cam.events,[(0x1001,[]),(0x944B,[])])
    def test_verify_is_no_activation_and_no_persistence_claim(self):
        cam=Camera();cam.data[4:6]=bytes([0xB2,0x6A])
        result=self.run_cam(cam,'verify',after_reboot=True)
        self.assertTrue(result['readback_verified'])
        self.assertTrue(result['user_reported_reboot'])
        self.assertFalse(result['cold_boot_verified'])
        self.assertFalse(set(self.cmds(cam))&{0xC102,0xC052,0xF030})
    def test_existing_backup_not_overwritten(self):
        self.folder.mkdir();cam=Camera()
        with self.assertRaises(FileExistsError):self.run_cam(cam)
        self.assertEqual(cam.events,[])
    def test_offline_default_and_activate_without_execute(self):
        with patch.dict('sys.modules',{'wpd_transport':None}),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(r.main([]),0)
            self.assertEqual(r.main(['unlock']),0)
    def test_response_layers_and_truncated_info(self):
        for value in ((0x2001,[]),(0x2001,[0xA001]),(0x2005,[0x2001])):
            with self.assertRaises(RuntimeError):r.check(value,True)
        with self.assertRaises(ValueError):r.device_info(identity()[:-2])


class ReleaseTests(unittest.TestCase):
    def test_packaged_keys_match_profile(self):
        r.validate_keys(r.KEY_DIR)

    def test_first_generation_keys_rejected_before_device_access(self):
        cam = Camera()
        with self.assertRaises(RuntimeError):
            r.run(cam, 'unlock', Path('unused'), r.KEY_DIR.parent)
        self.assertEqual(cam.events, [])

    def test_no_combined_or_language_command(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for args in (['--all'], ['unlock', '--both'], ['enable-languages']):
                with self.assertRaises(SystemExit) as error:
                    r.main(args)
                self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
