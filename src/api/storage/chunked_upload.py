import os
import aiofiles
import hashlib
from typing import Dict, Any, Optional

from api.errors import NexusGateException, ErrorCodes
from cache.__init__ import CacheManager

class ChunkedUploadManager:
    """Manages multi-part chunked file uploads."""
    
    @classmethod
    async def initiate(cls, upload_id: str, data: Dict[str, Any], ttl: int = 3600):
        # Store metadata in cache
        await CacheManager.set(f"upload:{upload_id}", data, ttl=ttl)
        
        # Temp dir for chunks
        temp_dir = os.path.join("./storage", ".tmp", upload_id)
        os.makedirs(temp_dir, exist_ok=True)
        
    @classmethod
    async def get_session(cls, upload_id: str) -> Optional[Dict[str, Any]]:
        return await CacheManager.get(f"upload:{upload_id}")
        
    @classmethod
    async def write_chunk(cls, upload_id: str, index: int, chunk_hash: str, file_bytes: bytes):
        session = await cls.get_session(upload_id)
        if not session:
            raise NexusGateException(ErrorCodes.FS_UPLOAD_EXPIRED, "Upload session not found or expired", 404)
            
        # Verify hash
        actual_hash = hashlib.sha256(file_bytes).hexdigest()
        if actual_hash != chunk_hash:
            raise NexusGateException(ErrorCodes.FS_CHECKSUM_MISMATCH, "Chunk checksum mismatch", 400)
            
        temp_path = os.path.join("./storage", ".tmp", upload_id, f"chunk_{index}")
        async with aiofiles.open(temp_path, "wb") as f:
            await f.write(file_bytes)
            
        # Update session
        session["uploaded_chunks"].append(index)
        session["uploaded_bytes"] += len(file_bytes)
        await CacheManager.set(f"upload:{upload_id}", session, ttl=3600)
        
    @classmethod
    async def finalize(cls, upload_id: str, target_path: str):
        session = await cls.get_session(upload_id)
        if not session:
             raise NexusGateException(ErrorCodes.FS_UPLOAD_EXPIRED, "Upload session not found or expired", 404)
             
        total_chunks = session["total_chunks"]
        if len(session["uploaded_chunks"]) < total_chunks:
             raise NexusGateException(ErrorCodes.FS_UPLOAD_INVALID, "Not all chunks uploaded", 400)
             
        temp_dir = os.path.join("./storage", ".tmp", upload_id)
        
        # Reassemble
        # We stream temp chunks to final destination, computing final hash on the fly
        sha256 = hashlib.sha256()
        
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        
        async with aiofiles.open(target_path, "wb") as outfile:
            for i in range(total_chunks):
                chunk_path = os.path.join(temp_dir, f"chunk_{i}")
                if not os.path.exists(chunk_path):
                     # Clean up partial completion
                     import shutil
                     shutil.rmtree(temp_dir, ignore_errors=True)
                     if os.path.exists(target_path): os.remove(target_path)
                     raise NexusGateException(ErrorCodes.FS_UPLOAD_INVALID, f"Missing chunk {i}", 400)
                     
                async with aiofiles.open(chunk_path, "rb") as infile:
                    while c := await infile.read(65536):
                        await outfile.write(c)
                        sha256.update(c)
                        
        final_hash = sha256.hexdigest()
        
        # Clean up temp
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        await CacheManager.delete(f"upload:{upload_id}")
        
        # Final integrity check
        if session.get("checksum_sha256") and final_hash != session["checksum_sha256"]:
            if os.path.exists(target_path): os.remove(target_path)
            raise NexusGateException(ErrorCodes.FS_CHECKSUM_MISMATCH, "Final file checksum mismatch", 400)
            
        return {
             "size": session["total_size"],
             "checksum_verified": True
        }
        
    @classmethod
    async def cancel(cls, upload_id: str):
        temp_dir = os.path.join("./storage", ".tmp", upload_id)
        if os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        await CacheManager.delete(f"upload:{upload_id}")
