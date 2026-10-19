"""Independent command interfaces; importing this module never opens a camera."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

import unlock_core as core


def main(feature, argv=None):
    if feature not in ('raw', 'languages'):
        raise ValueError(feature)
    raw = feature == 'raw'
    parser = argparse.ArgumentParser(description=(
        'Unlock HDMI RAW only.' if raw else 'Unlock the full language list only.'))
    parser.add_argument('action', nargs='?', default='plan',
                        choices=('plan', 'unlock', 'verify', 'snapshot', 'list-devices'))
    parser.add_argument('--execute', action='store_true', help='Apply unlock; otherwise print an offline plan')
    parser.add_argument('--device', help='Exact Nikon WPD device ID')
    parser.add_argument('--backup-dir', type=Path, help='New directory; existing directories are never overwritten')
    parser.add_argument('--experimental-z7', action='store_true', help='Opt in to Z7 3.80; not hardware validated')
    if not raw:
        parser.add_argument('--restore-language-from', type=Path, help='Restore the language flag from a same-camera snapshot.json')
    args = parser.parse_args(argv)
    restore = getattr(args, 'restore_language_from', None)
    if restore and args.action != 'unlock':
        parser.error('--restore-language-from requires unlock')
    if args.action == 'plan' or (args.action == 'unlock' and not args.execute):
        print('Offline plan; no camera accessed.')
        print('Supported: Z6 3.80 (hardware validated); Z7 3.80 (experimental opt-in).')
        print('Identify camera -> service mode -> backup -> ' + (
            'C102/C052 -> verify RAW -> F030 -> exit.' if raw else
            'write language flag only -> compare configuration -> F030 -> exit.'))
        print('Add --execute to unlock. After a normal power cycle, run verify.')
        return 0
    previous = json.loads(restore.read_text(encoding='utf-8')) if restore else None
    value = previous['language_flag'] if previous is not None else 1
    if type(value) is not int or not 0 <= value <= 255:
        parser.error('Invalid language_flag in snapshot')
    from wpd_transport import WpdCamera
    if args.action == 'list-devices':
        print(json.dumps(WpdCamera.devices(), indent=2))
        return 0
    folder = args.backup_dir or Path(__file__).resolve().parent / 'backups' / (
        datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + feature)
    with WpdCamera(args.device) as cam:
        print(json.dumps(core.validate_camera(cam, args.experimental_z7), ensure_ascii=False))
        if previous is not None:
            old = previous.get('identity') or {}
            if not old.get('serial') or any(old.get(k) != cam.identity.get(k) for k in ('serial', 'model', 'version')):
                raise RuntimeError('Snapshot identity does not match this camera and firmware')
        if args.action == 'unlock':
            result = core.update(cam, 'persist-raw' if raw else 'enable-languages', folder, value)
            print(json.dumps(result, indent=2))
            print('Normal power-off/on still required; then run verify.')
        else:
            if args.action == 'verify' and raw:
                core.check(cam.send(0x944B, []))
            with core.service(cam):
                _, meta = core.snapshot(cam, folder, include_raw=raw)
            print(json.dumps(meta, indent=2))
            if args.action == 'verify':
                if raw and (meta['raw_authorized'], meta['raw_status']) != (1, 1):
                    raise RuntimeError('HDMI RAW verification failed')
                if not raw and meta['language_flag'] != 1:
                    raise RuntimeError('Full language list is not enabled')
                print('Selected feature verified. Persistence is verified only if you actually power-cycled first.')
        print(f'Backup/readback: {folder}')
    return 0
