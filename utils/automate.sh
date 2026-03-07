#!/bin/sh
dir="/home/pi/pi-commander/utils"
chmod +x $dir/add_service.sh
chmod +x $dir/add_nginx.sh
chmod +x $dir/open_url.sh
mkdir -p /home/pi/.config/lxsession/LXDE-pi
cp $dir/autostart /home/pi/.config/lxsession/LXDE-pi/autostart
sudo mkdir -p /etc/lightdm/lightdm.conf.d
sudo cp $dir/50-nocursor.conf /etc/lightdm/lightdm.conf.d/50-nocursor.conf
sudo systemctl restart lightdm

$dir/add_nginx.sh
$dir/add_service.sh

read -r -p "Changes will take effect after rebooting. Reboot NOW? [y/N] " ans
case "$ans" in
  y|Y|yes|YES)
    sudo reboot now
    ;;
  *)
    echo "Canceled."
    ;;
esac
