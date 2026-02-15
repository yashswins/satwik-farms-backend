# Simple & Secure: PostgreSQL on NAS with Tailscale

The easiest and most secure way to connect your Render backend to your UGREEN NAS database.

---

## Why Tailscale?

- ✅ **100% Free** for personal use
- ✅ **No port forwarding** needed
- ✅ **Secure** - Creates a private VPN mesh network
- ✅ **Works anywhere** - Render servers can access your NAS securely
- ✅ **Simple setup** - Just install and connect

---

## Complete Setup (30 minutes)

### **Part 1: Install PostgreSQL on Your NAS** (10 minutes)

#### 1.1: SSH into your UGREEN NAS

```bash
ssh admin@your-nas-ip-address
```

#### 1.2: Create PostgreSQL using Docker

```bash
# Create data directory
mkdir -p /volume1/docker/postgresql/data

# Run PostgreSQL container
docker run -d \
  --name satwik-postgres \
  --restart=unless-stopped \
  -e POSTGRES_USER=satwikfarms \
  -e POSTGRES_PASSWORD=YourStrongPassword123! \
  -e POSTGRES_DB=satwik_farms \
  -v /volume1/docker/postgresql/data:/var/lib/postgresql/data \
  -p 5432:5432 \
  postgres:16-alpine
```

**⚠️ IMPORTANT:** Replace `YourStrongPassword123!` with your own strong password!

#### 1.3: Verify it's running

```bash
# Check container status
docker ps | grep satwik-postgres

# Test connection
docker exec -it satwik-postgres psql -U satwikfarms -d satwik_farms
```

If you see the `satwik_farms=#` prompt, it's working! Type `\q` to exit.

---

### **Part 2: Set Up Tailscale** (10 minutes)

#### 2.1: Create Tailscale Account

1. Go to https://tailscale.com/
2. Click **Get Started** → **Sign up**
3. Sign up with Google/GitHub/Email (free account)

#### 2.2: Install Tailscale on Your NAS

```bash
# SSH into your NAS (if not already connected)
ssh admin@your-nas-ip

# Download and install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh

# Start Tailscale and authenticate
sudo tailscale up
```

This will print a URL. **Open it in your browser** and authorize the device.

#### 2.3: Get Your NAS's Tailscale IP

```bash
# Get the Tailscale IP (starts with 100.x.x.x)
tailscale ip -4
```

**Save this IP!** Example: `100.64.100.25`

---

### **Part 3: Install Tailscale on Render** (5 minutes)

Since Render doesn't natively support Tailscale, we'll use **Tailscale Subnet Router** approach:

#### Option A: Use Tailscale's App Connector (Easiest)

1. Go to Tailscale admin console: https://login.tailscale.com/admin
2. Click **Access Controls**
3. Add this ACL to allow Render to access your NAS:
   ```json
   {
     "acls": [
       {
         "action": "accept",
         "src": ["*"],
         "dst": ["*:5432"]
       }
     ]
   }
   ```

#### Option B: Use Docker Sidecar (Recommended for Render)

Unfortunately, Render's free tier doesn't support running Tailscale directly. **We need a different approach.**

---

### **Part 3 (Alternative): Use Tailscale Funnel** (Simplest!)

Tailscale Funnel lets you expose your database securely to the internet through Tailscale's network.

#### 3.1: Enable Tailscale Funnel on your NAS

```bash
# SSH into your NAS
ssh admin@your-nas-ip

# Enable Tailscale Funnel for PostgreSQL
tailscale funnel --bg 5432
```

#### 3.2: Get your Funnel URL

```bash
tailscale funnel status
```

This will show something like: `https://your-machine-name.tail-scale.ts.net:5432`

**Save this URL!**

---

### **Part 4: Update Render Backend** (5 minutes)

#### 4.1: Update DATABASE_URL

Your new connection string format:

**If using Tailscale IP (from Part 2.3):**
```
postgresql://satwikfarms:YourStrongPassword123!@100.64.100.25:5432/satwik_farms
```

**If using Tailscale Funnel:**
```
postgresql://satwikfarms:YourStrongPassword123!@your-machine-name.tail-scale.ts.net:5432/satwik_farms
```

#### 4.2: Set the variable in Render

1. Go to https://dashboard.render.com
2. Open your **satwik-farms-backend** service
3. Go to **Environment** tab
4. Click **Edit** on `DATABASE_URL`
5. Paste your new connection string
6. Click **Save Changes**

Render will automatically redeploy (takes 2-3 minutes).

---

## Testing

### Test 1: Check Backend Logs

1. In Render dashboard, click **Logs**
2. Look for any database connection errors
3. Should see successful startup

### Test 2: Test Health Endpoint

```bash
curl https://satwik-farms-backend.onrender.com/health
```

Expected: `{"status":"healthy","accu360_configured":true}`

### Test 3: Test from Your Android App

1. Open Satwik Farms app
2. Try placing a test order
3. Check if it saves successfully

---

## ⚠️ WAIT! There's a Better Way...

I just realized: **Render's free tier might not be able to connect via Tailscale** because we can't install Tailscale on their containers.

### The **ACTUALLY WORKING** Solution:

**Use ngrok (Free Tier) to expose your PostgreSQL**

#### 1. Install ngrok on your NAS

```bash
# SSH into NAS
ssh admin@your-nas-ip

# Download ngrok
wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm64.tgz
tar -xvzf ngrok-v3-stable-linux-arm64.tgz
sudo mv ngrok /usr/local/bin/

# Sign up at https://ngrok.com and get your auth token
ngrok authtoken YOUR_AUTH_TOKEN_HERE
```

#### 2. Create TCP tunnel for PostgreSQL

```bash
ngrok tcp 5432
```

You'll see output like:
```
Forwarding: tcp://0.tcp.ngrok.io:12345 -> localhost:5432
```

#### 3. Use ngrok URL in Render

Your `DATABASE_URL`:
```
postgresql://satwikfarms:YourPassword@0.tcp.ngrok.io:12345/satwik_farms
```

**⚠️ Problem:** ngrok free tier gives you a random URL that changes when you restart.

---

## 🎯 FINAL RECOMMENDATION

Given all the complications, here are your **THREE best options**:

### Option 1: Keep Database on Render (Simplest)
- Pay $7/month for Render PostgreSQL
- Zero setup hassle
- Keep everything working

### Option 2: Move EVERYTHING to Your NAS (Most Control)
- Host both backend API + database on your NAS
- Use Cloudflare Tunnel to expose the API
- Complete control, no costs
- Requires more setup

### Option 3: Use a Free PostgreSQL Provider
- **Supabase**: 500MB free database
- **Neon**: Free serverless Postgres
- **Railway**: Free tier with PostgreSQL
- Just change the `DATABASE_URL` in Render

---

## What Would I Recommend?

**For your use case (testing phase):**

1. **Short term:** Use **Neon** (https://neon.tech) - Free PostgreSQL, better than Render
   - Sign up → Create database → Copy connection string → Update Render
   - Takes 5 minutes, zero maintenance

2. **Long term:** When ready for production, move everything to your NAS
   - Host backend + database on NAS
   - Use Cloudflare Tunnel for API access
   - Complete control, no costs

**Want me to help you set up Neon instead?** It's literally 5 minutes and solves your problem immediately.
