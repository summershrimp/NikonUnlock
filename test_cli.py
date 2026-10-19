"""Offline checks for release command separation and camera validation."""
import contextlib
import io
import tempfile
import unittest
from unittest.mock import patch

import unlock_cli as cli
import unlock_core as core


class CliTests(unittest.TestCase):
    def test_combined_commands_rejected(self):
        for feature in ('raw', 'languages'):
            for argv in (['activate'], ['--all'], ['unlock', '--both']):
                with self.subTest(feature=feature, argv=argv), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        cli.main(feature, argv)
                    self.assertEqual(raised.exception.code, 2)

    def test_default_and_unlock_preview_never_open_camera(self):
        with patch('wpd_transport.WpdCamera') as cam, contextlib.redirect_stdout(io.StringIO()):
            for feature in ('raw', 'languages'):
                for argv in ([], ['unlock']):
                    self.assertEqual(cli.main(feature, argv), 0)
            cam.assert_not_called()

    def test_feature_specific_verification(self):
        for feature in ('raw', 'languages'):
            # The other feature is intentionally disabled.
            meta = dict(language_flag=0 if feature == 'raw' else 1,
                        raw_authorized=1 if feature == 'raw' else 0,
                        raw_status=1 if feature == 'raw' else 0)
            with patch('wpd_transport.WpdCamera') as camera, \
                 patch.object(core, 'validate_camera', return_value={}), \
                 patch.object(core, 'service', return_value=contextlib.nullcontext()), \
                 patch.object(core, 'snapshot', return_value=(b'', meta)) as snapshot, \
                 contextlib.redirect_stdout(io.StringIO()):
                cam = camera.return_value.__enter__.return_value
                cam.send.return_value = (0x2001, [])
                self.assertEqual(cli.main(feature, ['verify']), 0)
                self.assertEqual(snapshot.call_args.kwargs['include_raw'], feature == 'raw')
                if feature == 'languages':
                    cam.send.assert_not_called()

    def test_model_and_firmware_guards(self):
        class Camera:
            def read(self, *args):
                return b'', (0x2001, [])
        for model, version, optin, accepted in (
            ('Z 6', '3.80', False, True), ('Z 7', '3.80', False, False),
            ('Z 7', '3.80', True, True), ('Z 6II', '3.80', True, False),
            ('Z 6', '3.70', True, False)):
            identity = dict(manufacturer='Nikon', model=model, version=version, serial='test')
            with self.subTest(model=model, version=version, optin=optin), \
                 patch.object(core, 'device_info', return_value=identity):
                if accepted:
                    self.assertEqual(core.validate_camera(Camera(), optin), identity)
                else:
                    with self.assertRaises(RuntimeError):
                        core.validate_camera(Camera(), optin)


if __name__ == '__main__':
    unittest.main()
