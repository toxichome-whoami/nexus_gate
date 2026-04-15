const fs = require('fs');
const http = require('http');
const https = require('https');
const path = require('path');
const crypto = require('crypto');

/**
 * NEXUSGATE CHUNKED UPLOADER
 * Efficiently handles large file uploads by segmenting them into small chunks.
 */

// ─────────────────────────────────────────────────────────────────────────────
// Configuration Loader
// ─────────────────────────────────────────────────────────────────────────────

function fetchConfig() {
    const envPath = path.resolve(__dirname, '.env');
    const envContent = fs.existsSync(envPath) ? fs.readFileSync(envPath, 'utf8') : '';
    
    const env = Object.fromEntries(
        envContent.split('\n')
            .filter(line => line.includes('='))
            .map(line => {
                const [key, ...rest] = line.split('=');
                return [key.trim(), rest.join('=').trim().replace(/"/g, '')];
            })
    );

    return {
        baseUrl: env.NEXUSGATE_URL || 'http://localhost:4500',
        keyName: env.NEXUSGATE_KEY_NAME || 'example',
        keySecret: env.NEXUSGATE_KEY_SECRET || 'your_secret_key_here',
        chunkSizeBytes: 9437184, // ~9MB
    };
}

const CONFIG = fetchConfig();
const AUTH_TOKEN_B64 = Buffer.from(`${CONFIG.keyName}:${CONFIG.keySecret}`).toString('base64');

// ─────────────────────────────────────────────────────────────────────────────
// API Engine
// ─────────────────────────────────────────────────────────────────────────────

async function apiCall(endpoint, method, payload = null, customHeaders = {}) {
    const url = new URL(`${CONFIG.baseUrl}${endpoint}`);
    const networkClient = url.protocol === 'https:' ? https : http;
    
    const requestOptions = {
        method,
        rejectUnauthorized: false,
        headers: {
            'Authorization': `Bearer ${AUTH_TOKEN_B64}`,
            ...customHeaders
        }
    };

    return new Promise((resolve, reject) => {
        const req = networkClient.request(url, requestOptions, (res) => {
            let buffer = '';
            res.on('data', chunk => buffer += chunk);
            res.on('end', () => processApiResponse(res, buffer, resolve, reject));
        });

        req.on('error', reject);
        if (payload) req.write(payload);
        req.end();
    });
}

function processApiResponse(res, rawBody, resolve, reject) {
    try {
        const parsed = JSON.parse(rawBody);
        const isOk = res.statusCode >= 200 && res.statusCode < 300;
        
        if (isOk) return resolve(parsed);
        
        const errorDesc = parsed.error ? JSON.stringify(parsed.error) : rawBody;
        reject(new Error(`[${res.statusCode}] ${errorDesc}`));
    } catch (e) {
        reject(new Error(`[${res.statusCode}] Could not parse JSON: ${rawBody}`));
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Upload Procedures
// ─────────────────────────────────────────────────────────────────────────────

async function discoverStorages() {
    process.stdout.write('📦 Fetching available storages...\n');
    try {
        const responseData = await apiCall('/api/fs/storages', 'GET');
        const summary = responseData.data.storages.map(s => ({
            "Alias": s.name, 
            "Status": s.status, 
            "Limit": s.limit 
        }));
        console.table(summary);
    } catch (error) {
        console.error(`❌ Discovery failed: ${error.message}`);
    }
}

async function startUploadSession(alias, fileName, remotePath, totalSize) {
    return apiCall(`/api/fs/${alias}/upload`, 'POST', JSON.stringify({
        action: 'initiate',
        filename: fileName,
        path: remotePath,
        total_size: totalSize,
        chunk_size: CONFIG.chunkSizeBytes
    }), { 'Content-Type': 'application/json' });
}

async function uploadSingleChunk(alias, uploadId, chunkIndex, buffer) {
    const chunkHash = crypto.createHash('sha256').update(buffer).digest('hex');
    const boundary = `----NexusGateBoundary${crypto.randomBytes(8).toString('hex')}`;
    
    const multipartBody = Buffer.concat([
        Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="action"\r\n\r\nchunk\r\n`),
        Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="upload_id"\r\n\r\n${uploadId}\r\n`),
        Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="chunk_index"\r\n\r\n${chunkIndex}\r\n`),
        Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="chunk_hash"\r\n\r\n${chunkHash}\r\n`),
        Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="blob"\r\nContent-Type: application/octet-stream\r\n\r\n`),
        buffer,
        Buffer.from(`\r\n--${boundary}--\r\n`)
    ]);

    return apiCall(`/api/fs/${alias}/upload`, 'POST', multipartBody, {
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': multipartBody.length
    });
}

/**
 * Orchestrates a complete chunked file transfer.
 */
async function runChunkedUploadFlow(alias, localFilePath, targetRelativePath) {
    if (!fs.existsSync(localFilePath)) return console.error(`❌ Local file missing: ${localFilePath}`);

    const fileName = path.basename(localFilePath);
    const destination = targetRelativePath || fileName;
    const fileStats = fs.statSync(localFilePath);
    const totalBytes = fileStats.size;

    console.log(`🚀 [START] Chunked Transfer: '${fileName}' (${(totalBytes / 1024 / 1024).toFixed(2)} MB)`);

    try {
        const initResponse = await startUploadSession(alias, fileName, destination, totalBytes);
        const { upload_id: uploadId, total_chunks: totalChunks, chunks: chunkMetadata } = initResponse.data;
        
        console.log(`✅ Session ID: ${uploadId}`);

        const fileDescriptor = fs.openSync(localFilePath, 'r');

        for (let i = 0; i < totalChunks; i++) {
            const currentChunkInfo = chunkMetadata[i];
            const buffer = Buffer.alloc(currentChunkInfo.size);
            fs.readSync(fileDescriptor, buffer, 0, buffer.length, currentChunkInfo.offset);

            const progress = ((i / totalChunks) * 100).toFixed(1);
            process.stdout.write(`📤 Transmitting Chunk ${i + 1}/${totalChunks} (${progress}%)... `);

            await uploadSingleChunk(alias, uploadId, i, buffer);
            console.log('✅');
        }
        
        fs.closeSync(fileDescriptor);

        process.stdout.write('🏁 Committing merge... ');
        const finalizeResponse = await apiCall(`/api/fs/${alias}/upload`, 'POST', JSON.stringify({
            action: 'finalize',
            upload_id: uploadId
        }), { 'Content-Type': 'application/json' });

        console.log('✨ Successful Transfer!');
        console.log(`🔗 Remote Path: ${finalizeResponse.data.file.path}`);
    } catch (error) {
        console.error(`\n❌ TRANSFER ERROR: ${error.message}`);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Command Line Interface
// ─────────────────────────────────────────────────────────────────────────────

function main() {
    const [aliasArg, localPathArg, remotePathArg] = process.argv.slice(2);

    if (!aliasArg) {
        return discoverStorages();
    }

    if (!localPathArg) {
        return console.log('Usage: node upload.js <alias> <local_path> [remote_path]');
    }

    runChunkedUploadFlow(aliasArg, localPathArg, remotePathArg);
}

main();
