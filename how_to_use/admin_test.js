const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const envPath = path.resolve(__dirname, '.env');
const env = fs.existsSync(envPath)
    ? Object.fromEntries(fs.readFileSync(envPath, 'utf8').split('\n').filter(l => l.includes('=')).map(l => l.split('=').map(s => s.trim().replace(/"/g, ''))))
    : {};

const CONFIG = {
    url: env.NEXUSGATE_URL || 'http://localhost:4500',
    key_name: env.NEXUSGATE_KEY_NAME || 'example',
    secret: env.NEXUSGATE_KEY_SECRET || 'your_secret_key_here'
};

const authHeader = `Bearer ${Buffer.from(`${CONFIG.key_name}:${CONFIG.secret}`).toString('base64')}`;

async function request(apiPath, method = 'GET', body = null) {
    const url = new URL(`${CONFIG.url}${apiPath}`);
    const options = {
        method: method,
        rejectUnauthorized: false,
        headers: {
            'Authorization': authHeader,
            'Content-Type': 'application/json'
        }
    };

    return new Promise((resolve, reject) => {
        const parsedUrl = new URL(CONFIG.url);
        const lib = parsedUrl.protocol === 'https:' ? https : http;
        const req = lib.request(url, options, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try {
                    const parsed = JSON.parse(data);
                    if (res.statusCode >= 200 && res.statusCode < 300) resolve(parsed);
                    else reject(new Error(`[${res.statusCode}] ${JSON.stringify(parsed.error || parsed)}`));
                } catch (e) {
                    reject(new Error(`[${res.statusCode}] ${data}`));
                }
            });
        });
        req.on('error', reject);
        if (body) req.write(JSON.stringify(body));
        req.end();
    });
}

async function runTests() {
    console.log("==========================================");
    console.log("🛡️  NEXUSGATE ADMIN API SECURITY TEST");
    console.log("==========================================\n");

    try {
        // 1. Fetch Keys
        process.stdout.write("🔑 Testing /api/admin/keys... ");
        const keys = await request('/api/admin/keys');
        console.log(`✅ Found ${keys.data.keys.length} keys.`);

        // 2. Fetch Bans
        process.stdout.write("🚫 Testing /api/admin/bans... ");
        const bans = await request('/api/admin/bans');
        console.log(`✅ IP Bans: ${bans.data.ip_bans.length}, Key Bans: ${bans.data.key_bans.length}`);

        // 3. Test Ban/Unban IP
        const testIp = "1.2.3.4";
        process.stdout.write(`🔨 Testing IP Ban (${testIp})... `);
        await request('/api/admin/bans/ip', 'POST', { ip: testIp, reason: "Test Ban" });
        process.stdout.write("✅ | Unbanning... ");
        await request(`/api/admin/bans/ip/${testIp}`, 'DELETE');
        console.log("✅");

        // 4. Test Ban/Unban Key
        const testKeyName = "temporary_test_key";
        process.stdout.write(`🔑 Testing Key Ban (${testKeyName})... `);
        await request('/api/admin/bans/key', 'POST', { key_name: testKeyName, reason: "Test Key Ban" });
        process.stdout.write("✅ | Unbanning... ");
        await request(`/api/admin/bans/key/${testKeyName}`, 'DELETE');
        console.log("✅");

        // 5. Test Key Generation & Revocation (Revocation just bans)
        process.stdout.write("🆕 Testing Key Generation... ");
        const newKey = await request('/api/admin/keys', 'POST', { name: "test_gen_key", mode: "readonly" });
        process.stdout.write(`✅ (${newKey.data.name}) | Revoking... `);
        await request(`/api/admin/keys/${newKey.data.name}`, 'DELETE');
        console.log("✅");

        // 6. Test Circuit Breakers & Reset
        process.stdout.write("⚡ Testing /api/admin/circuit-breakers... ");
        const cb = await request('/api/admin/circuit-breakers');
        console.log(`✅ Active Circuits: ${Object.keys(cb.data.circuits).length}`);

        if (Object.keys(cb.data.circuits).length > 0) {
            const firstKey = Object.keys(cb.data.circuits)[0];
            process.stdout.write(`🔄 Resetting Circuit (${firstKey})... `);
            await request(`/api/admin/circuit-breakers/${firstKey}/reset`, 'POST');
            console.log("✅");
        }

        // 7. Viewing Config (Redacted)
        process.stdout.write("📝 Testing /api/admin/config (REDACTED)... ");
        const cfg = await request('/api/admin/config');
        console.log(`✅ Server Port: ${cfg.data.config.server.port}`);

        // 8. Viewing Rate Limits
        process.stdout.write("📊 Testing /api/admin/rate-limits... ");
        const rl = await request('/api/admin/rate-limits');
        console.log(`✅ Max Requests: ${rl.data.global.max_requests}`);

        console.log("\n✨ ALL ADMIN TESTS PASSED! Your key is a REAL admin.");

    } catch (e) {
        console.error(`\n❌ TEST FAILED: ${e.message}`);
        console.log("\nHELP: Verify that your key has 'full_admin = true' in config.toml.");
    }
}

runTests();
