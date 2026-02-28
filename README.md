# pi-commander
This repo includes a webserver built in python that uses the scryfall api to get specific cards, or random commanders (given color restrictions) and makes the border_crop image available on /face and the config availalbe on /config (both on port 8000)


Additional guide for setting up nginx to serve on port 80 and setting the websever up as a service, auto open browser in fullscreen on boot etc (QOL only)

I'm using this to access http://localhost/face?rotate=1 on a 3.5" display on my rpib3+ and using it as a proxy on the battlefield when playing commander, but you can use it for whatever you want.


---

## 0) Clone the repo

The rest of the files assume this is installed in ~
```bash
cd ~
git clone https://github.com/estenaks/pi-commander.git
cd ~/pi-commander
```

---

## 1) Create the Python venv + install dependencies


```bash
cd ~/pi-commander/webserver

# create venv
python3 -m venv .venv

# activate + install deps
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 2) Run the webserver on port 8000 (local-only)

```bash
python3 ~/pi-commander/webserver/app.py
```

The webserver is now running. It can be reached via browser or by curl:

```bash
curl -I http://127.0.0.1:8000/face
```

You should see an HTTP status 200.

Visiting /face before /config shows a qr code to visit /config (no cards load before config is loaded)

---

# OPTIONAL BUT RECOMMENDED:

Either follow the rest of these steps, or run `/utils/automate.sh` (`chmod +x /home/pi/pi-commander/utils/automate.sh; /home/pi/pi-commander/utils/automate.sh`)

## 3) Create a systemd service for the webserver

Create the unit file:

```bash
sudo nano /etc/systemd/system/pi-commander-webserver.service
```
and paste the contents of `/utils/pi-commander-webserver.service`, then enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable pi-commander-webserver.service
sudo systemctl start pi-commander-webserver.service
```


Check status/logs:

```bash
sudo systemctl status pi-commander-webserver.service -n 50 --no-pager
sudo journalctl -u pi-commander-webserver.service -b --no-pager
```

Verify it’s listening:

```bash
curl -I http://127.0.0.1:8000/face
```

---

## 4) Install nginx and set up


```bash
sudo apt-get update
sudo apt-get install -y nginx
```

Enable nginx at boot:

```bash
sudo systemctl enable nginx
```

Disable and remove the default enabled site to avoid conflicts:

```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo rm -f /etc/nginx/sites-available/default
```

Create a new nginx site config:

```bash
sudo nano /etc/nginx/sites-available/pi-commander
```

Copy and paste contents of `/utils/nginx-site`

Enable the site:

```bash
sudo ln -sf /etc/nginx/sites-available/pi-commander /etc/nginx/sites-enabled/pi-commander
```

Test nginx config and reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

The website is now available over LAN on http://HOSTNAME (HOSTNAME is usually raspberrypi.local) or reachable via ip-adress.
Use /config to select a card, and show it on any device by accessing /face

# Double optional: make pi launch browser on boot to show the selected card

## 5) launch in browser on pi on boot

run `crontab -e` and paste the contents of /utils/cron

## 6) turn off screensaving, mouse pointer etc
I'm tired of writing, run or read /utils/automate.sh or whatever contents in utils I don't understand.

