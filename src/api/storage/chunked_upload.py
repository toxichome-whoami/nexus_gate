import os
import aiofiles
import hashlib
import shutil
from typing import Dict, Any, Optional

from api.errors import NexusGateException, ErrorCodes
from cache import CacheManager

class ChunkedUploadManager:
    """Manages multi-part chunked file uploads explicitly routing file operations."""

    # ─────────────────────────────────────────────────────────────────────────────
    # Internal Stream Utilities
    # ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    def _cleanup_failed_reassembly(cls, temp_dir: str, target_path: str) -> None:
        """Purges partially merged blobs isolating corruption events."""
        shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(target_path): 
            os.remove(target_path)

    @classmethod
    async def _stream_chunk_to_file(cls, chunk_path: str, outfile, sha256_hash) -> None:
        """Pipes file partitions verifying cryptographic headers asynchronously."""
        async with aiofiles.open(chunk_path, "rb") as infile:
            while c := await infile.read(65536):
                await outfile.write(c)
                sha256_hash.update(c)

    @classmethod
    async def _reassemble_chunks(cls, temp_dir: str, target_path: str, total_chunks: int) -> str:
        """Garbage collects missing fragments generating synchronized blocks natively."""
        sha256_hash = hashlib.sha256()
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        
        async with aiofiles.open(target_path, "wb") as outfile:
            for i in range(total_chunks):
                chunk_path = os.path.join(temp_dir, f"chunk_{i}")
                if not os.path.exists(chunk_path):
                    cls._cleanup_failed_reassembly(temp_dir, target_path)
                    raise NexusGateException(ErrorCodes.FS_UPLOAD_INVALID, f"Missing chunk sequence {i}", 400)
                     
                await cls._stream_chunk_to_file(chunk_path, outfile, sha256_hash)
                        
        return sha256_hash.hexdigest()

    @classmethod
    def _verify_final_hash(cls, final_hash: str, session: dict, target_path: str) -> None:
        """Rejects unverified payloads preventing corruption propagation."""
        if session.get("checksum_sha256") and final_hash != session["checksum_sha256"]:
            if os.path.exists(target_path): 
                os.remove(target_path)
            raise NexusGateException(ErrorCodes.FS_CHECKSUM_MISMATCH, "Final file checksum validation failed", 400)

    # ─────────────────────────────────────────────────────────────────────────────
    # Standard Interface API
    # ─────────────────────────────────────────────────────────────────────────────
    
    @classmethod
    async def initiate(cls, upload_id: str, data: Dict[str, Any], ttl: int = 3600):
        await CacheManager.set(f"upload:{upload_id}", data, ttl=ttl)
        os.makedirs(os.path.join("./storage", ".tmp", upload_id), exist_ok=True)
        
    @classmethod
    async def get_session(cls, upload_id: str) -> Optional[Dict[str, Any]]:
        return await CacheManager.get(f"upload:{upload_id}")
        
    @classmethod
    async def write_chunk(cls, upload_id: str, index: int, chunk_hash: str, file_bytes: bytes):
        session = await cls.get_session(upload_id)
        if not session:
            raise NexusGateException(ErrorCodes.FS_UPLOAD_EXPIRED, "Upload session missing or expired", 404)
            
        if hashlib.sha256(file_bytes).hexdigest() != chunk_hash:
            raise NexusGateException(ErrorCodes.FS_CHECKSUM_MISMATCH, "Chunk checksum block corrupted", 400)
            
        temp_path = os.path.join("./storage", ".tmp", upload_id, f"chunk_{index}")
        async with aiofiles.open(temp_path, "wb") as f:
            await f.write(file_bytes)
            
        session["uploaded_chunks"].append(index)
        session["uploaded_bytes"] += len(file_bytes)
        await CacheManager.set(f"upload:{upload_id}", session, ttl=3600)
        
    @classmethod
    async def finalize(cls, upload_id: str, target_path: str):
        session = await cls.get_session(upload_id)
        if not session:
            raise NexusGateException(ErrorCodes.FS_UPLOAD_EXPIRED, "Upload session unavailable", 404)
             
        if len(session["uploaded_chunks"]) < session["total_chunks"]:
            raise NexusGateException(ErrorCodes.FS_UPLOAD_INVALID, "Incomplete blob sequence detected", 400)
             
        temp_dir = os.path.join("./storage", ".tmp", upload_id)
        final_hash = await cls._reassemble_chunks(temp_dir, target_path, session["total_chunks"])
        
        shutil.rmtree(temp_dir, ignore_errors=True)
        await CacheManager.delete(f"upload:{upload_id}")
        
        cls._verify_final_hash(final_hash, session, target_path)
            
        return {
             "size": session["total_size"],
             "checksum_verified": True
        }
        
    @classmethod
    async def cancel(cls, upload_id: str):
        temp_dir = os.path.join("./storage", ".tmp", upload_id)
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        await CacheManager.delete(f"upload:{upload_id}")
