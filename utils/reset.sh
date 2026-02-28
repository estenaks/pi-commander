dir="/home/pi/pi-commander/utils"
cd $dir
git pull
$dir/kill_chromium.sh >/dev/null 2>&1
sudo systemctl restart pi-commander-webserver >/dev/null 2>&1
$dir/open_url.sh >/dev/null 2>&1