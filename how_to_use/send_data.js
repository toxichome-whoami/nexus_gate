const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');

// ─────────────────────────────────────────────────────────────────────────────
// Constants & Configuration
// ─────────────────────────────────────────────────────────────────────────────

const TABLES_TO_SEED = [
    'users', 'products', 'orders', 'categories', 'inventory',
    'suppliers', 'reviews', 'locations', 'departments', 'employees',
    'projects', 'tasks', 'events', 'tags', 'settings',
    'logs', 'messages', 'sessions', 'profiles', 'teams'
];

function loadLocalEnv() {
    const envPath = path.resolve(__dirname, '.env');
    if (!fs.existsSync(envPath)) return {};

    return Object.fromEntries(
        fs.readFileSync(envPath, 'utf8').split('\n')
          .filter(l => l.includes('='))
          .map(l => {
              const [k, ...v] = l.split('=');
              return [k.trim(), v.join('=').trim().replace(/"/g, '')];
          })
    );
}

const env = loadLocalEnv();
const API_URL = env.NEXUSGATE_URL || 'http://localhost:4500';
const DB_NAME = env.NEXUSGATE_DB || 'example_db';
const AUTH_TOKEN = Buffer.from(`${env.NEXUSGATE_KEY_NAME || 'example'}:${env.NEXUSGATE_KEY_SECRET || 'your_secret_key_here'}`).toString('base64');

// ─────────────────────────────────────────────────────────────────────────────
// Low-level HTTP Client
// ─────────────────────────────────────────────────────────────────────────────

async function executeRequest(endpoint, method, requestBody) {
    const payloadString = JSON.stringify(requestBody);
    const url = new URL(API_URL);
    const agent = url.protocol === 'https:' ? https : http;

    const requestOptions = {
        hostname: url.hostname,
        port: url.port || (url.protocol === 'https:' ? 443 : 80),
        path: endpoint,
        method: method,
        rejectUnauthorized: false,
        headers: {
            'Authorization': `Bearer ${AUTH_TOKEN}`,
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(payloadString)
        }
    };

    return new Promise((resolve, reject) => {
        const req = agent.request(requestOptions, (res) => {
            let buffer = '';
            res.on('data', chunk => buffer += chunk);
            res.on('end', () => {
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    return resolve(JSON.parse(buffer));
                }
                reject(new Error(`[${res.statusCode}] ${buffer}`));
            });
        });
        req.on('error', reject);
        req.write(payloadString);
        req.end();
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Seeding Procedures
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Creates a standard table schema if it doesn't already exist.
 */
async function ensureTableExists(tableName) {
    const tableStructureSql = `
        CREATE TABLE IF NOT EXISTS ${tableName} (
            id INT AUTO_INCREMENT PRIMARY KEY,
            tag VARCHAR(255) DEFAULT 'test_item',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )`;

    return executeRequest(`/api/v1/db/${DB_NAME}/query`, 'POST', { sql: tableStructureSql });
}

/**
 * Injects initial test data into the target table.
 */
async function insertInitialData(tableName) {
    const dummyDataSql = `
        INSERT INTO ${tableName} (tag)
        VALUES ('${tableName}_seed_data_1'), ('${tableName}_seed_data_2'), ('${tableName}_seed_data_3')`;

    return executeRequest(`/api/v1/db/${DB_NAME}/query`, 'POST', { sql: dummyDataSql });
}

/**
 * Orchestrates the full database seeding process.
 */
async function startSeedingProcess() {
    console.log('🚀 Starting NexusGate seeder...');

    for (const tableName of TABLES_TO_SEED) {
        process.stdout.write(`Provisioning table: ${tableName}... `);
        try {
            await ensureTableExists(tableName);
            await insertInitialData(tableName);
            console.log('✅ Done');
        } catch (error) {
            console.log(`❌ Error: ${error.message}`);
        }
    }

    console.log('\n🌟 All tables seeded successfully!');
}

startSeedingProcess().catch(console.error);
