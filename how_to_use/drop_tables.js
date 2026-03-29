const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');

// --- 🛠️ Configurations ---
const envPath = path.resolve(__dirname, '.env');
const env = fs.existsSync(envPath)
    ? Object.fromEntries(fs.readFileSync(envPath, 'utf8').split('\n').filter(l => l.includes('=')).map(l => l.split('=').map(s => s.trim().replace(/"/g, ''))))
    : {};

const CONFIG = {
    url: env.NEXUSGATE_URL || 'http://localhost:4500',
    db_name: env.NEXUSGATE_DB || 'example_db',
    key_name: env.NEXUSGATE_KEY_NAME || 'example',
    secret: env.NEXUSGATE_KEY_SECRET || 'your_secret_key_here'
};

const auth_token = Buffer.from(`${CONFIG.key_name}:${CONFIG.secret}`).toString('base64');

async function request(path, method, body = null) {
    const data = body ? JSON.stringify(body) : '';
    const options = {
        hostname: new URL(CONFIG.url).hostname,
        port: new URL(CONFIG.url).port || (new URL(CONFIG.url).protocol === 'https:' ? 443 : 80),
        path: path,
        method: method,
        rejectUnauthorized: false,
        headers: {
            'Authorization': `Bearer ${auth_token}`,
            'Content-Type': 'application/json',
            ...(body ? { 'Content-Length': data.length } : {})
        }
    };

    return new Promise((resolve, reject) => {
        const parsedUrl = new URL(CONFIG.url);
        const lib = parsedUrl.protocol === 'https:' ? https : http;
        const req = lib.request(options, (res) => {
            let resData = '';
            res.on('data', (chunk) => resData += chunk);
            res.on('end', () => {
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    resolve(JSON.parse(resData));
                } else {
                    reject(new Error(`Status ${res.statusCode}: ${resData}`));
                }
            });
        });
        req.on('error', reject);
        if (body) req.write(data);
        req.end();
    });
}

async function cleanup() {
    console.log('🗑️  Fetching all tables...');

    try {
        const response = await request(`/api/db/${CONFIG.db_name}/tables`, 'GET');
        const tables = response.data.tables;

        if (!tables || tables.length === 0) {
            console.log('✨ No tables found. Database is clean!');
            return;
        }

        console.log(`🧨 Found ${tables.length} tables. Starting drop...`);

        for (const table of tables) {
            process.stdout.write(`Dropping: ${table.name}... `);
            await request(`/api/db/${CONFIG.db_name}/query`, 'POST', {
                sql: `DROP TABLE ${table.name}`
            });
            console.log('✅');
        }

        console.log('\n🌌 All tables dropped. Database is empty.');
    } catch (error) {
        console.error(`❌ Cleanup failed: ${error.message}`);
    }
}

cleanup().catch(console.error);
