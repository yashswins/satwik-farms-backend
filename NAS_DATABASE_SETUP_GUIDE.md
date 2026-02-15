# PostgreSQL on UGREEN NAS Setup Guide

Complete guide to move your PostgreSQL database from Render to your UGREEN NASync DXP4800 Plus, with secure connection via Cloudflare Tunnel.

---

## Overview

**What we're doing:**
1. Install PostgreSQL on your UGREEN NAS using Docker
2. Set up Cloudflare Tunnel for secure access (no port forwarding needed!)
3. Configure your Render backend to connect to the NAS database
4. Test the connection

**Benefits:**
- ✅ No monthly database costs
- ✅ Full control over your data
- ✅ No public exposure (Cloudflare Tunnel handles security)
- ✅ No port forwarding needed on your router

---

## Part 1: Set Up PostgreSQL on Your UGREEN NAS

### Step 1.1: Access Your NAS and Install Docker

1. **Log into your UGREEN NAS web interface**
   - Open a browser and go to your NAS IP address (e.g., `http://192.168.1.100`)
   - Log in with your admin credentials

2. **Enable/Install Docker** (if not already installed)
   - Go to **App Center** or **Package Manager**
   - Search for "Docker" or "Container Manager"
   - Install it if not already installed
   - Launch the Docker/Container Manager app

### Step 1.2: Create PostgreSQL Container

1. **Open Terminal/SSH on your NAS**
   - In UGOS, go to **Control Panel** → **Terminal & SNMP**
   - Enable SSH service
   - Connect via SSH: `ssh admin@your-nas-ip`
   - Or use the built-in terminal in the web interface

2. **Create a directory for PostgreSQL data**
   ```bash
   mkdir -p /volume1/docker/postgresql/data
   ```

3. **Run the PostgreSQL Docker container**
   ```bash
   docker run -d \
     --name satwik-farms-postgres \
     --restart=unless-stopped \
     -e POSTGRES_USER=satwikfarms \
     -e POSTGRES_PASSWORD=CHOOSE_A_STRONG_PASSWORD_HERE \
     -e POSTGRES_DB=satwik_farms \
     -v /volume1/docker/postgresql/data:/var/lib/postgresql/data \
     -p 5432:5432 \
     postgres:16-alpine
   ```

   **⚠️ IMPORTANT:** Replace `CHOOSE_A_STRONG_PASSWORD_HERE` with a strong password. Save this password - you'll need it later!

4. **Verify PostgreSQL is running**
   ```bash
   docker ps
   ```
   You should see the `satwik-farms-postgres` container running.

5. **Test local connection**
   ```bash
   docker exec -it satwik-farms-postgres psql -U satwikfarms -d satwik_farms
   ```
   If successful, you'll see a PostgreSQL prompt. Type `\q` to exit.

---

## Part 2: Set Up Cloudflare Tunnel (Secure Access)

Cloudflare Tunnel creates a secure connection from your NAS to the internet without opening any ports on your router.

### Step 2.1: Create Cloudflare Account (If You Don't Have One)

1. Go to https://dash.cloudflare.com/sign-up
2. Create a free account
3. You don't need a domain for this to work! (But if you have one, even better)

### Step 2.2: Install Cloudflared on Your NAS

1. **SSH into your NAS** (same as before)

2. **Download and install cloudflared**
   ```bash
   # Download cloudflared (ARM64 version for UGREEN NAS)
   cd /tmp
   wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64

   # Move to a permanent location
   sudo mv cloudflared-linux-arm64 /usr/local/bin/cloudflared
   sudo chmod +x /usr/local/bin/cloudflared

   # Verify installation
   cloudflared --version
   ```

3. **Authenticate with Cloudflare**
   ```bash
   cloudflared tunnel login
   ```
   This will print a URL. Open it in your browser, log in to Cloudflare, and authorize the tunnel.

4. **Create a tunnel**
   ```bash
   cloudflared tunnel create satwik-farms-db
   ```
   Save the **Tunnel ID** that appears - you'll need it!

5. **Create tunnel configuration**
   ```bash
   mkdir -p ~/.cloudflared
   nano ~/.cloudflared/config.yml
   ```

   Paste this configuration (replace `YOUR_TUNNEL_ID` with the ID from step 4):
   ```yaml
   tunnel: YOUR_TUNNEL_ID
   credentials-file: /root/.cloudflared/YOUR_TUNNEL_ID.json

   ingress:
     - service: tcp://localhost:5432
   ```

   Save with `Ctrl+O`, `Enter`, then exit with `Ctrl+X`

6. **Start the tunnel**
   ```bash
   cloudflared tunnel run satwik-farms-db
   ```

   Keep this terminal open for now. You should see "Connection registered" messages.

7. **Get your tunnel endpoint**
   - In the Cloudflare dashboard, go to **Zero Trust** → **Networks** → **Tunnels**
   - Click on your tunnel
   - You'll see a URL like: `https://your-tunnel-id.cfargotunnel.com`
   - **Save this URL!**

