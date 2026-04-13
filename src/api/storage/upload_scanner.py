"""
Upload security scanner.

Validates uploaded files by inspecting:
  1. File extension against allow/block lists.
  2. Magic bytes (file signature) to detect disguised executables.
  3. File size against configured limits.

This runs BEFORE the file hits disk, scanning only the first 1024 bytes
of the stream. Zero overhead on legitimate uploads.
"""
import os
import structlog
from typing import Optional, Set

logger = structlog.get_logger()

# Magic byte signatures for dangerous file types.
# Format: (offset, signature_bytes, description)
# These catch executables even if renamed to .jpg, .txt, etc.
DANGEROUS_SIGNATURES = [
    # Windows Executables
    (0, b"MZ", "PE/EXE/DLL"),                     # .exe, .dll, .sys, .scr
    # ELF (Linux executables)
    (0, b"\x7fELF", "ELF Binary"),                 # Linux binaries
    # Mach-O (macOS executables)
    (0, b"\xfe\xed\xfa\xce", "Mach-O 32-bit"),
    (0, b"\xfe\xed\xfa\xcf", "Mach-O 64-bit"),
    (0, b"\xca\xfe\xba\xbe", "Mach-O Universal"),
    # Java
    (0, b"\xca\xfe\xba\xbe", "Java Class"),
    (0, b"PK\x03\x04", "ZIP/JAR"),                 # .jar, .apk, .docx (needs context)
    # Scripts with shebangs
    (0, b"#!/", "Shell Script"),                    # .sh, .bash, .py with shebang
    (0, b"#!\\", "Shell Script (Windows)"),
    # Windows shortcuts
    (0, b"\x4c\x00\x00\x00", "Windows LNK"),
    # MSI Installer
    (0, b"\xd0\xcf\x11\xe0", "MS Compound/MSI"),
    # WebAssembly
    (0, b"\x00asm", "WebAssembly"),
]

# Extensions that should ALWAYS be blocked regardless of magic bytes
HARDCODED_BLOCKED_EXTENSIONS: Set[str] = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".pif",
    ".msi", ".msp", ".mst",
    ".cpl", ".hta", ".inf", ".ins", ".isp",
    ".jse", ".lnk", ".reg", ".rgs", ".sct",
    ".shb", ".shs", ".vbe", ".vbs", ".wsc",
    ".wsf", ".wsh", ".ws",
    ".ps1", ".ps1xml", ".ps2", ".ps2xml",
    ".psc1", ".psc2",
    ".sh", ".bash", ".csh", ".ksh",
    ".elf", ".bin", ".run",
    ".app", ".action", ".command",
    ".dll", ".sys", ".drv",
}


class UploadScanner:
    """Validates file uploads for security threats before writing to disk."""
    __slots__ = ("allowed_extensions", "blocked_extensions", "max_file_size")

    def __init__(
        self,
        allowed_extensions: Optional[list] = None,
        blocked_extensions: Optional[list] = None,
        max_file_size: int = 0,
    ):
        # If allowed_extensions is set, ONLY those are permitted (whitelist mode).
        # Otherwise, blocked_extensions acts as a blacklist.
        self.allowed_extensions = set(e.lower() for e in (allowed_extensions or []))
        self.blocked_extensions = HARDCODED_BLOCKED_EXTENSIONS | set(
            e.lower() for e in (blocked_extensions or [])
        )
        self.max_file_size = max_file_size

    def validate_filename(self, filename: str) -> None:
        """Check extension against allow/block lists."""
        _, ext = os.path.splitext(filename.lower())
        if not ext:
            return  # No extension, allow by default

        # Whitelist mode: only allowed extensions pass
        if self.allowed_extensions and ext not in self.allowed_extensions:
            raise ScannerRejectError(
                f"Extension '{ext}' is not in the allowed list",
                code="FS_EXTENSION_BLOCKED",
            )

        # Blacklist mode: blocked extensions are rejected
        if ext in self.blocked_extensions:
            raise ScannerRejectError(
                f"Extension '{ext}' is blocked for security reasons",
                code="FS_EXTENSION_BLOCKED",
            )

    def validate_size(self, content_length: int) -> None:
        """Pre-check declared size before reading any bytes."""
        if self.max_file_size > 0 and content_length > self.max_file_size:
            raise ScannerRejectError(
                f"File size {content_length} exceeds limit {self.max_file_size}",
                code="FS_FILE_TOO_LARGE",
            )

    def scan_magic_bytes(self, header_bytes: bytes, filename: str = "") -> None:
        """
        Inspect the first bytes of a file for dangerous signatures.
        This catches executables disguised as images, documents, etc.
        """
        for offset, signature, description in DANGEROUS_SIGNATURES:
            end = offset + len(signature)
            if len(header_bytes) >= end and header_bytes[offset:end] == signature:
                # Allow ZIP-based formats that are safe (.docx, .xlsx, .pptx, .zip)
                if signature == b"PK\x03\x04":
                    _, ext = os.path.splitext(filename.lower())
                    safe_zip_exts = {".zip", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".epub", ".cbz"}
                    if ext in safe_zip_exts:
                        continue

                logger.warning(
                    "Upload rejected: dangerous file signature detected",
                    filename=filename,
                    signature=description,
                )
                raise ScannerRejectError(
                    f"File contains a dangerous signature ({description}). "
                    f"Upload blocked for security.",
                    code="FS_EXTENSION_BLOCKED",
                )


class ScannerRejectError(Exception):
    """Raised when a file fails security validation."""
    __slots__ = ("message", "code")

    def __init__(self, message: str, code: str = "FS_EXTENSION_BLOCKED"):
        self.message = message
        self.code = code
        super().__init__(message)
