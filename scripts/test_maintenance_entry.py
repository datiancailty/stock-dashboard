import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

class MaintenanceEntryTests(unittest.TestCase):
    def test_current_entry_classifies_active_legacy_and_pending(self):
        path = ROOT/'docs/MAINTENANCE-ENTRY.md'
        self.assertTrue(path.is_file(), 'one current maintenance index is missing')
        text = path.read_text()
        for heading in ['活动源码', '构建输入', '活动旧依赖', '冻结归档', '暂停', '待观察']:
            self.assertIn(heading, text)
        self.assertIn('MAINTENANCE-ENTRY.md', (ROOT/'README.md').read_text())
        self.assertNotIn('本机确定性日更按', (ROOT/'README.md').read_text())
        self.assertNotIn('10:05、11:55、15:20', (ROOT/'README.md').read_text())

if __name__ == '__main__':
    unittest.main()
