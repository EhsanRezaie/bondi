# Bondi Admin VPN — Connection Guide (Windows & Ubuntu)

The admin panel **https://admin.bondiapp.ir** is locked to a WireGuard VPN.
You must connect to the VPN first, then open the admin panel.
The public website (`www.bondiapp.ir`) and API (`api.bondiapp.ir`) do **not**
need the VPN.

---

## 1. What you need

| Item | Value |
|------|-------|
| VPN type | WireGuard |
| Server | `87.107.5.88` (UDP port `51820`) |
| VPN subnet | `10.8.0.0/24` |
| Admin URL | `https://admin.bondiapp.ir` |
| Admin host mapping | `admin.bondiapp.ir` → `10.8.0.1` (hosts file) |

Config files (in this folder):

| File | Use on |
|------|--------|
| `admin-windows-pc.conf` | Windows PC (peer `10.8.0.2`) |
| `admin-ubuntu-pc.conf` | Ubuntu PC (peer `10.8.0.3`) |

> These files contain **private keys**. Do not share or commit them.

---

## 2. Ubuntu PC

### 2.1 Install WireGuard

```bash
sudo apt update
sudo apt install -y wireguard
```

### 2.2 Install the config

```bash
sudo install -m 600 ~/Desktop/bondi-wireguard/admin-ubuntu-pc.conf /etc/wireguard/wg0.conf
```

### 2.3 Point the admin domain at the VPN address

The config only tunnels the VPN subnet, so `admin.bondiapp.ir` must resolve to
the VPN server address `10.8.0.1`. Add one line to `/etc/hosts`:

```bash
echo "10.8.0.1 admin.bondiapp.ir" | sudo tee -a /etc/hosts
```

### 2.4 Connect

```bash
sudo wg-quick up wg0
```

Optional — start automatically on boot:

```bash
sudo systemctl enable wg-quick@wg0
```

### 2.5 Verify

```bash
sudo wg show                 # should show "latest handshake" and transfer
ping -c2 10.8.0.1            # reply in a few ms
```

Then open **https://admin.bondiapp.ir** in your browser.

### 2.6 Disconnect / reconnect

```bash
sudo wg-quick down wg0       # turn VPN off
sudo wg-quick up wg0         # turn VPN on
sudo wg show                 # status
```

### 2.7 One-shot installer (optional)

This folder also has `setup-ubuntu.sh`, which does 2.1–2.4 for you:

```bash
sudo bash ~/Desktop/bondi-wireguard/setup-ubuntu.sh
```

---

## 3. Windows PC

### 3.1 Install the WireGuard client

Download and install from: **https://www.wireguard.com/install/**

### 3.2 Import the config

1. Open the WireGuard app.
2. Click **Import tunnel(s) from file**.
3. Select `admin-windows-pc.conf`.

### 3.3 Point the admin domain at the VPN address

1. Open **Notepad as Administrator** (Start → right-click Notepad → *Run as administrator*).
2. Open this file:

   ```
   C:\Windows\System32\drivers\etc\hosts
   ```

3. Add this line at the end and save:

   ```
   10.8.0.1 admin.bondiapp.ir
   ```

   > If saving is blocked, use **File → Save As**, choose the same path and
   > overwrite the existing `hosts` file.

### 3.4 Connect

In the WireGuard app, click **Activate** on the tunnel (status becomes *Active*).

### 3.5 Verify

Open **https://admin.bondiapp.ir** in your browser.

### 3.6 Disconnect

Click **Deactivate** in the WireGuard app.

---

## 4. How to confirm it is working

| Check | Expected |
|-------|----------|
| `sudo wg show` (Ubuntu) | `latest handshake` a few seconds ago |
| `ping 10.8.0.1` | replies (a few ms) |
| Open `https://admin.bondiapp.ir` | admin login page loads |
| Without VPN, open the same URL | **403 Forbidden** (this is correct) |

---

## 5. Troubleshooting

**I get `403 Forbidden` on admin.bondiapp.ir**
- The VPN is not connected — run `sudo wg-quick up wg0` (Ubuntu) or click
  *Activate* (Windows).
- The hosts entry is missing. Confirm `admin.bondiapp.ir` maps to `10.8.0.1`:
  - Ubuntu: `grep admin.bondiapp.ir /etc/hosts`
  - Windows: check the `hosts` file (step 3.3). Then run `ipconfig /flushdns`.

**VPN is connected but admin still does not open**
- Check the hosts entry again — without it the browser reaches the public IP,
  which is intentionally blocked.
- Run `sudo wg show` and confirm there is a recent handshake.

**No handshake / no connection**
- Check internet connectivity.
- Some networks block UDP; try another network / mobile hotspot.
- Confirm the server is up (ask the owner to check `wg show wg0`).

**I closed the VPN but the admin still loads**
- The browser cached the page. Close the tab; a fresh request will be blocked.

**Why do I need the hosts entry?**
- The config tunnels only `10.8.0.0/24` (to avoid a routing loop). The hosts
  entry makes the browser use the VPN address `10.8.0.1` for the admin domain.

---

## 6. Security notes

- Treat the `.conf` files like passwords; never share or commit them.
- If a laptop/phone is lost, the owner can remove that peer from the server and
  issue a new config.
- To revoke a device, the server admin removes the matching `[Peer]` from
  `/etc/wireguard/wg0.conf` and runs `wg syncconf wg0 <(wg-quick strip wg0)`.

---

## 7. Server-side reference (for the owner)

| Setting | Value |
|---------|-------|
| Interface | `wg0` on the VPS |
| Server address | `10.8.0.1/24` |
| Listen port | `51820/udp` |
| Config | `/etc/wireguard/wg0.conf` |
| Client configs | `/root/bondi-wireguard/clients/` |
| Firewall | UFW allows 22, 80, 443, 51820/udp |
| Admin access rule | nginx `allow 10.8.0.0/24; deny all;` |

```bash
# on the server
wg show wg0                 # peers + handshakes
systemctl status wg-quick@wg0
```
