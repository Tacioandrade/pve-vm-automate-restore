import unittest
import os
import tempfile
from pbs_restore.vm_manager import get_vms_to_restore, rotate_vm
from pbs_restore.exceptions import VMFileError

class TestVMManager(unittest.TestCase):
    def setUp(self):
        self.tmp_file = tempfile.NamedTemporaryFile(mode='w', delete=False)
        self.tmp_file.write("100\n101\n102\n")
        self.tmp_file.close()
        self.filename = self.tmp_file.name

    def tearDown(self):
        if os.path.exists(self.filename):
            os.remove(self.filename)

    def test_get_vms_valid(self):
        vms = get_vms_to_restore(self.filename, count=2)
        self.assertEqual(vms, ["100", "101"])

    def test_get_vms_dedup(self):
        with open(self.filename, 'w') as f:
            f.write("100\n100\n101\n")
        vms = get_vms_to_restore(self.filename, count=3)
        self.assertEqual(vms, ["100", "101"])

    def test_rotate_vm(self):
        rotate_vm(self.filename, "100")
        with open(self.filename, 'r') as f:
            lines = f.read().splitlines()
        self.assertEqual(lines, ["101", "102", "100"])

    def test_invalid_file(self):
        with self.assertRaises(VMFileError):
            get_vms_to_restore("non_existent_file.txt")

if __name__ == '__main__':
    unittest.main()
