# Remote Access (Tailscale) — `remote-access` branch

This branch is the **off-site edition**: it runs the same recorder on a PC that
is *not* on your home/office network, and reaches the DVR over **Tailscale**.

Your DVR is at `192.168.1.13`. It can't run Tailscale itself, so one on-site
computer acts as a **subnet router** that bridges your LAN onto the Tailscale
network. After setup, the off-site PC reaches `192.168.1.13` as if it were local
— no port forwarding, no public exposure, and it survives your public IP
changing.

```
[ DVR 192.168.1.13 ] --LAN--> [ on-site PC + Tailscale ] ==Tailscale==> [ off-site PC (this app) ]
                                  (subnet router)            (encrypted)
```

---

## One-time setup

### 1. On-site computer (the subnet router)

Install Tailscale, then advertise your LAN subnet:

```powershell
tailscale up --advertise-routes=192.168.1.0/24
```

> `192.168.1.0/24` covers `192.168.1.1`–`192.168.1.254`, which includes the DVR
> at `.13`. If your network uses a different range, adjust it.

### 2. Approve the route (Tailscale admin console)

Go to **https://login.tailscale.com/admin/machines**, click the on-site machine
→ **Edit route settings** → enable the advertised `192.168.1.0/24` route.
(Routes stay disabled until you approve them here.)

### 3. Off-site PC (this app)

Install Tailscale, sign in to the **same** Tailscale account, and accept routes:

```powershell
tailscale up --accept-routes
```

Verify the DVR is reachable:

```powershell
tailscale status            # confirm both machines are listed/connected
ping 192.168.1.13           # should now respond over Tailscale
Test-NetConnection 192.168.1.13 -Port 554   # RTSP port reachable?
```

---

## Configure & run

`config.ini` on this branch already points at the DVR's LAN address
(`192.168.1.13`) — keep it that way; Tailscale handles the routing. Just set
your username and password (remember `@` in the password becomes `%40`):

```ini
url = rtsp://admin:Security%4012@192.168.1.13:554/Streaming/Channels/101
```

Then install/run exactly like the main version:

```powershell
.\setup.ps1
.\setup.ps1 -Action Status
```

---

## What's different from the main branch

- **Config** points at the DVR over Tailscale (`192.168.1.13`), with notes.
- **Connectivity preflight**: before each recording attempt the app TCP-probes
  the DVR. If it's unreachable it logs a clear hint —
  *"cannot reach DVR … is Tailscale up and the subnet route approved?"* — and
  retries every 15s, instead of flooding the log with ffmpeg errors.

Everything else (10 GB cap, 21-day retention, background task) is identical.

---

## Why not the public IP?

Forwarding the DVR's port to your public IP (`137.115.3.87`) also works, but:

- Your **public IP is dynamic** and will change, breaking the link (you'd need
  Dynamic DNS).
- A DVR exposed to the open internet gets scanned and attacked constantly, and
  RTSP sends credentials in cleartext.

Tailscale avoids both problems. If you ever *must* use the public IP, set up DDNS
on the DVR and point `url` at the DDNS hostname instead of `192.168.1.13`.
