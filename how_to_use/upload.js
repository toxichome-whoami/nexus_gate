const fs = require('fs');
const http = require('http');
const path = require('path');
const crypto = require('crypto');

/**
 * NEXUSGATE CHUNKED UPLOADER
 * ──────────────────────────
 * Bypasses server body limits by splitting files into smaller 10MB segments.
 */

// --- 🛠️ Configurations ---
const envPath = path.resolve(__dirname, '.env');
const env = fs.existsSync(envPath)
    ? Object.fromEntries(fs.readFileSync(envPath, 'utf8').split('\n').filter(l => l.includes('=')).map(l => l.split('=').map(s => s.trim().replace(/"/g, ''))))
    : {};

const CONFIG = {
    url: env.NEXUSGATE_URL || 'http://localhost:4500',
    key_name: env.NEXUSGATE_KEY_NAME || 'example',
    secret: env.NEXUSGATE_KEY_SECRET || 'your_secret_key_here',
    chunk_size: 9437184, // ~9MB (Staying safely under the 10MB limit)
};

const authHeader = `Bearer ${Buffer.from(`${CONFIG.key_name}:${CONFIG.secret}`).toString('base64')}`;

async function request(apiPath, method, body = null, headers = {}) {
    const url = new URL(`${CONFIG.url}${apiPath}`);
    const options = {
        method: method,
        headers: {
            'Authorization': authHeader,
            ...headers
        }
    };

    return new Promise((resolve, reject) => {
        const req = http.request(url, options, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try {
                    const parsed = JSON.parse(data);
                    if (res.statusCode >= 200 && res.statusCode < 300) resolve(parsed);
                    else reject(new Error(`Status ${res.statusCode}: ${JSON.stringify(parsed.error || parsed)}`));
                } catch (e) {
                    reject(new Error(`Status ${res.statusCode}: ${data}`));
                }
            });
        });
        req.on('error', reject);
        if (body) req.write(body);
        req.end();
    });
}

async function listStorages() {
    process.stdout.write('📦 Fetching available storages...\n');
    try {
        const res = await request('/api/fs/storages', 'GET');
        console.table(res.data.storages.map(s => ({ "Alias": s.name, "Status": s.status, "Limit": s.limit })));
    } catch (e) {
        console.error(`❌ List Error: ${e.message}`);
    }
}

async function uploadFileChunked(alias, localPath, remotePath) {
    if (!fs.existsSync(localPath)) return console.error(`❌ Local file not found: ${localPath}`);

    const fileName = path.basename(localPath);
    const destPath = remotePath || fileName;
    const stats = fs.statSync(localPath);
    const totalSize = stats.size;

    console.log(`🚀 [START] Chunked Upload: '${fileName}' (${(totalSize / 1024 / 1024).toFixed(2)} MB)`);

    try {
        // 1. Initiate Session
        process.stdout.write('🔗 Initializing session... ');
        const init = await request(`/api/fs/${alias}/upload`, 'POST', JSON.stringify({
            action: 'initiate',
            filename: fileName,
            path: destPath,
            total_size: totalSize,
            chunk_size: CONFIG.chunk_size
        }), { 'Content-Type': 'application/json' });

        const uploadId = init.data.upload_id;
        console.log(`✅ ID: ${uploadId}`);

        // 2. Upload Chunks
        const totalChunks = init.data.total_chunks;
        const fd = fs.openSync(localPath, 'r');

        for (let i = 0; i < totalChunks; i++) {
            const buffer = Buffer.alloc(init.data.chunks[i].size);
            fs.readSync(fd, buffer, 0, buffer.length, init.data.chunks[i].offset);

            const hash = crypto.createHash('sha256').update(buffer).digest('hex');
            const percent = ((i / totalChunks) * 100).toFixed(1);

            process.stdout.write(`📤 Uploading Chunk ${i + 1}/${totalChunks} (${percent}%)... `);

            const boundary = `----ChunkBoundary${Math.random().toString(16)}`;
            const payload = Buffer.concat([
                Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="action"\r\n\r\nchunk\r\n`),
                Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="upload_id"\r\n\r\n${uploadId}\r\n`),
                Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="chunk_index"\r\n\r\n${i}\r\n`),
                Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="chunk_hash"\r\n\r\n${hash}\r\n`),
                Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="blob"\r\nContent-Type: application/octet-stream\r\n\r\n`),
                buffer,
                Buffer.from(`\r\n--${boundary}--\r\n`)
            ]);

            await request(`/api/fs/${alias}/upload`, 'POST', payload, {
                'Content-Type': `multipart/form-data; boundary=${boundary}`,
                'Content-Length': payload.length
            });
            console.log('✅');
        }
        fs.closeSync(fd);

        // 3. Finalize
        process.stdout.write('🏁 Finalizing and merging... ');
        const result = await request(`/api/fs/${alias}/upload`, 'POST', JSON.stringify({
            action: 'finalize',
            upload_id: uploadId
        }), { 'Content-Type': 'application/json' });

        console.log('✨ Success!');
        console.log(`🔗 Path: ${result.data.file.path}`);
    } catch (e) {
        console.error(`\n❌ ERROR: ${e.message}`);
    }
}

// --- CLI ---
const args = process.argv.slice(2);
if (args.length === 0) listStorages();
else if (args.length >= 2) uploadFileChunked(args[0], args[1], args[2]);
else console.log('Usage: node upload.js <alias> <local_path> [remote_path]');
