"""
Upload security scanner.
Validates uploaded files by inspecting: extensions, magic bytes, and sizing limits.
"""

import os
from typing import Optional, Set

import structlog

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# Signature Maps
# ─────────────────────────────────────────────────────────────────────────────

DANGEROUS_SIGNATURES = [
    (0, b"MZ", "PE/EXE/DLL"),
    (0, b"\x7fELF", "ELF Binary"),
    (0, b"\xfe\xed\xfa\xce", "Mach-O 32-bit"),
    (0, b"\xfe\xed\xfa\xcf", "Mach-O 64-bit"),
    (0, b"\xca\xfe\xba\xbe", "Java Class or Mac Universal"),
    (0, b"PK\x03\x04", "ZIP/JAR"),
    (0, b"#!/", "Shell Script"),
    (0, b"#!\\", "Shell Script (Windows)"),
    (0, b"\x4c\x00\x00\x00", "Windows LNK"),
    (0, b"\xd0\xcf\x11\xe0", "MS Compound/MSI"),
    (0, b"\x00asm", "WebAssembly"),
]

HARDCODED_BLOCKED_EXTENSIONS: Set[str] = {
    ".exe",
    ".bat",
    ".cmd",
    ".com",
    ".scr",
    ".pif",
    ".msi",
    ".msp",
    ".mst",
    ".cpl",
    ".hta",
    ".inf",
    ".ins",
    ".isp",
    ".jse",
    ".lnk",
    ".reg",
    ".rgs",
    ".sct",
    ".shb",
    ".shs",
    ".vbe",
    ".vbs",
    ".wsc",
    ".wsf",
    ".wsh",
    ".ws",
    ".ps1",
    ".ps1xml",
    ".ps2",
    ".ps2xml",
    ".psc1",
    ".psc2",
    ".sh",
    ".bash",
    ".csh",
    ".ksh",
    ".elf",
    ".bin",
    ".run",
    ".app",
    ".action",
    ".command",
    ".dll",
    ".sys",
    ".drv",
}

# ─────────────────────────────────────────────────────────────────────────────
# Validations
# ─────────────────────────────────────────────────────────────────────────────


class ScannerRejectError(Exception):
    __slots__ = ("message", "code")

    def __init__(self, message: str, code: str = "FS_EXTENSION_BLOCKED"):
        self.message = message
        self.code = code
        super().__init__(message)


class UploadScanner:
    __slots__ = ("allowed_extensions", "blocked_extensions", "max_file_size")

    def __init__(
        self,
        allowed_extensions: Optional[list] = None,
        blocked_extensions: Optional[list] = None,
        max_file_size: int = 0,
    ):
        self.allowed_extensions = set(e.lower() for e in (allowed_extensions or []))
        self.blocked_extensions = HARDCODED_BLOCKED_EXTENSIONS | set(
            e.lower() for e in (blocked_extensions or [])
        )
        self.max_file_size = max_file_size

    def validate_filename(self, filename: str) -> None:
        _, ext = os.path.splitext(filename.lower())
        if not ext:
            return

        if self.allowed_extensions and ext not in self.allowed_extensions:
            raise ScannerRejectError(
                f"Extension '{ext}' explicitly missing from allowed maps.",
                "FS_EXTENSION_BLOCKED",
            )

        if ext in self.blocked_extensions:
            raise ScannerRejectError(
                f"Extension '{ext}' triggers security restrictions.",
                "FS_EXTENSION_BLOCKED",
            )

    def validate_size(self, content_length: int) -> None:
        if self.max_file_size > 0 and content_length > self.max_file_size:
            raise ScannerRejectError(
                f"File sizing maps ({content_length}) exceed explicitly bound limits.",
                "FS_FILE_TOO_LARGE",
            )

    def _is_safe_archive(self, signature: bytes, filename: str) -> bool:
        """Filters natively safe Office schema wrappers explicitly avoiding blocking documents."""
        if signature == b"PK\x03\x04":
            return os.path.splitext(filename.lower())[1] in {
                ".zip",
                ".docx",
                ".xlsx",
                ".pptx",
                ".odt",
                ".ods",
                ".epub",
                ".cbz",
            }
        return False

    def scan_magic_bytes(self, header_bytes: bytes, filename: str = "") -> None:
        for offset, signature, description in DANGEROUS_SIGNATURES:
            if (
                len(header_bytes) >= offset + len(signature)
                and header_bytes[offset : offset + len(signature)] == signature
            ):
                if self._is_safe_archive(signature, filename):
                    continue

                logger.warning(
                    "Upload rejected: dangerous file signature detected",
                    filename=filename,
                    signature=description,
                )
                raise ScannerRejectError(
                    f"Signature triggers execution flag ({description}).",
                    "FS_EXTENSION_BLOCKED",
                )
