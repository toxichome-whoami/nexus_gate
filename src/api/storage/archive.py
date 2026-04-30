import os

from fastapi.responses import StreamingResponse
from zipstream.ng import ZipStream

from api.errors import ErrorCodes, NexusGateException

# ─────────────────────────────────────────────────────────────────────────────
# Sub-Routines
# ─────────────────────────────────────────────────────────────────────────────


def _append_folder_to_zip(
    z: ZipStream, folder_path: str, root: str, files: list
) -> None:
    """Traverses leaf nodes executing individual compress injections cleanly."""
    for file in files:
        full_path = os.path.join(root, file)
        rel_path = os.path.relpath(full_path, folder_path)
        z.add_path(full_path, arcname=rel_path)


def _generate_zip_stream(folder_path: str) -> ZipStream:
    """Walks directory paths building archive metadata."""
    z = ZipStream()
    for root, dirs, files in os.walk(folder_path):
        _append_folder_to_zip(z, folder_path, root, files)
    return z


# ─────────────────────────────────────────────────────────────────────────────
# Interface
# ─────────────────────────────────────────────────────────────────────────────


def stream_zip_folder(folder_path: str, filename: str) -> StreamingResponse:
    if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
        raise NexusGateException(
            ErrorCodes.FS_PATH_NOT_FOUND, f"Folder not found: {folder_path}", 404
        )

    z = _generate_zip_stream(folder_path)

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}.zip"',
    }

    def zip_streamer():
        yield from z

    return StreamingResponse(
        zip_streamer(), media_type="application/zip", headers=headers
    )