### Step 2.3: Make the Tunnel Run Automatically

1. **Stop the running tunnel** (press `Ctrl+C` in the terminal)

2. **Create a systemd service** (so it starts on boot)
   ```bash
   sudo nano /etc/systemd/system/cloudflared.service
   ```

   Paste this content:
   ```ini
   [Unit]
   Description=Cloudflare Tunnel
   After=network.target

   [Service]
   Type=simple
   User=root
   ExecStart=/usr/local/bin/cloudflared tunnel run satwik-farms-db
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

3. **Enable and start the service**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable cloudflared
   sudo systemctl start cloudflared
   sudo systemctl status cloudflared
   ```

---

## Part 3: Configure Render Backend to Use NAS Database

### Step 3.1: Build the Connection String

Your new `DATABASE_URL` will be:
```
postgresql://satwikfarms:YOUR_PASSWORD@YOUR_NAS_LOCAL_IP:5432/satwik_farms
```

**Example:**
```
postgresql://satwikfarms:MyStr0ngP@ssw0rd@192.168.1.100:5432/satwik_farms
```

Replace:
- `YOUR_PASSWORD` → The password you set in Part 1, Step 1.2
- `YOUR_NAS_LOCAL_IP` → Your NAS's local network IP (e.g., 192.168.1.100)

### Step 3.2: Update Render Environment Variables

**Option A: Using Render Dashboard**

1. Go to https://dashboard.render.com
2. Click on your **satwik-farms-backend** service
3. Go to **Environment** tab
4. Find the `DATABASE_URL` variable
5. Click **Edit** and replace it with your new connection string from Step 3.1
6. Click **Save Changes**
7. Render will automatically redeploy your backend

**Option B: Update render.yaml (Not Recommended for Now)**

Since you're using the database connection from Render's PostgreSQL service, we'll keep the `render.yaml` as is for now. You can remove the database section later once everything is working.

### Step 3.3: Test the Connection

1. **Wait for Render to finish redeploying** (2-3 minutes)

2. **Check your backend logs**
   - In Render dashboard, go to your backend service
   - Click **Logs** tab
   - Look for any database connection errors

3. **Test the health endpoint**
   ```bash
   curl https://satwik-farms-backend.onrender.com/health
   ```

   Expected response:
   ```json
   {
     "status": "healthy",
     "accu360_configured": true
   }
   ```

4. **Test from your Android app**
   - Open your Satwik Farms app
   - Try to place a test order
   - Check if it works!

---

## Part 4: Important Security Notes

### ✅ What's Secure:
- Database is on your local network only
- Render connects to your NAS via local network
- PostgreSQL is not exposed to the internet

### ⚠️ Additional Security Measures:

1. **Use strong database password** (already done in Part 1)

2. **Enable PostgreSQL SSL (Optional but Recommended)**
   ```bash
   # We can set this up later if needed
   ```

3. **Regular backups** - Set up automated backups:
   ```bash
   # Create backup script
   docker exec satwik-farms-postgres pg_dump -U satwikfarms satwik_farms > /volume1/backups/satwik_farms_$(date +%Y%m%d_%H%M%S).sql
   ```

---

## Troubleshooting

### Issue: "Connection refused" or timeout

**Solution:**
1. Check if PostgreSQL is running:
   ```bash
   docker ps | grep postgres
   ```

2. Check if you can connect locally:
   ```bash
   docker exec -it satwik-farms-postgres psql -U satwikfarms -d satwik_farms
   ```

3. Verify your NAS IP address:
   ```bash
   ip addr show
   ```

### Issue: Backend logs show "password authentication failed"

**Solution:**
- Double-check the password in your `DATABASE_URL`
- Make sure there are no special characters that need URL encoding (e.g., `@` should be `%40`, `#` should be `%23`)

### Issue: Backend can't reach NAS from Render

**Solution:**
- **This is expected!** Render's servers can't reach your local NAS
- You need to use Cloudflare Tunnel or another solution

**Wait, there's a problem here!** Render's backend is hosted on Render's servers, which can't access your local network. We need to use **Cloudflare Tunnel for TCP** or another approach.

---

## Alternative Approach: Cloudflare Tunnel for TCP (Advanced)

For Render to access your NAS database, we need to expose the database via Cloudflare Tunnel with TCP support.

Unfortunately, Cloudflare Tunnel's free tier doesn't support TCP tunneling for databases directly.

### Better Solutions:

1. **Use Tailscale (Recommended)** - Create a mesh VPN
2. **Use ngrok** - Simpler but paid for persistent URLs
3. **Use Render's Private Network** - Host both backend and database on Render
4. **Self-host the backend too** - Move both backend and database to your NAS

**Would you like me to guide you through the Tailscale approach instead?** It's the most reliable and secure way to connect Render to your NAS database.

---

## Next Steps

Let me know:
1. Did you successfully install PostgreSQL on your NAS?
2. Which connection method do you want to use (Tailscale recommended)?
3. Any errors you encountered?

I'll help you through each step!
