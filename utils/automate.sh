dir="/home/pi/pi-commander/utils"
chmod +x $dir/add_service.sh
chmod +x $dir/add_nginx.sh
chmod +x $dir/open_url.sh
mkdir -p /home/pi/.config/lxsession/LXDE-pi
cp $dir/autostart /home/pi/.config/lxsession/LXDE-pi/autostart
sudo mkdir -p /etc/lightdm/lightdm.conf.d
cp $dir/50-nocursor.conf /etc/lightdm/lightdm.conf.d/50-nocursor.conf
sudo systemctl restart lightdm
