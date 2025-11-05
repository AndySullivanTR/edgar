# VPS Server Guide - SEC EDGAR Monitoring

## What This Is

A Virtual Private Server (VPS) running on DigitalOcean that monitors SEC EDGAR filings 24/7 for investigative journalism at Thomson Reuters.

**Server Details:**
- **IP Address:** 165.227.107.74
- **Operating System:** Ubuntu 25.10 Linux
- **Provider:** DigitalOcean
- **Cost:** $6/month
- **Server Name:** rockygap

---

## What's Running

### 1. SEC Cybersecurity Incident Monitor
- **Script:** `sec_cyber_monitor.py`
- **Location:** `/root/edgar/sec-cyber-monitor/`
- **Schedule:** Checks every 15 minutes
- **Purpose:** Monitors all SEC filings for nation-state cyber attack disclosures
- **Output:** `cyber_incidents.csv`

### 2. BIS/China Trade Monitor
- **Script:** `bis_china_monitor.py`
- **Location:** `/root/edgar/bis-china_monitor/`
- **Schedule:** Checks every 5 minutes
- **Purpose:** Monitors 33 semiconductor companies for China-related trade restriction disclosures from Bureau of Industry and Security
- **Output:** `bis_china_disclosures.csv`
- **Email Alerts:** Sends to andy.sullivan@thomsonreuters.com and chris.sanders@thomsonreuters.com

**Both monitors:**
- Run continuously in the background
- Survive server restarts
- Log all matches with excerpts and direct links to SEC filings

---

## How to Access the Server

### Connecting via SSH

**From Windows Terminal or PowerShell:**
```powershell
ssh root@165.227.107.74
```

Enter your password when prompted.

You're now controlling the Linux server remotely.

---

## Essential Commands

### Check What's Running
```bash
screen -ls
```
Should show:
```
9348.cyber (Detached)
XXXX.bis   (Detached)
```

### View the Cyber Monitor
```bash
screen -r cyber
```
- See what it's doing in real-time
- Detach without stopping: Press `Ctrl+A` then `D`

### View the BIS Monitor
```bash
screen -r bis
```
- See what it's doing in real-time
- Detach without stopping: Press `Ctrl+A` then `D`

### Stop a Monitor
```bash
screen -r cyber
# Press Ctrl+C to stop
```

### Restart a Monitor
```bash
screen -r cyber
# Press Ctrl+C to stop
cd /root/edgar/sec-cyber-monitor
python3 sec_cyber_monitor.py
# Press Ctrl+A then D to detach
```

### Disconnect from Server
```bash
exit
```
The monitors keep running after you disconnect.

---

## Downloading Results

### Option 1: WinSCP (Recommended)

