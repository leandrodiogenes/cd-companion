import struct
import sys
import unittest

if sys.platform == 'win32':
    from server.readonly_position import ReadOnlyPositionEngine


@unittest.skipUnless(sys.platform == 'win32', 'Windows process-reader module')
class ReadOnlyPositionTests(unittest.TestCase):
    def engine(self, data):
        engine = ReadOnlyPositionEngine()
        engine.base, engine.size = 0x100000, len(data)
        engine.read = lambda address, size: bytes(data[address-engine.base:address-engine.base+size])
        return engine

    def sample(self):
        data = bytearray(1024)
        data[64:79] = b'\xc5\xfb\x11\x3d' + struct.pack('<i', 200-72) + b'\x44\x89\x3d' + struct.pack('<i', 208-79)
        struct.pack_into('<fff', data, 200, -10516.0, 576.0, -3977.0)
        return data

    def test_position_and_disabled_teleport(self):
        engine = self.engine(self.sample())
        engine.scan_and_hook()
        self.assertEqual(engine.get_player_abs(), (-10516.0, 576.0, -3977.0))
        self.assertFalse(engine.teleport_enabled)
        self.assertFalse(engine.teleport_to_abs(1, 2, 3)[0])

    def test_missing_or_ambiguous_signature_rejected(self):
        with self.assertRaises(RuntimeError):
            self.engine(bytearray(1024)).scan_and_hook()
        data = self.sample()
        data[300:315] = data[64:79]
        with self.assertRaises(RuntimeError):
            self.engine(data).scan_and_hook()

    def test_outside_module_target_rejected(self):
        data = self.sample()
        struct.pack_into('<i', data, 68, 2000)
        with self.assertRaises(RuntimeError):
            self.engine(data).scan_and_hook()

    def test_loading_and_nonfinite_coordinates_not_published(self):
        for xyz in ((0, 0, 0), (0, 1000.15, 0), (float('nan'), 10, 10)):
            with self.subTest(xyz=xyz):
                data = self.sample()
                struct.pack_into('<fff', data, 200, *xyz)
                engine = self.engine(data)
                engine.scan_and_hook()
                self.assertIsNone(engine.get_player_abs())


if __name__ == '__main__':
    unittest.main()
