const http = require('http');
const fs = require('fs');
const path = require('path');

// --- 🛠️ Configurations ---
const envPath = path.resolve(__dirname, '.env');
const env = fs.existsSync(envPath)
    ? Object.fromEntries(fs.readFileSync(envPath, 'utf8').split('\n').filter(l => l.includes('=')).map(l => l.split('=').map(s => s.trim().replace(/"/g, ''))))
    : {};

const CONFIG = {
    url: env.NEXUSGATE_URL || 'http://localhost:4500',
    key_name: env.NEXUSGATE_KEY_NAME || 'example',
    secret: env.NEXUSGATE_KEY_SECRET || 'your_secret_key_here'
};

const auth_token = Buffer.from(`${CONFIG.key_name}:${CONFIG.secret}`).toString('base64');

async function request(path, method) {
    const options = {
        hostname: 'localhost',
        port: 4500,
        path: path,
        method: method,
        headers: {
            'Authorization': `Bearer ${auth_token}`,
            'Content-Type': 'application/json'
        }
    };

    return new Promise((resolve, reject) => {
        const req = http.request(options, (res) => {
            let data = '';
            res.on('data', (chunk) => data += chunk);
            res.on('end', () => {
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    resolve(JSON.parse(data));
                } else {
                    reject(new Error(`Status ${res.statusCode}: ${data}`));
                }
            });
        });
        req.on('error', reject);
        req.end();
    });
}

async function fetchDatabases() {
    console.log('🔍 Querying NexusGate for databases...\n');

    try {
        const response = await request('/api/db/databases', 'GET');
        const dbs = response.data.databases;

        if (!dbs || dbs.length === 0) {
            console.log('📭 No databases configured.');
            return;
        }

        console.table(dbs.map(db => ({
            "Name": db.name,
            "Engine": db.engine,
            "Status": db.status,
            "Tables": db.tables_count,
            "Mode": db.mode
        })));

        console.log(`\n✨ Successfully fetched ${dbs.length} database(s).`);
    } catch (error) {
        console.error(`❌ Fetch failed: ${error.message}`);
    }
}

fetchDatabases().catch(console.error);