1. **Download WinSCP:** [winscp.net](https://winscp.net/)
2. **Open WinSCP**
3. **Connection settings:**
   - Host name: `165.227.107.74`
   - User name: `root`
   - Password: (your server password)
4. **Click Login**
5. **Navigate to:**
   - `/root/edgar/sec-cyber-monitor/` for cyber incidents CSV
   - `/root/edgar/bis-china_monitor/` for BIS disclosures CSV
6. **Drag files** from right panel (server) to left panel (your PC)

### Option 2: Command Line
```bash
scp root@165.227.107.74:/root/edgar/sec-cyber-monitor/cyber_incidents.csv .
scp root@165.227.107.74:/root/edgar/bis-china_monitor/bis_china_disclosures.csv .
```

---

## Updating the Scripts

### When You Make Changes on Your Windows PC:

**1. Commit and push to GitHub:**
```powershell
cd C:\Users\8010317\projects\government-bots\SEC-cyber
git add .
git commit -m "description of changes"
git push
```

**2. SSH into the VPS:**
```powershell
ssh root@165.227.107.74
```

**3. Update and restart monitors:**

**For cyber monitor:**
```bash
cd /root/edgar
git pull
screen -r cyber
# Press Ctrl+C to stop
cd sec-cyber-monitor
python3 sec_cyber_monitor.py
# Press Ctrl+A then D to detach
```

**For BIS monitor:**
```bash
cd /root/edgar
git pull
screen -r bis
# Press Ctrl+C to stop
cd bis-china_monitor
python3 bis_china_monitor.py
# Press Ctrl+A then D to detach
```

---

## GitHub Repository

**URL:** https://github.com/AndySullivanTR/edgar

**Repository Structure:**
```
edgar/
├── README.md
├── requirements.txt
├── sec-cyber-monitor/
│   ├── sec_cyber_monitor.py
│   ├── cyber_incidents.csv
│   └── seen_filings.json
└── bis-china_monitor/
    ├── bis_china_monitor.py
    ├── bis_china_disclosures.csv
    └── bis_seen_filings.json
```

---

## Troubleshooting

### Monitors Not Running?
```bash
screen -ls
```
If you don't see both cyber and bis, restart them following the instructions above.

### Server Not Responding?
- Check DigitalOcean dashboard: [cloud.digitalocean.com](https://cloud.digitalocean.com/)
- May need to reboot from the console

### Can't SSH In?
- Verify IP address hasn't changed (check DigitalOcean dashboard)
- Verify you're using correct password

### Monitors Crashed?
Check what happened:
```bash
screen -r cyber
# or
screen -r bis
```
If empty/dead, restart the monitor.

---

## Key Linux Commands Reference

### Navigation
- `pwd` - Show current directory
- `ls` - List files
- `cd directory` - Change directory
- `cd ..` - Go up one directory
- `cd /root/edgar` - Go to specific path

### File Viewing
- `cat filename` - Display file contents
- `nano filename` - Edit file (Ctrl+X to exit)
- `less filename` - View large files (q to quit)

### Screen (Background Processes)
- `screen -S name` - Start new named session
- `Ctrl+A` then `D` - Detach (leave running)
- `screen -ls` - List all sessions
- `screen -r name` - Reconnect to session
- `Ctrl+C` - Stop running program

### System
- `apt update` - Update package list
- `apt install package` - Install software
- `pip3 install package` - Install Python package
- `reboot` - Restart server

---

## Monthly Costs

**DigitalOcean VPS:**
- $6.00/month ($0.009/hour)
- Billed to credit card on file
- Can destroy anytime to stop billing

**Check for credits:**
- New accounts often get $200 free credit (covers ~33 months)
- Check: [cloud.digitalocean.com/billing](https://cloud.digitalocean.com/billing)

---

## Security Notes

- Server password is for root access - keep it secure
- Server accessible from any IP address
- Consider setting up SSH keys for passwordless login (more secure)
- Firewall is configured by default

---

## Support Resources

**DigitalOcean Documentation:**
- [docs.digitalocean.com](https://docs.digitalocean.com/)

**SSH Clients:**
- Windows Terminal (built-in)
- PuTTY: [putty.org](https://putty.org/)

**File Transfer:**
- WinSCP: [winscp.net](https://winscp.net/)
- FileZilla: [filezilla-project.org](https://filezilla-project.org/)

---

## Quick Start After Server Reboot

If the server restarts (power outage, maintenance, etc.), the monitors won't auto-start. Restart them:

```bash
ssh root@165.227.107.74

# Start cyber monitor
screen -S cyber
cd /root/edgar/sec-cyber-monitor
python3 sec_cyber_monitor.py
# Ctrl+A then D

# Start BIS monitor
screen -S bis
cd /root/edgar/bis-china_monitor
python3 bis_china_monitor.py
# Ctrl+A then D

# Verify both running
screen -ls
```

---

## Contact Info

**Server Setup:** November 2025  
**Monitors Deployed:** November 5, 2025  
**Setup Assistance:** Claude (Anthropic AI Assistant)

---

*This guide covers the VPS running SEC EDGAR monitoring scripts for Thomson Reuters investigative journalism.*
