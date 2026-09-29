"""Explicit opt-in POSIX file store for rotating Dashboard credentials.

DASHBOARD_SECRET_DIR must be absolute, with trusted, non-writable ancestors.
Files are permission-protected plaintext, NOT encryption at rest. Provision an
encrypted filesystem if required. No credentials are taken from argv or logs.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


class SecretStoreError(RuntimeError):
    """Only stable, non-sensitive categories may leave this module."""


def _name(service, account):
    if not all(isinstance(v, str) and v and '\x00' not in v for v in (service, account)):
        raise SecretStoreError('worker_secret_binding_invalid')
    return hashlib.sha256(json.dumps([service, account], ensure_ascii=True, separators=(',', ':')).encode()).hexdigest() + '.secret'


def _validate_value(value):
    if not isinstance(value, str) or not value or any(c in value for c in '\r\n\x00'):
        raise SecretStoreError('worker_refresh_token_invalid')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError:
        raise SecretStoreError('worker_refresh_token_invalid') from None
    if size > 16384:
        raise SecretStoreError('worker_refresh_token_invalid')
    return value


@contextmanager
def _directory(create=False):
    fd = None
    try:
        raw = os.environ.get('DASHBOARD_SECRET_DIR', '')
        path = Path(raw)
        if not raw or not path.is_absolute() or '..' in path.parts or len(path.parts) < 2:
            raise SecretStoreError('worker_secret_directory_invalid')
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open('/', flags)
        root = os.fstat(fd)
        if root.st_uid != 0 or root.st_mode & 0o022:
            raise SecretStoreError('worker_secret_directory_invalid')
        for index, part in enumerate(path.parts[1:], 1):
            leaf = index == len(path.parts) - 1
            if leaf and create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
                raise SecretStoreError('worker_secret_directory_invalid')
            if leaf and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
                raise SecretStoreError('worker_secret_directory_invalid')
        yield fd
    except (OSError, ValueError, UnicodeError):
        raise SecretStoreError('worker_secret_io_failed') from None
    finally:
        if fd is not None:
            os.close(fd)


def _check_file(info):
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 16384:
        raise SecretStoreError('worker_secret_file_invalid')


def _existing(fd, name):
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    _check_file(info)
    return True


def read(service, account):
    name = _name(service, account)
    with _directory() as fd:
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, 'rb') as stream:
            _check_file(os.fstat(stream.fileno()))
            value = stream.read(16385).decode('utf-8')
        return _validate_value(value)


def write(service, account, value):
    name = _name(service, account)
    data = _validate_value(value).encode('utf-8')
    with _directory(create=True) as fd:
        _existing(fd, name)
        temporary = '.' + secrets.token_hex(16) + '.tmp'
        created = False
        try:
            target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            created = True
            with os.fdopen(target, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            _existing(fd, name)
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            created = False
            os.fsync(fd)
        finally:
            if created:
                os.unlink(temporary, dir_fd=fd)


def delete(service, account):
    name = _name(service, account)
    with _directory() as fd:
        if _existing(fd, name):
            os.unlink(name, dir_fd=fd)
            os.fsync(fd)
