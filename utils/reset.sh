dir="/home/pi/pi-commander/utils"
cd $dir
git pull
$dir/kill_chromium.sh
sudo systemctl restart pi-commander-webserver
$dir/open_url.sh