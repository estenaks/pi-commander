dir="/home/pi/pi-commander/utils"
chmod +x $dir/add_service.sh
chmod +x $dir/add_nginx.sh
chmod +x $dir/open_url.sh
mkdir -p /home/pi/.config/lxsession/LXDE-pi
cp $dir/autostart /home/pi/.config/lxsession/LXDE-pi/autostart