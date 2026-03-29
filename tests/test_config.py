import unittest
from unittest.mock import patch
import os
from pbs_restore.config import load_config
from pbs_restore.exceptions import ConfigurationError

class TestConfig(unittest.TestCase):
    def setUp(self):
        self.env_patch = {
            "PROXMOX_URL": "https://pve:8006",
            "PROXMOX_NODE": "pve",
            "PROXMOX_USER": "root@pam",
            "PROXMOX_PASSWORD": "pass",
            "BACKUP_STORAGE": "pbs",
            "RESTORE_STORAGE": "zfs",
            "VM_RESTORE_COUNT": "2",
            "SCREENSHOT_WAIT_MINUTES": "5",
            "AUTO_START_VM": "True",
            "ANALYZE_WITH_GEMINI": "False",
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_CHAT_ID": "123",
            "PROXMOX_TIMEOUT": "60"
        }


    @patch("pbs_restore.config.load_dotenv")
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_config(self, mock_load_dotenv):
        with self.assertRaises(ConfigurationError):
            load_config()

    @patch("pbs_restore.config.load_dotenv")
    @patch.dict(os.environ, {}, clear=True)
    def test_valid_config(self, mock_load_dotenv):
        with patch.dict(os.environ, self.env_patch):
            config = load_config()
            self.assertEqual(config.proxmox_url, "https://pve:8006")
            self.assertEqual(config.vm_restore_count, 2)
            self.assertTrue(config.auto_start_vm)


if __name__ == '__main__':
    unittest.main()
