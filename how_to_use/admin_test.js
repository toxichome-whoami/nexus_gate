const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');

// ─────────────────────────────────────────────────────────────────────────────
// Configuration & Bootstrapping
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Loads environment variables from a .env file if it exists.
 */
function loadEnvironmentVariables() {
    const envPath = path.resolve(__dirname, '.env');
    if (!fs.existsSync(envPath)) return {};
    
    const lines = fs.readFileSync(envPath, 'utf8').split('\n');
    return Object.fromEntries(
        lines.filter(line => line.includes('='))
             .map(line => {
                 const [key, ...valueParts] = line.split('=');
                 return [key.trim(), valueParts.join('=').trim().replace(/"/g, '')];
             })
    );
}

const env = loadEnvironmentVariables();

const CONFIG = {
    url: env.NEXUSGATE_URL || 'http://localhost:4500',
    keyName: env.NEXUSGATE_KEY_NAME || 'example',
    keySecret: env.NEXUSGATE_KEY_SECRET || 'your_secret_key_here'
};

const AUTHORIZATION_HEADER = `Bearer ${Buffer.from(`${CONFIG.keyName}:${CONFIG.keySecret}`).toString('base64')}`;

// ─────────────────────────────────────────────────────────────────────────────
// HTTP Client Logic
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Executes an HTTP request to the NexusGate API.
 */
async function performRequest(apiPath, method = 'GET', body = null) {
    const url = new URL(`${CONFIG.url}${apiPath}`);
    const protocol = url.protocol === 'https:' ? https : http;
    
    const options = {
        method,
        rejectUnauthorized: false, // For testing self-signed certs
        headers: {
            'Authorization': AUTHORIZATION_HEADER,
            'Content-Type': 'application/json'
        }
    };

    return new Promise((resolve, reject) => {
        const req = protocol.request(url, options, (res) => {
            let responseData = '';
            res.on('data', chunk => responseData += chunk);
            res.on('end', () => handleResponse(res, responseData, resolve, reject));
        });

        req.on('error', reject);
        if (body) req.write(JSON.stringify(body));
        req.end();
    });
}

function handleResponse(res, data, resolve, reject) {
    try {
        const parsedData = JSON.parse(data);
        const isSuccess = res.statusCode >= 200 && res.statusCode < 300;
        
        if (isSuccess) {
            return resolve(parsedData);
        }
        
        const errorMessage = parsedData.error ? JSON.stringify(parsedData.error) : data;
        reject(new Error(`[${res.statusCode}] ${errorMessage}`));
    } catch (error) {
        reject(new Error(`[${res.statusCode}] Failed to parse response: ${data}`));
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Specific Test Routines
// ─────────────────────────────────────────────────────────────────────────────

async function testApiKeys() {
    process.stdout.write("🔑 Testing /api/v1/admin/keys... ");
    const keysResponse = await performRequest('/api/v1/admin/keys');
    console.log(`✅ Found ${keysResponse.data.keys.length} keys.`);
}

async function testBans() {
    process.stdout.write("🚫 Testing /api/v1/admin/bans... ");
    const bansResponse = await performRequest('/api/v1/admin/bans');
    console.log(`✅ IP Bans: ${bansResponse.data.ip_bans.length}, Key Bans: ${bansResponse.data.key_bans.length}`);
}

async function testIpBanManagement(ip = "1.2.3.4") {
    process.stdout.write(`🔨 Testing IP Ban (${ip})... `);
    await performRequest('/api/v1/admin/bans/ip', 'POST', { ip, reason: "Test Ban" });
    process.stdout.write("✅ | Unbanning... ");
    await performRequest(`/api/v1/admin/bans/ip/${ip}`, 'DELETE');
    console.log("✅");
}

async function testKeyBanManagement(keyName = "temporary_test_key") {
    process.stdout.write(`🔑 Testing Key Ban (${keyName})... `);
    await performRequest('/api/v1/admin/bans/key', 'POST', { key_name: keyName, reason: "Test Key Ban" });
    process.stdout.write("✅ | Unbanning... ");
    await performRequest(`/api/v1/admin/bans/key/${keyName}`, 'DELETE');
    console.log("✅");
}

async function testKeyLifecycle() {
    process.stdout.write("🆕 Testing Key Generation... ");
    const generationRes = await performRequest('/api/v1/admin/keys', 'POST', { name: "test_gen_key", mode: "readonly" });
    process.stdout.write(`✅ (${generationRes.data.name}) | Revoking... `);
    await performRequest(`/api/v1/admin/keys/${generationRes.data.name}`, 'DELETE');
    console.log("✅");
}

async function testCircuitBreakers() {
    process.stdout.write("⚡ Testing /api/v1/admin/circuit-breakers... ");
    const cbResponse = await performRequest('/api/v1/admin/circuit-breakers');
    const circuits = cbResponse.data.circuits;
    console.log(`✅ Active Circuits: ${Object.keys(circuits).length}`);

    if (Object.keys(circuits).length === 0) return;

    const firstCircuitKey = Object.keys(circuits)[0];
    process.stdout.write(`🔄 Resetting Circuit (${firstCircuitKey})... `);
    await performRequest(`/api/v1/admin/circuit-breakers/${firstCircuitKey}/reset`, 'POST');
    console.log("✅");
}

async function testServerConfig() {
    process.stdout.write("📝 Testing /api/v1/admin/config (REDACTED)... ");
    const configResponse = await performRequest('/api/v1/admin/config');
    console.log(`✅ Server Port: ${configResponse.data.config.server.port}`);
}

async function testRateLimits() {
    process.stdout.write("📊 Testing /api/v1/admin/rate-limits... ");
    const rateLimitResponse = await performRequest('/api/v1/admin/rate-limits');
    console.log(`✅ Max Requests: ${rateLimitResponse.data.global.max_requests}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Execution Entry Point
// ─────────────────────────────────────────────────────────────────────────────

async function executeAdminTests() {
    console.log("==========================================");
    console.log("🛡️  NEXUSGATE ADMIN API SECURITY TEST");
    console.log("==========================================\n");

    try {
        await testApiKeys();
        await testBans();
        await testIpBanManagement();
        await testKeyBanManagement();
        await testKeyLifecycle();
        await testCircuitBreakers();
        await testServerConfig();
        await testRateLimits();

        console.log("\n✨ ALL ADMIN TESTS PASSED! Your key is a verified admin.");
    } catch (error) {
        console.error(`\n❌ TEST FAILED: ${error.message}`);
        console.log("\nHELP: Verify that your key has 'full_admin = true' in config.toml.");
    }
}

executeAdminTests();
