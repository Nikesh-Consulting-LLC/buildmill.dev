"""Synchronous SFTP + exec helpers over an open paramiko transport.

Used by the file manager (US-1.30) and text editor (US-1.46). Each function
is blocking and expects to be called from a worker thread; the transport is
the one already authenticated by app.ssh. Nothing here reads credentials.
"""

from __future__ import annotations

import posixpath
import shlex
import stat as stat_mod

import paramiko

TEXT_EDIT_LIMIT = 1_000_000  # 1 MB — mirrors the limit stated in the editor UI


class SftpError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SftpConflict(SftpError):
    """The file changed on the server since it was opened."""


class NotEditable(SftpError):
    """Binary or oversized file — can't open in the text editor."""


def _sftp(transport: paramiko.Transport) -> paramiko.SFTPClient:
    client = paramiko.SFTPClient.from_transport(transport)
    if client is None:
        raise SftpError("Could not open an SFTP channel.")
    return client


def _entry_type(mode: int) -> str:
    if stat_mod.S_ISDIR(mode):
        return "dir"
    if stat_mod.S_ISLNK(mode):
        return "link"
    if stat_mod.S_ISREG(mode):
        return "file"
    return "other"


def home_dir(transport: paramiko.Transport) -> str:
    return _sftp(transport).normalize(".")


def normalize(transport: paramiko.Transport, path: str) -> str:
    return _sftp(transport).normalize(path)


def list_dir(transport: paramiko.Transport, path: str) -> dict:
    sftp = _sftp(transport)
    resolved = sftp.normalize(path)
    entries = []
    for attr in sftp.listdir_attr(resolved):
        mode = attr.st_mode or 0
        etype = _entry_type(mode)
        # For a symlink, resolve one hop so the UI can descend into linked dirs.
        if etype == "link":
            try:
                target = sftp.stat(posixpath.join(resolved, attr.filename))
                etype = _entry_type(target.st_mode or 0)
            except IOError:
                pass
        entries.append(
            {
                "name": attr.filename,
                "type": etype,
                "size": attr.st_size or 0,
                "mtime": int(attr.st_mtime) if attr.st_mtime else 0,
            }
        )
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return {"path": resolved, "entries": entries}


def make_dir(transport: paramiko.Transport, path: str) -> None:
    sftp = _sftp(transport)
    try:
        sftp.mkdir(path)
    except IOError as e:
        raise SftpError(f"Could not create folder: {e.strerror or e}")


def _remove_recursive(sftp: paramiko.SFTPClient, path: str) -> None:
    for attr in sftp.listdir_attr(path):
        child = posixpath.join(path, attr.filename)
        if stat_mod.S_ISDIR(attr.st_mode or 0):
            _remove_recursive(sftp, child)
        else:
            sftp.remove(child)
    sftp.rmdir(path)


def remove(transport: paramiko.Transport, path: str, recursive: bool) -> None:
    sftp = _sftp(transport)
    try:
        attr = sftp.lstat(path)
    except IOError:
        raise SftpError("That path no longer exists.")

    if stat_mod.S_ISDIR(attr.st_mode or 0):
        contents = sftp.listdir(path)
        if contents and not recursive:
            raise SftpError("This folder isn't empty — confirm recursive delete.")
        if recursive:
            _remove_recursive(sftp, path)
        else:
            sftp.rmdir(path)
    else:
        try:
            sftp.remove(path)
        except IOError as e:
            raise SftpError(f"Could not delete: {e.strerror or e}")


def read_text(transport: paramiko.Transport, path: str) -> dict:
    """Read a small UTF-8 text file for editing (US-1.46)."""
    sftp = _sftp(transport)
    try:
        st = sftp.stat(path)
    except IOError:
        raise SftpError("That file no longer exists.")
    if stat_mod.S_ISDIR(st.st_mode or 0):
        raise SftpError("That's a folder, not a file.")
    size = st.st_size or 0
    if size > TEXT_EDIT_LIMIT:
        raise NotEditable(
            f"This file is {size // 1024} KB — larger than the 1 MB edit limit. "
            "Download it instead."
        )
    with sftp.open(path, "rb") as f:
        raw = f.read(TEXT_EDIT_LIMIT + 1)
    if b"\x00" in raw:
        raise NotEditable("This looks like a binary file — download it instead.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise NotEditable("This file isn't valid UTF-8 text — download it instead.")
    eol = "crlf" if "\r\n" in text else "lf"
    # The editor works in \n; we re-apply the original EOL on save.
    return {
        "content": text.replace("\r\n", "\n"),
        "eol": eol,
        "mtime": int(st.st_mtime) if st.st_mtime else 0,
        "size": size,
    }


def write_text(
    transport: paramiko.Transport,
    path: str,
    content: str,
    eol: str,
    expected_mtime: int | None,
    expected_size: int | None,
    force: bool,
) -> dict:
    """Write edited text back, preserving EOL + permissions (US-1.46)."""
    sftp = _sftp(transport)
    existing_mode: int | None = None
    try:
        st = sftp.stat(path)
        existing_mode = stat_mod.S_IMODE(st.st_mode or 0)
        if not force and expected_mtime is not None:
            changed = int(st.st_mtime or 0) != int(expected_mtime) or (
                expected_size is not None and (st.st_size or 0) != int(expected_size)
            )
            if changed:
                raise SftpConflict(
                    "This file changed on the server since you opened it. "
                    "Saving will overwrite those changes."
                )
    except IOError:
        pass  # new file — no conflict possible

    normalized = content.replace("\r\n", "\n")
    data = normalized.replace("\n", "\r\n") if eol == "crlf" else normalized
    payload = data.encode("utf-8")
    try:
        with sftp.open(path, "wb") as f:
            f.write(payload)
        if existing_mode is not None:
            sftp.chmod(path, existing_mode)
    except IOError as e:
        raise SftpError(f"Could not save: {e.strerror or e}")

    new_st = sftp.stat(path)
    return {"mtime": int(new_st.st_mtime or 0), "size": new_st.st_size or 0}


def create_file(transport: paramiko.Transport, path: str) -> None:
    """Create an empty file, refusing to clobber an existing one (US-1.46)."""
    sftp = _sftp(transport)
    try:
        sftp.stat(path)
        raise SftpError("A file with that name already exists here.")
    except IOError:
        pass
    try:
        with sftp.open(path, "wb") as f:
            f.write(b"")
    except IOError as e:
        raise SftpError(f"Could not create file: {e.strerror or e}")


def extract_zip(transport: paramiko.Transport, zip_path: str, dest_dir: str) -> None:
    """Unpack a .zip on the server via an exec channel (US-1.30)."""
    quoted_zip = shlex.quote(zip_path)
    quoted_dir = shlex.quote(dest_dir)
    command = (
        "command -v unzip >/dev/null 2>&1 || { echo __NO_UNZIP__ >&2; exit 3; }; "
        f"unzip -o {quoted_zip} -d {quoted_dir}"
    )
    chan = transport.open_session()
    chan.exec_command(command)
    stderr = chan.makefile_stderr("rb").read().decode("utf-8", "replace")
    chan.makefile("rb").read()
    status = chan.recv_exit_status()
    chan.close()
    if status == 0:
        return
    if status == 3 or "__NO_UNZIP__" in stderr:
        raise SftpError("`unzip` is not installed on this server.")
    raise SftpError(stderr.strip() or f"Extraction failed (exit {status}).")
