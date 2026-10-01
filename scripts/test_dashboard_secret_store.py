"""Offline synthetic credentials only; never contact authentication services."""
import importlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class SecretStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'secrets'
        self.env = patch.dict(os.environ, {'DASHBOARD_SECRET_DIR': str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def store(self):
        self.assertIsNotNone(importlib.util.find_spec('dashboard_secret_store'), 'file backend not implemented')
        return importlib.import_module('dashboard_secret_store')

    def test_explicit_worker_dispatch(self):
        import dashboard_private_session as worker
        with patch.object(worker.subprocess, 'run', side_effect=AssertionError('must not invoke Keychain')), patch.object(worker.pty, 'fork', side_effect=AssertionError('must not invoke PTY')):
            worker.keychain_write('service', 'account', 'synthetic')
            self.assertEqual(worker.keychain_read('service', 'account'), 'synthetic')
            worker.keychain_delete('service', 'account')
            with self.assertRaises(worker.WorkerError):
                worker.keychain_read('service', 'account')
            with patch.dict(os.environ, {'DASHBOARD_SECRET_DIR': ''}):
                with self.assertRaises(worker.WorkerError):
                    worker.keychain_read('service', 'account')

    def test_invalid_values_are_safe(self):
        s = self.store()
        for value in ('', 'synthetic\nsecret', 'synthetic\rsecret', 'synthetic\x00secret', 'x' * 16385, '\ud800'):
            with self.subTest(kind=repr(value[:12])):
                with self.assertRaises(s.SecretStoreError) as caught:
                    s.write('service', 'account', value)
                self.assertNotIn('synthetic', str(caught.exception))

    def test_unsafe_directory_and_parent(self):
        s = self.store()
        self.root.mkdir(mode=0o755)
        with self.assertRaises(s.SecretStoreError):
            s.write('s', 'a', 'synthetic')
        self.root.chmod(0o700)
        self.root.parent.chmod(0o777)
        try:
            with self.assertRaises(s.SecretStoreError):
                s.write('s', 'a', 'synthetic')
        finally:
            self.root.parent.chmod(0o700)

    def test_symlink_directory_and_parent(self):
        s = self.store()
        target = self.root.parent / 'target'
        target.mkdir(mode=0o700)
        self.root.symlink_to(target, target_is_directory=True)
        with self.assertRaises(s.SecretStoreError):
            s.write('s', 'a', 'synthetic')
        with patch.dict(os.environ, {'DASHBOARD_SECRET_DIR': str(self.root / 'child')}):
            with self.assertRaises(s.SecretStoreError):
                s.write('s', 'a', 'synthetic')

    def test_unsafe_file_rejected_for_all_operations(self):
        s = self.store()
        s.write('s', 'a', 'synthetic')
        file = next(self.root.iterdir())
        file.chmod(0o644)
        for operation in (lambda: s.read('s', 'a'), lambda: s.write('s', 'a', 'next'), lambda: s.delete('s', 'a')):
            with self.assertRaises(s.SecretStoreError):
                operation()
        file.chmod(0o600)
        file.unlink()
        target = self.root.parent / 'target'
        target.write_text('do-not-touch')
        file.symlink_to(target)
        for operation in (lambda: s.read('s', 'a'), lambda: s.write('s', 'a', 'next'), lambda: s.delete('s', 'a')):
            with self.assertRaises(s.SecretStoreError):
                operation()
        self.assertEqual(target.read_text(), 'do-not-touch')

    def test_wrong_owner_and_hardlink(self):
        s = self.store()
        s.write('s', 'a', 'synthetic')
        with patch.object(s.os, 'getuid', return_value=os.getuid() + 1):
            with self.assertRaises(s.SecretStoreError):
                s.read('s', 'a')
        os.link(next(self.root.iterdir()), self.root.parent / 'alias')
        with self.assertRaises(s.SecretStoreError):
            s.read('s', 'a')

    def test_atomic_failure_preserves_old_secret_and_cleans_temp(self):
        s = self.store()
        s.write('s', 'a', 'old')
        with patch.object(s.os, 'replace', side_effect=OSError('synthetic-sensitive-path')):
            with self.assertRaises(s.SecretStoreError) as caught:
                s.write('s', 'a', 'new')
        self.assertNotIn('synthetic', str(caught.exception))
        self.assertEqual(s.read('s', 'a'), 'old')
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_fsync_before_and_after_replace(self):
        s = self.store()
        s.write('s', 'a', 'old')
        events = []
        real_fsync, real_replace = s.os.fsync, s.os.replace
        def sync(fd):
            events.append('sync')
            return real_fsync(fd)
        def replace(*args, **kwargs):
            events.append('replace')
            return real_replace(*args, **kwargs)
        with patch.object(s.os, 'fsync', side_effect=sync), patch.object(s.os, 'replace', side_effect=replace):
            s.write('s', 'a', 'new')
        self.assertEqual(events, ['sync', 'replace', 'sync'])

    def test_binding_names_do_not_collide(self):
        s = self.store()
        s.write('a/b', 'c', 'one')
        s.write('a', 'b/c', 'two')
        self.assertEqual(s.read('a/b', 'c'), 'one')
        self.assertEqual(s.read('a', 'b/c'), 'two')
        self.assertEqual(len(list(self.root.iterdir())), 2)

    def test_default_mac_path_unchanged(self):
        import dashboard_private_session as worker
        from types import SimpleNamespace
        with patch.dict(os.environ):
            os.environ.pop('DASHBOARD_SECRET_DIR', None)
            with patch.object(worker.subprocess, 'run', return_value=SimpleNamespace(stdout='synthetic\n', returncode=0)) as run:
                self.assertEqual(worker.keychain_read('s', 'a'), 'synthetic')
                self.assertEqual(run.call_args.args[0], ['security', 'find-generic-password', '-a', 'a', '-s', 's', '-w'])
                worker.keychain_delete('s', 'a')
                self.assertEqual(run.call_args.args[0], ['security', 'delete-generic-password', '-a', 'a', '-s', 's'])
            with patch.object(worker.pty, 'fork', side_effect=OSError('synthetic')) as fork:
                with self.assertRaises(worker.WorkerError):
                    worker.keychain_write('s', 'a', 'synthetic')
                fork.assert_called_once()

    def test_refresh_rotation_persists_before_access_return(self):
        import dashboard_private_session as worker
        from types import SimpleNamespace
        worker.keychain_write('s', 'a', 'old')
        config = {'keychainService': 's', 'keychainAccount': 'a', 'supabaseUrl': 'https://invalid.example', 'supabaseAnonKey': 'synthetic'}
        response = SimpleNamespace(status_code=200, ok=True, json=lambda: {'access_token': 'access', 'refresh_token': 'new'})
        with patch.object(worker.requests, 'post', return_value=response) as post:
            self.assertEqual(worker.refresh_session(config), 'access')
            self.assertEqual(worker.keychain_read('s', 'a'), 'new')
            self.assertEqual(post.call_args.kwargs['json'], {'refresh_token': 'old'})
            with patch.object(worker, 'keychain_write') as write:
                self.assertEqual(worker.refresh_session(config), 'access')
                write.assert_not_called()
            response.json = lambda: {'access_token': 'access', 'refresh_token': 'newer'}
            with patch.object(worker, 'keychain_write', side_effect=worker.WorkerError('worker_secret_io_failed')):
                with self.assertRaises(worker.WorkerError):
                    worker.refresh_session(config)

    def test_invalid_paths_fail_closed(self):
        s = self.store()
        for path in ('', '.', '/', 'relative/secrets', str(self.root / '..' / 'other')):
            with patch.dict(os.environ, {'DASHBOARD_SECRET_DIR': path}):
                with self.assertRaises(s.SecretStoreError):
                    s.write('s', 'a', 'synthetic')

    def test_fsync_failure_does_not_publish_new_token(self):
        s = self.store()
        s.write('s', 'a', 'old')
        with patch.object(s.os, 'fsync', side_effect=OSError('synthetic')):
            with self.assertRaises(s.SecretStoreError):
                s.write('s', 'a', 'new')
        self.assertEqual(s.read('s', 'a'), 'old')
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_nonregular_and_oversize_file_rejected(self):
        s = self.store()
        s.write('s', 'a', 'old')
        file = next(self.root.iterdir())
        file.write_bytes(b'x' * 16385)
        with self.assertRaises(s.SecretStoreError):
            s.read('s', 'a')
        file.unlink()
        os.mkfifo(file, 0o600)
        with self.assertRaises(s.SecretStoreError):
            s.read('s', 'a')

    def test_refresh_failures_never_replace_token(self):
        import dashboard_private_session as worker
        from types import SimpleNamespace
        worker.keychain_write('s', 'a', 'old')
        config = {'keychainService': 's', 'keychainAccount': 'a', 'supabaseUrl': 'https://invalid.example', 'supabaseAnonKey': 'synthetic'}
        responses = [SimpleNamespace(status_code=code, ok=False) for code in (400, 401, 403, 500)]
        responses += [SimpleNamespace(status_code=200, ok=True, json=lambda: {'access_token': 'access'})]
        for response in responses:
            with patch.object(worker.requests, 'post', return_value=response), patch.object(worker, 'keychain_write') as write:
                with self.assertRaises(worker.WorkerError):
                    worker.refresh_session(config)
                write.assert_not_called()
        with patch.object(worker.requests, 'post', side_effect=worker.requests.Timeout()):
            with self.assertRaises(worker.WorkerError):
                worker.refresh_session(config)
        self.assertEqual(worker.keychain_read('s', 'a'), 'old')

    def test_roundtrip_rotation_delete(self):
        s = self.store()
        s.write('service', 'account', 'synthetic-one')
        self.assertEqual(s.read('service', 'account'), 'synthetic-one')
        s.write('service', 'account', 'synthetic-two')
        self.assertEqual(s.read('service', 'account'), 'synthetic-two')
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        files = list(self.root.iterdir())
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0].name, r'^[0-9a-f]{64}\.secret$')
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        s.delete('service', 'account')
        s.delete('service', 'account')
        with self.assertRaises(s.SecretStoreError):
            s.read('service', 'account')


if __name__ == '__main__':
    unittest.main()
