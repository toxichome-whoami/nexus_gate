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
    db_name: env.NEXUSGATE_DB || 'example_db',
    key_name: env.NEXUSGATE_KEY_NAME || 'example',
    secret: env.NEXUSGATE_KEY_SECRET || 'your_secret_key_here'
};

const tables = [
    'users', 'products', 'orders', 'categories', 'inventory',
    'suppliers', 'reviews', 'locations', 'departments', 'employees',
    'projects', 'tasks', 'events', 'tags', 'settings',
    'logs', 'messages', 'sessions', 'profiles', 'teams'
];

const auth_token = Buffer.from(`${CONFIG.key_name}:${CONFIG.secret}`).toString('base64');

async function request(path, method, body) {
    const data = JSON.stringify(body);
    const options = {
        hostname: 'localhost',
        port: 4500,
        path: path,
        method: method,
        headers: {
            'Authorization': `Bearer ${auth_token}`,
            'Content-Type': 'application/json',
            'Content-Length': data.length
        }
    };

    return new Promise((resolve, reject) => {
        const req = http.request(options, (res) => {
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
        req.write(data);
        req.end();
    });
}

async function seed() {
    console.log('🚀 Starting NexusGate seeder...');

    for (const table of tables) {
        process.stdout.write(`Creating table: ${table}... `);
        try {
            // 1. Create Table
            await request(`/api/db/${CONFIG.db_name}/query`, 'POST', {
                sql: `CREATE TABLE IF NOT EXISTS ${table} (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    tag VARCHAR(255) DEFAULT 'test_item',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )`
            });

            // 2. Insert dummy data
            await request(`/api/db/${CONFIG.db_name}/query`, 'POST', {
                sql: `INSERT INTO ${table} (tag) VALUES ('${table}_seed_data_1'), ('${table}_seed_data_2'), ('${table}_seed_data_3')`
            });

            console.log('✅ Done');
        } catch (error) {
            console.log(`❌ Error: ${error.message}`);
        }
    }

    console.log('\n🌟 All tables seeded successfully!');
}

seed().catch(console.error);
