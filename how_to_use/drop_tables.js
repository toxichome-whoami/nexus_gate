const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');

// ─────────────────────────────────────────────────────────────────────────────
// Configuration & Environment
// ─────────────────────────────────────────────────────────────────────────────

function loadEnv() {
    const envPath = path.resolve(__dirname, '.env');
    if (!fs.existsSync(envPath)) return {};
    
    return Object.fromEntries(
        fs.readFileSync(envPath, 'utf8')
          .split('\n')
          .filter(line => line.includes('='))
          .map(line => {
              const [key, ...val] = line.split('=');
              return [key.trim(), val.join('=').trim().replace(/"/g, '')];
          })
    );
}

const env = loadEnv();

const CONFIG = {
    url: env.NEXUSGATE_URL || 'http://localhost:4500',
    databaseName: env.NEXUSGATE_DB || 'example_db',
    keyName: env.NEXUSGATE_KEY_NAME || 'example',
    secret: env.NEXUSGATE_KEY_SECRET || 'your_secret_key_here'
};

const B64_AUTH = Buffer.from(`${CONFIG.keyName}:${CONFIG.secret}`).toString('base64');

// ─────────────────────────────────────────────────────────────────────────────
// Request Engine
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Standard HTTP/S request wrapper.
 */
async function sendRequest(apiPath, method, payload = null) {
    const jsonPayload = payload ? JSON.stringify(payload) : '';
    const targetUrl = new URL(CONFIG.url);
    const protocolClient = targetUrl.protocol === 'https:' ? https : http;

    const options = {
        hostname: targetUrl.hostname,
        port: targetUrl.port || (targetUrl.protocol === 'https:' ? 443 : 80),
        path: apiPath,
        method: method,
        rejectUnauthorized: false,
        headers: {
            'Authorization': `Bearer ${B64_AUTH}`,
            'Content-Type': 'application/json',
            ...(payload ? { 'Content-Length': Buffer.byteLength(jsonPayload) } : {})
        }
    };

    return new Promise((resolve, reject) => {
        const req = protocolClient.request(options, (res) => {
            let chunkedData = '';
            res.on('data', chunk => chunkedData += chunk);
            res.on('end', () => processFinalResponse(res, chunkedData, resolve, reject));
        });

        req.on('error', reject);
        if (payload) req.write(jsonPayload);
        req.end();
    });
}

function processFinalResponse(res, rawData, resolve, reject) {
    const isSuccess = res.statusCode >= 200 && res.statusCode < 300;
    
    if (!isSuccess) {
        return reject(new Error(`Status ${res.statusCode}: ${rawData}`));
    }

    try {
        resolve(JSON.parse(rawData));
    } catch (error) {
        reject(new Error(`JSON Parse Error: ${error.message}. Content: ${rawData}`));
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Database Logic
// ─────────────────────────────────────────────────────────────────────────────

async function dropSingleTable(tableName) {
    process.stdout.write(`Dropping: ${tableName}... `);
    await sendRequest(`/api/db/${CONFIG.databaseName}/query`, 'POST', {
        sql: `DROP TABLE ${tableName}`
    });
    console.log('✅');
}

async function fetchAllTables() {
    const response = await sendRequest(`/api/db/${CONFIG.databaseName}/tables`, 'GET');
    return response.data.tables || [];
}

/**
 * Main routine to purge all tables from the configured database.
 */
async function purgeDatabase() {
    console.log('🗑️  Fetching all tables...');

    try {
        const tables = await fetchAllTables();

        if (tables.length === 0) {
            console.log('✨ No tables found. Database is already clean!');
            return;
        }

        console.log(`🧨 Found ${tables.length} tables. Starting drop...`);

        for (const table of tables) {
            await dropSingleTable(table.name);
        }

        console.log('\n🌌 All tables dropped. Database is empty.');
    } catch (error) {
        console.error(`❌ Cleanup failed: ${error.message}`);
    }
}

purgeDatabase().catch(console.error);
