const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');
const crypto = require('crypto');
const readline = require('readline');

/**
 * NEXUSGATE UNIFIED TOOL (Messaging Enhanced)
 * ──────────────────────────────────────────────────────────────────────────
 * 1. Startup Check: Ensures 'messages' table exists in the database.
 * 2. Smart CLI: Auto-detects if you are typing an SQL query OR just a message.
 * 3. Webhook Receiver: Listens for notifications from the NexusGate.
 */

// --- 🛠️ 1. Configurations ---
const envPath = path.resolve(__dirname, '.env');
const env = fs.existsSync(envPath)
    ? Object.fromEntries(fs.readFileSync(envPath, 'utf8').split('\n').filter(l => l.includes('=')).map(l => l.split('=').map(s => s.trim().replace(/"/g, ''))))
    : {};

const CONFIG = {
    apiKeyName: env.NEXUSGATE_KEY_NAME || "example",
    apiKeySecret: env.NEXUSGATE_KEY_SECRET || "your_secret_key_here",
    webhookSecret: env.NEXUSGATE_WEBHOOK_SECRET || "your_webhook_secret_here",
    baseUrl: env.NEXUSGATE_URL || "http://localhost:4500",
    dbName: env.NEXUSGATE_DB || "example_db",
    serverPort: parseInt(env.TOOL_PORT || "3111"),
    receiverPath: '/api/sync'
};

const authHeader = `Bearer ${Buffer.from(`${CONFIG.apiKeyName}:${CONFIG.apiKeySecret}`).toString('base64')}`;

const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout
});

/**
 * 📡 Utility: Send Request to Gateway
 */
async function callGateway(path, body = null, method = 'POST') {
    const url = new URL(`${CONFIG.baseUrl}${path}`);
    const postData = body ? JSON.stringify(body) : '';

    const options = {
        method: method,
        rejectUnauthorized: false,
        headers: {
            'Authorization': authHeader,
            'Content-Type': 'application/json',
            'X-NexusGate-Webhook-Token': CONFIG.webhookSecret,
            ...(body ? { 'Content-Length': Buffer.byteLength(postData) } : {})
        }
    };

    return new Promise((resolve, reject) => {
        const parsedUrl = new URL(CONFIG.url);
        const lib = parsedUrl.protocol === 'https:' ? https : http;
        const req = lib.request(url, options, (res) => {
            let data = '';
            res.on('data', chunk => { data += chunk; });
            res.on('end', () => {
                try {
                    const parsed = JSON.parse(data);
                    if (res.statusCode >= 200 && res.statusCode < 300) resolve(parsed);
                    else reject(new Error(parsed.error?.message || `Status ${res.statusCode}`));
                } catch (e) {
                    reject(new Error(`Failed to parse response: ${data}`));
                }
            });
        });
        req.on('error', reject);
        if (body) req.write(postData);
        req.end();
    });
}

/**
 * 🛠️ 2. Startup Database Verification
 */
async function ensureTablesExist() {
    process.stdout.write(`🔍 Checking if table 'messages' exists... `);
    try {
        const tablesRes = await callGateway(`/api/db/${CONFIG.dbName}/tables`, null, 'GET');
        const exists = tablesRes.data.tables.some(t => t.name.toLowerCase() === 'messages');

        if (exists) {
            console.log('✅ Found.');
        } else {
            console.log('❌ Missing.');
            process.stdout.write(`🏗️  Creating table 'messages'... `);
            await callGateway(`/api/db/${CONFIG.dbName}/query`, {
                sql: `CREATE TABLE IF NOT EXISTS messages (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )`
            });
            console.log('✅ Created.');
        }
    } catch (e) {
        console.log(`⚠️  Warning: ${e.message}`);
        console.log(`   (Checking permissions or database status)\n`);
    }
}

/**
 * 🔒 3. Webhook Signature Verification
 */
function verifySignature(payload, signature) {
    if (!CONFIG.webhookSecret) return true;
    const hmac = crypto.createHmac('sha256', CONFIG.webhookSecret);
    const digest = hmac.update(payload).digest('hex');
    return `sha256=${digest}` === signature;
}

/**
 * 📡 4. The Webhook Receiver Server
 */
const server = http.createServer((req, res) => {
    if (req.method === 'POST' && req.url === CONFIG.receiverPath) {
        let body = '';
        req.on('data', chunk => { body += chunk; });
        req.on('end', () => {
            const signature = req.headers['x-nexusgate-signature'];
            const timestamp = req.headers['x-nexusgate-timestamp'];
            const isValid = verifySignature(body, signature);

            console.log(`\n\n🔔 [WEBHOOK RECEIVED] Path: ${req.url}`);
            if (!isValid) {
                console.log(`❌ [SECURITY] Invalid HMAC Signature! Blocked.`);
                res.writeHead(401);
                return res.end();
            }

            console.log(`✅ [SIGNATURE] Verified via HMAC-SHA256`);
            try {
                const data = JSON.parse(body);
                console.log(`📦 [DATA]`, JSON.stringify(data.data, null, 2));
            } catch (e) {
                console.log(`📦 [DATA] Raw Body: ${body}`);
            }

            process.stdout.write('\n💬 Enter Message or SQL: ');
            res.writeHead(200);
            res.end('OK');
        });
    } else {
        res.writeHead(404);
        res.end();
    }
});

/**
 * 💬 5. The Interactive CLI Trigger
 */
function ask() {
    rl.question('\n💬 Enter Message or SQL: ', async (input) => {
        if (!input.trim()) return ask();
        if (input.toLowerCase() === 'exit') process.exit(0);

        let sql = input;

        // Auto-detect if it's SQL or JUST a text message
        const isSQL = /^(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|DESCRIBE|SHOW)/i.test(input.trim());

        if (!isSQL) {
            // Treat as a direct message and wrap it in an INSERT statement
            const cleanMessage = input.replace(/'/g, "''"); // Escape single quotes
            sql = `INSERT INTO messages (content) VALUES ('${cleanMessage}')`;
            console.log(`📝 Wrapping text as message...`);
        } else {
            console.log(`🛠️  Executing raw SQL...`);
        }

        try {
            const result = await callGateway(`/api/db/${CONFIG.dbName}/query`, { sql });

            if (isSQL) {
               console.log(`✅ SQL Success! Affected rows: ${result.data.affected_rows}`);
               if (result.data.rows && result.data.rows.length > 0) {
                   console.table(result.data.rows.slice(0, 5));
               }
            } else {
               console.log(`🚀 Message sent to database!`);
            }
        } catch (e) {
            console.log(`❌ Gateway Error: ${e.message}`);
        }
        ask();
    });
}

/**
 * 🚀 6. Startup
 */
(async () => {
    console.log("==========================================");
    console.log("🚀 NEXUSGATE UNIFIED MANAGEMENT TOOL");
    console.log("==========================================");

    // 1. Check and Create Table on start
    await ensureTablesExist();

    // 2. Start Receiver
    server.listen(CONFIG.serverPort, '0.0.0.0', () => {
        console.log(`📡 Receiver Webhook URL: http://localhost:${CONFIG.serverPort}${CONFIG.receiverPath}`);
        console.log(`🔑 Security: HMAC-SHA256`);
        console.log("------------------------------------------");
        console.log("Just type a message and hit Enter to save it, or type SQL.\n");
        ask();
    });
})();
