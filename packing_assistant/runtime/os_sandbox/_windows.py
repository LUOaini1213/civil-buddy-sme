"""Windows: the worker lowers its own integrity level and puts itself in a job that forbids children.

No administrator rights, no AppContainer, nothing installed.

    Low integrity   Mandatory Integrity Control is enforced by the kernel: a Low process cannot
                    create, modify or delete anything labelled Medium — which is everything the
                    user owns by default — and can still read it. The parent labels the write
                    roots Low (inheritable) before it starts the worker; a process may lower its
                    own level and can never raise it again.
    job object      ActiveProcessLimit = 1, so CreateProcess fails for the life of the worker;
                    KILL_ON_JOB_CLOSE takes it down with its parent's handle.

What this does NOT do: restrict the network. Without AppContainer (which would need ACL changes on
the Python installation) or a firewall rule (administrator), Windows offers an unprivileged
process no way to do that. `enforces["network"]` is reported False, and the worker refuses inet
sockets at the application layer only.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import stat
from typing import Any, Dict, Sequence

_LABEL_SECURITY_INFORMATION, _SE_FILE_OBJECT = 0x10, 1
_TOKEN_ADJUST_DEFAULT, _TOKEN_QUERY = 0x0080, 0x0008
_TOKEN_INTEGRITY_LEVEL, _SE_GROUP_INTEGRITY = 25, 0x20
_LOW_SID, _LOW_LABEL_SDDL = "S-1-16-4096", "S:(ML;OICI;NW;;;LW)"
_JOB_EXTENDED_LIMIT, _LIMIT_ACTIVE_PROCESS, _LIMIT_KILL_ON_CLOSE = 9, 0x0008, 0x2000
_OWNER_SECURITY_INFORMATION, _DACL_SECURITY_INFORMATION = 1, 4
_WRITE_OWNER, _GENERIC_ALL = 0x80000, 0x10000000


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _Trustee(ctypes.Structure):
    _fields_ = [("multiple", ctypes.c_void_p), ("operation", ctypes.c_int),
                ("form", ctypes.c_int), ("type", ctypes.c_int), ("sid", ctypes.c_void_p)]


class _ExplicitAccess(ctypes.Structure):
    _fields_ = [("permissions", wintypes.DWORD), ("mode", ctypes.c_int),
                ("inheritance", wintypes.DWORD), ("trustee", _Trustee)]


class _AclSize(ctypes.Structure):
    _fields_ = [("count", wintypes.DWORD), ("used", wintypes.DWORD), ("free", wintypes.DWORD)]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _dlls():
    return ctypes.WinDLL("advapi32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)


def _fail(what: str) -> OSError:
    return OSError(f"{what} failed (WinError {ctypes.get_last_error()})")


def probe() -> Dict[str, Any]:
    return {"backend": "windows-low-integrity", "available": True,
            "enforces": {"write": True, "spawn": True, "network": False},
            "reason": "Windows 不给非管理员进程限制网络的办法；网络只在应用层拒绝"}


def _checked_output_directory(path: str) -> Path:
    directory = Path(path)
    if not directory.is_absolute() or directory.name != "out" or directory.parent.name != ".civil-buddy":
        raise PermissionError("Label setup is restricted to .civil-buddy/out")
    for component in (directory, *directory.parents):
        info = component.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PermissionError("Label setup cannot traverse links or non-directories")
    return directory


def _grant_output_owner_label_access(path: str) -> None:
    """Grant only WRITE_OWNER on our own out directory, never take ownership.

    A creator owns a directory but can inherit only Modify on a data drive.
    The owner can edit its DACL, while setting a mandatory label needs
    WRITE_OWNER. Preserve the old ACL; no inheritance, Full Control, other SID,
    privileges, or explicit/inherited WRITE_OWNER deny is added or overridden.
    https://learn.microsoft.com/en-us/windows/win32/secauthz/security-information
    """
    directory = _checked_output_directory(path)
    advapi32, kernel32 = _dlls()
    kernel32.LocalFree.argtypes, kernel32.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    advapi32.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.SetEntriesInAclW.argtypes = [wintypes.DWORD, ctypes.POINTER(_ExplicitAccess), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.SetEntriesInAclW.restype = wintypes.DWORD
    advapi32.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    token, descriptor, new_acl = wintypes.HANDLE(), ctypes.c_void_p(), ctypes.c_void_p()
    try:
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
            raise _fail("OpenProcessToken")
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))  # TokenUser
        if required.value < ctypes.sizeof(_SidAndAttributes) or required.value > 65536:
            raise _fail("GetTokenInformation(TokenUser size)")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(token, 1, buffer, required.value, ctypes.byref(required)):
            raise _fail("GetTokenInformation(TokenUser)")
        user = ctypes.cast(buffer, ctypes.POINTER(_SidAndAttributes)).contents.Sid
        owner, old_acl = ctypes.c_void_p(), ctypes.c_void_p()
        error = advapi32.GetNamedSecurityInfoW(str(directory), _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION, ctypes.byref(owner), None,
            ctypes.byref(old_acl), None, ctypes.byref(descriptor))
        if error:
            raise OSError(f"Cannot inspect output owner and DACL (error {error})")
        if not owner or not advapi32.EqualSid(owner, user):
            raise PermissionError("Output directory is not owned by the current user; its permissions are unchanged")
        if not old_acl:
            raise PermissionError("Cannot repair a missing output DACL")
        size = _AclSize()
        if not advapi32.GetAclInformation(old_acl, ctypes.byref(size), ctypes.sizeof(size), 2):
            raise _fail("GetAclInformation")
        for index in range(size.count):
            ace = ctypes.c_void_p()
            if not advapi32.GetAce(old_acl, index, ctypes.byref(ace)):
                raise _fail("GetAce")
            kind = ctypes.c_ubyte.from_address(ace.value).value
            mask = wintypes.DWORD.from_address(ace.value + 4).value
            if kind in {1, 6, 10, 12} and mask & (_WRITE_OWNER | _GENERIC_ALL):
                raise PermissionError("Output DACL denies label access; no deny rule is overridden")
        # GRANT_ACCESS=1, NO_INHERITANCE=0, TRUSTEE_IS_SID=0, TRUSTEE_IS_USER=1.
        entry = _ExplicitAccess(_WRITE_OWNER, 1, 0, _Trustee(None, 0, 0, 1, user))
        error = advapi32.SetEntriesInAclW(1, ctypes.byref(entry), old_acl, ctypes.byref(new_acl))
        if error:
            raise OSError(f"Cannot merge output label permission (error {error})")
        error = advapi32.SetNamedSecurityInfoW(str(directory), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
            None, None, new_acl, None)
        if error:
            raise OSError(f"Cannot set output label permission (error {error})")
    finally:
        if new_acl:
            kernel32.LocalFree(new_acl)
        if descriptor:
            kernel32.LocalFree(descriptor)
        if token:
            kernel32.CloseHandle(token)


def label_low(path: str) -> None:
    """Parent side: mark a directory (and what is created in it) writable by Low-integrity processes."""
    path = str(_checked_output_directory(path))  # Validate before *any* label or ACL change, not just fallback.
    advapi32, kernel32 = _dlls()
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(_LOW_LABEL_SDDL, 1, ctypes.byref(descriptor), None):
        raise _fail("ConvertStringSecurityDescriptorToSecurityDescriptor")
    try:
        present, defaulted, sacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorSacl(descriptor, ctypes.byref(present), ctypes.byref(sacl), ctypes.byref(defaulted)):
            raise _fail("GetSecurityDescriptorSacl")
        advapi32.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                                                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        error = advapi32.SetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _LABEL_SECURITY_INFORMATION, None, None, None, sacl)
        if error == 5:  # Access denied: only the directory's actual owner may repair its absent grant.
            _grant_output_owner_label_access(path)
            error = advapi32.SetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _LABEL_SECURITY_INFORMATION, None, None, None, sacl)
        if error:
            raise OSError(f"SetNamedSecurityInfo({path}) failed (error {error})")
    finally:
        kernel32.LocalFree(descriptor)


def _lower_integrity() -> None:
    advapi32, kernel32 = _dlls()
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_ADJUST_DEFAULT | _TOKEN_QUERY, ctypes.byref(token)):
        raise _fail("OpenProcessToken")
    sid = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(_LOW_SID, ctypes.byref(sid)):
        raise _fail("ConvertStringSidToSid")
    try:
        label = _SidAndAttributes(sid, _SE_GROUP_INTEGRITY)
        advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
        advapi32.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        if not advapi32.SetTokenInformation(token, _TOKEN_INTEGRITY_LEVEL, ctypes.byref(label),
                                            ctypes.sizeof(label) + advapi32.GetLengthSid(sid)):
            raise _fail("SetTokenInformation(TokenIntegrityLevel)")
    finally:
        kernel32.LocalFree(sid)
        kernel32.CloseHandle(token)


def _forbid_children() -> None:
    _advapi32, kernel32 = _dlls()
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise _fail("CreateJobObject")
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = _LIMIT_ACTIVE_PROCESS | _LIMIT_KILL_ON_CLOSE
    limits.BasicLimitInformation.ActiveProcessLimit = 1
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    if not kernel32.SetInformationJobObject(job, _JOB_EXTENDED_LIMIT, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise _fail("SetInformationJobObject")
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        raise _fail("AssignProcessToJobObject")
    # the job handle is deliberately kept open for the life of the process


def confine_self(write_roots: Sequence[str], *, network: bool = False, spawn: bool = False) -> Dict[str, Any]:
    """``write_roots`` were labelled Low by the parent; from here on nothing else can be written."""
    _lower_integrity()
    if not spawn:
        _forbid_children()
    return {"backend": "windows-low-integrity", "enforces": {"write": True, "spawn": not spawn, "network": False}}
