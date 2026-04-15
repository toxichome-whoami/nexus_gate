const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');

// ─────────────────────────────────────────────────────────────────────────────
// Configuration Loader
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Extracts configuration from .env or defaults.
 */
function getConfiguration() {
    const envPath = path.resolve(__dirname, '.env');
    const envFileData = fs.existsSync(envPath) ? fs.readFileSync(envPath, 'utf8') : '';
    
    const env = Object.fromEntries(
        envFileData.split('\n')
            .filter(line => line.includes('='))
            .map(line => {
                const [key, ...value] = line.split('=');
                return [key.trim(), value.join('=').trim().replace(/"/g, '')];
            })
    );

    return {
        baseUrl: env.NEXUSGATE_URL || 'http://localhost:4500',
        keyName: env.NEXUSGATE_KEY_NAME || 'example',
        keySecret: env.NEXUSGATE_KEY_SECRET || 'your_secret_key_here'
    };
}

const CONFIG = getConfiguration();
const CREDENTIALS = Buffer.from(`${CONFIG.keyName}:${CONFIG.keySecret}`).toString('base64');

// ─────────────────────────────────────────────────────────────────────────────
// API Client
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Generic Fetcher for NexusGate API.
 */
async function fetchFromApi(endpoint, method = 'GET') {
    const url = new URL(CONFIG.baseUrl);
    const client = url.protocol === 'https:' ? https : http;

    const options = {
        hostname: url.hostname,
        port: url.port || (url.protocol === 'https:' ? 443 : 80),
        path: endpoint,
        method: method,
        rejectUnauthorized: false,
        headers: {
            'Authorization': `Bearer ${CREDENTIALS}`,
            'Content-Type': 'application/json'
        }
    };

    return new Promise((resolve, reject) => {
        const req = client.request(options, (res) => {
            let buffer = '';
            res.on('data', chunk => buffer += chunk);
            res.on('end', () => {
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    return resolve(JSON.parse(buffer));
                }
                reject(new Error(`API Error: ${res.statusCode} - ${buffer}`));
            });
        });
        
        req.on('error', reject);
        req.end();
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Domain Logic
// ─────────────────────────────────────────────────────────────────────────────

function displayDatabases(databases) {
    if (!databases || databases.length === 0) {
        console.log('📭 No databases configured.');
        return;
    }

    const formattedList = databases.map(db => ({
        "Name": db.name,
        "Engine": db.engine,
        "Status": db.status,
        "Tables": db.tables_count,
        "Mode": db.mode
    }));

    console.table(formattedList);
    console.log(`\n✨ Successfully fetched ${databases.length} database(s).`);
}

/**
 * Main command to retrieve and list all active databases.
 */
async function runDatabaseDiscovery() {
    console.log('🔍 Querying NexusGate for databases...\n');

    try {
        const apiResponse = await fetchFromApi('/api/v1/db/databases');
        displayDatabases(apiResponse.data.databases);
    } catch (error) {
        console.error(`❌ Fetch failed: ${error.message}`);
    }
}

runDatabaseDiscovery().catch(console.error);
